"""Multi-Agent Software Development System â€” Orchestrator.

Pipeline:
    Human Requirements
        -> Architect  (Claude)  : writes tasks/plan.md + tasks/todo.md
        -> Coder      (Codex)   : implements every TODO
        -> loop up to max_iterations:
              Tester    (Claude) : produces tasks/bug_report.md
              if NO_ISSUES_FOUND: stop
              BugFixer  (Codex)  : applies fixes
        -> Final summary in tasks/summary.md

Usage:
    python orchestrator.py                              # interactive
    python orchestrator.py -r "build me an X"
    python orchestrator.py --requirements-file spec.md
    python orchestrator.py -r "..." --name my-project --max-iterations 8
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from agents.architect import ArchitectAgent
from agents.base import collect_code_files
from agents.bug_fixer import BugFixerAgent
from agents.coder import CoderAgent
from agents.tester import TesterAgent, count_report_issues
from verifier import VerificationRunner, append_verification_issues

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
RUNS_DIR = ROOT / "runs"


# ---------------------------- pretty output ---------------------------------

def _supports_color() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty() or os.environ.get("FORCE_COLOR") == "1"


_USE_COLOR = _supports_color()


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _USE_COLOR else text


def banner(title: str) -> None:
    bar = "=" * 70
    print()
    print(_c("36", bar))
    print(_c("36;1", title))
    print(_c("36", bar))


def ok(msg: str) -> None:
    print(_c("32", f"[ok]    {msg}"))


def warn(msg: str) -> None:
    print(_c("33", f"[warn]  {msg}"))


def err(msg: str) -> None:
    print(_c("31", f"[error] {msg}"))


def info(msg: str) -> None:
    print(f"        {msg}")


# ---------------------------- persistent state ------------------------------

class PipelineRun:
    """JSON run ledger + resumable project state."""

    def __init__(self, project_dir: Path, name: str, config: dict):
        self.project_dir = project_dir
        self.name = name
        self.state_path = project_dir / "tasks" / "pipeline_state.json"
        self.state = self._load_state()
        self.state.setdefault("run_id", datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
        self.state.setdefault("name", name)
        self.state.setdefault("project_dir", str(project_dir))
        self.state.setdefault("started", datetime.now().isoformat(timespec="seconds"))
        self.state.setdefault("status", "running")
        self.state.setdefault("stages", {})
        self.state.setdefault("iterations", {})
        self.state.setdefault("config", _public_config(config))

        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        self.ledger_path = RUNS_DIR / f"{self.state['run_id']}_{name}.json"
        self.save()

    def _load_state(self) -> dict:
        if self.state_path.exists():
            try:
                return json.loads(self.state_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                backup = self.state_path.with_suffix(".json.bak")
                backup.write_text(self.state_path.read_text(encoding="utf-8"), encoding="utf-8")
        return {}

    def stage_status(self, stage: str) -> str | None:
        data = self.state.get("stages", {}).get(stage)
        return data.get("status") if isinstance(data, dict) else None

    def stage_completed(self, stage: str) -> bool:
        return self.stage_status(stage) == "completed"

    def mark_stage(self, stage: str, status: str, details: dict | None = None) -> None:
        self.state["stages"][stage] = {
            "status": status,
            "updated": datetime.now().isoformat(timespec="seconds"),
            "details": details or {},
        }
        self.save()

    def mark_iteration(self, iteration: int, details: dict) -> None:
        self.state["iterations"][str(iteration)] = {
            "updated": datetime.now().isoformat(timespec="seconds"),
            **details,
        }
        self.save()

    def finish(self, status: str, details: dict) -> None:
        self.state["status"] = status
        self.state["finished"] = datetime.now().isoformat(timespec="seconds")
        self.state["result"] = details
        self.save()

    def save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(self.state, indent=2, sort_keys=True)
        self.state_path.write_text(text + "\n", encoding="utf-8")
        self.ledger_path.write_text(text + "\n", encoding="utf-8")


def _public_config(config: dict) -> dict:
    """Store non-secret config values in the run ledger."""
    redacted = dict(config)
    for key in list(redacted):
        if "key" in key.lower() or "token" in key.lower() or "secret" in key.lower():
            redacted[key] = "***"
    return redacted


def run_stage(
    run: PipelineRun,
    stage: str,
    action: Callable[[], dict],
    retries: int,
) -> dict:
    """Run a stage with automatic retry and structured state updates."""
    last_error: str | None = None
    for attempt in range(1, retries + 2):
        run.mark_stage(stage, "running", {"attempt": attempt})
        try:
            result = action()
            run.mark_stage(stage, "completed", {"attempt": attempt, "result": _jsonable(result)})
            return result
        except Exception as exc:
            last_error = f"{exc.__class__.__name__}: {exc}"
            run.mark_stage(stage, "retrying" if attempt <= retries else "failed", {
                "attempt": attempt,
                "error": last_error,
            })
            if attempt <= retries:
                warn(f"{stage} failed on attempt {attempt}; retrying automatically.")
                continue
            raise RuntimeError(f"{stage} failed after {attempt} attempt(s): {last_error}") from exc
    raise RuntimeError(f"{stage} failed: {last_error}")


def _jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value


# ---------------------------- accuracy gates --------------------------------

def artifact_nonempty(project_dir: Path, relative_path: str) -> bool:
    path = project_dir / relative_path
    try:
        return path.is_file() and path.read_text(encoding="utf-8", errors="replace").strip() != ""
    except OSError:
        return False


def hash_requirements_text(requirements: str) -> str:
    return hashlib.sha256(requirements.encode("utf-8")).hexdigest()


def compute_review_fingerprint(project_dir: Path, iteration: int) -> dict:
    """Fingerprint files that define the code/review evidence for an iteration."""
    project_dir = project_dir.resolve()
    entries: list[dict] = []
    for path in _review_fingerprint_paths(project_dir, iteration):
        try:
            data = path.read_bytes()
        except OSError:
            continue
        rel_path = path.relative_to(project_dir).as_posix()
        entries.append(
            {
                "path": rel_path,
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )

    entries.sort(key=lambda item: item["path"])
    digest = hashlib.sha256()
    for entry in entries:
        digest.update(entry["path"].encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(entry["size"]).encode("ascii"))
        digest.update(b"\0")
        digest.update(entry["sha256"].encode("ascii"))
        digest.update(b"\0")
    return {
        "algorithm": "sha256-v1",
        "digest": digest.hexdigest(),
        "files": entries,
    }


def _review_fingerprint_paths(project_dir: Path, iteration: int) -> list[Path]:
    seen: set[Path] = set()
    paths: list[Path] = []

    for path in collect_code_files(project_dir, max_bytes=None):
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            paths.append(resolved)

    for relative_path in [
        "tasks/requirements.md",
        "tasks/plan.md",
        "tasks/todo.md",
        "tasks/coder_report.md",
        "tasks/bug_report.md",
        f"tasks/bug_report_iter{iteration}.md",
        f"tasks/verification_iter{iteration}.md",
    ]:
        path = (project_dir / relative_path).resolve()
        if path.is_file() and path not in seen:
            seen.add(path)
            paths.append(path)

    return paths


def invalidate_stale_state(
    state: dict,
    current_requirements_hash: str,
    *,
    has_reusable_artifacts: bool = False,
) -> bool:
    previous_hash = state.get("requirements_hash")
    state["requirements_hash"] = current_requirements_hash
    if previous_hash and previous_hash != current_requirements_hash:
        _reset_stale_state(state)
        return True
    if not previous_hash and (_state_has_resume_markers(state) or has_reusable_artifacts):
        _reset_stale_state(state)
        return True
    return False


def _reset_stale_state(state: dict) -> None:
    state["stages"] = {}
    state["iterations"] = {}
    state["status"] = "running"
    state["stale_resume_detected"] = True


def _state_has_resume_markers(state: dict) -> bool:
    stages = state.get("stages")
    iterations = state.get("iterations")
    return bool(stages or iterations)


def reusable_resume_artifacts_exist(project_dir: Path) -> bool:
    return any(
        artifact_nonempty(project_dir, relative_path)
        for relative_path in [
            "tasks/plan.md",
            "tasks/todo.md",
            "tasks/coder_report.md",
            "tasks/bug_report.md",
        ]
    )


def count_bug_report_issues(project_dir: Path) -> int:
    path = project_dir / "tasks" / "bug_report.md"
    if not path.exists():
        return 1
    report = path.read_text(encoding="utf-8", errors="replace").strip()
    if report == "NO_ISSUES_FOUND":
        return 0
    count = count_report_issues(report)
    return count if count else 1


def bug_report_is_clean(project_dir: Path) -> bool:
    path = project_dir / "tasks" / "bug_report.md"
    return path.exists() and path.read_text(encoding="utf-8", errors="replace").strip() == "NO_ISSUES_FOUND"


def verification_has_passed_command(verification_result: dict) -> bool:
    for result in verification_result.get("results", []):
        if (
            result.get("status") == "passed"
            and result.get("returncode") == 0
            and not result.get("timed_out")
        ):
            return True
    return False


def final_iteration_clean(
    project_dir: Path,
    tester_result: dict | None,
    verification_result: dict | None,
) -> tuple[bool, list[str]]:
    blockers: list[str] = []
    if not artifact_nonempty(project_dir, "tasks/coder_report.md"):
        blockers.append("missing tasks/coder_report.md")
    if not tester_result or not tester_result.get("clean"):
        blockers.append("tester report is not clean")
    if not bug_report_is_clean(project_dir):
        blockers.append("tasks/bug_report.md is missing or not exactly NO_ISSUES_FOUND")
    if not verification_result:
        blockers.append("verifier result is missing")
    else:
        verifier_status = verification_result.get("status", "unknown")
        if verifier_status != "passed":
            blockers.append(f"verifier status is {verifier_status}; expected passed")
        elif not verification_result.get("clean"):
            blockers.append("verifier result is not clean")
        elif not verification_has_passed_command(verification_result):
            blockers.append("verifier result has no passed command evidence")
    return not blockers, blockers


def clean_iteration_artifacts_consistent(
    project_dir: Path,
    iteration_state: dict,
    iteration: int,
) -> tuple[bool, list[str]]:
    tester_result = iteration_state.get("tester") if isinstance(iteration_state, dict) else None
    verification_result = iteration_state.get("verification") if isinstance(iteration_state, dict) else None
    clean, blockers = final_iteration_clean(project_dir, tester_result, verification_result)
    if not artifact_nonempty(project_dir, f"tasks/bug_report_iter{iteration}.md"):
        blockers.append(f"missing tasks/bug_report_iter{iteration}.md")
    if verification_result and verification_result.get("status") != "disabled":
        report_path = verification_result.get("report_path") or f"tasks/verification_iter{iteration}.md"
        report = Path(report_path)
        if not report.is_absolute():
            report = project_dir / report
        if not report.is_file() or not report.read_text(encoding="utf-8", errors="replace").strip():
            blockers.append(f"missing verifier report for iteration {iteration}")
    expected_fingerprint = iteration_state.get("review_fingerprint") if isinstance(iteration_state, dict) else None
    if not expected_fingerprint:
        blockers.append("missing review fingerprint for clean iteration")
    else:
        current_fingerprint = compute_review_fingerprint(project_dir, iteration)
        if expected_fingerprint.get("digest") != current_fingerprint.get("digest"):
            blockers.append("review fingerprint changed since clean iteration")
    return not blockers, blockers


def status_for_zero_iterations(max_iterations: int) -> str | None:
    return "not_reviewed" if max_iterations <= 0 else None


def latest_iteration_details(run_state: dict) -> dict:
    iterations = run_state.get("iterations", {})
    if not iterations:
        return {}
    latest_key = max(iterations, key=lambda value: int(value) if str(value).isdigit() else -1)
    return iterations.get(latest_key, {})


def build_summary_model(
    *,
    name: str,
    started: datetime,
    finished: datetime,
    status: str,
    iterations_done: int,
    final_bug_count: int,
    coder_backend: str,
    ledger_path: Path,
    run_state: dict,
) -> dict:
    latest = latest_iteration_details(run_state)
    verification = latest.get("verification", {}) if isinstance(latest, dict) else {}
    tester = latest.get("tester", {}) if isinstance(latest, dict) else {}
    return {
        "name": name,
        "started": started.isoformat(timespec="seconds"),
        "finished": finished.isoformat(timespec="seconds"),
        "status": status,
        "iterations": iterations_done,
        "final_bug_count": final_bug_count,
        "coder_backend": coder_backend,
        "ledger_path": str(ledger_path),
        "latest_tester_status": "clean" if tester.get("clean") else ("issues_found" if tester else "missing"),
        "latest_verifier_status": verification.get("status", "missing"),
        "latest_verifier_clean": verification.get("clean"),
        "latest_verifier_report": verification.get("report_path", ""),
        "verification_commands": [item.get("command", "") for item in verification.get("commands", [])],
        "verification_results": verification.get("results", []),
        "environment_problems": [
            result
            for result in verification.get("results", [])
            if result.get("missing_executable") or result.get("status") in {"missing_executable", "timeout"}
        ],
    }


# ---------------------------- environment check -----------------------------

def check_environment(config: dict) -> bool:
    problems: list[str] = []
    claude_backend = str(config.get("claude_backend", "claude_code"))
    if claude_backend == "anthropic_api":
        if not os.environ.get("ANTHROPIC_API_KEY"):
            problems.append(
                "ANTHROPIC_API_KEY is not set "
                "(Architect and Tester need it when claude_backend='anthropic_api')."
            )
    else:
        claude_command = str(config.get("claude_command", "claude"))
        if shutil.which(claude_command) is None:
            problems.append(
                f"Claude Code CLI '{claude_command}' was not found (Architect and Tester use the Claude Code subscription)."
            )

    backend = config.get("coder_backend", "auto")
    if backend != "codex" and not os.environ.get("OPENAI_API_KEY"):
        # auto/openai both need the key when codex CLI isn't being used
        codex_present = shutil.which(config.get("codex_command", "codex")) is not None
        if backend == "openai" or not codex_present:
            problems.append(
                "OPENAI_API_KEY is not set (Coder/BugFixer need it when not using the codex CLI)."
            )

    if problems:
        err("Environment problems:")
        for p in problems:
            print(f"        - {p}")
        if claude_backend == "anthropic_api" or any("OPENAI_API_KEY" in p for p in problems):
            info(
                "Set API keys in PowerShell with setx only if you intentionally "
                "choose API-key backends."
            )
        info(
            "For subscription billing, log in with `claude` and `codex` in PowerShell, "
            "then open a NEW terminal and re-run."
        )
        return False
    return True


# ---------------------------- requirements I/O ------------------------------

def gather_requirements(args: argparse.Namespace) -> str:
    if args.requirements:
        return args.requirements.strip()
    if args.requirements_file:
        return Path(args.requirements_file).read_text(encoding="utf-8").strip()
    print()
    print(_c("1", "Enter project requirements."))
    print("End with a line containing only 'END' (no quotes), or press Ctrl-Z then Enter on Windows.")
    lines: list[str] = []
    try:
        while True:
            line = input()
            if line.strip() == "END":
                break
            lines.append(line)
    except EOFError:
        pass
    return "\n".join(lines).strip()


# ---------------------------- pipeline --------------------------------------

def load_config() -> dict:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    config.setdefault("agent_retries", 1)
    config.setdefault("execution_profile", "autonomous")
    config.setdefault("verification", {"enabled": True, "commands": [], "timeout_seconds": 300})

    profiles = config.get("codex_profiles", {})
    profile_name = config.get("execution_profile")
    if profile_name in profiles:
        config["codex_args"] = profiles[profile_name]

    return config


def create_project_dir(name: str, config: dict) -> Path:
    workspace = ROOT / config.get("workspace_root", "workspace")
    workspace.mkdir(parents=True, exist_ok=True)
    project_dir = workspace / name
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "tasks").mkdir(parents=True, exist_ok=True)
    (project_dir / "logs").mkdir(parents=True, exist_ok=True)
    return project_dir


def use_existing_project_dir(path: str) -> Path:
    project_dir = Path(path).expanduser().resolve()
    if not project_dir.exists():
        raise FileNotFoundError(f"Resume path does not exist: {project_dir}")
    (project_dir / "tasks").mkdir(parents=True, exist_ok=True)
    (project_dir / "logs").mkdir(parents=True, exist_ok=True)
    return project_dir


def write_summary(project_dir: Path, summary: dict) -> None:
    verification_results = summary.get("verification_results", [])
    command_lines = summary.get("verification_commands", [])
    lines = [
        "# Pipeline Summary",
        "",
        f"- **Project**: {summary['name']}",
        f"- **Started**: {summary['started']}",
        f"- **Finished**: {summary['finished']}",
        f"- **Status**: {summary['status']}",
        f"- **Iterations**: {summary['iterations']}",
        f"- **Final bug count**: {summary['final_bug_count']}",
        f"- **Coder backend**: {summary.get('coder_backend', '?')}",
        f"- **Latest tester status**: {summary.get('latest_tester_status', 'missing')}",
        f"- **Latest verifier status**: {summary.get('latest_verifier_status', 'missing')}",
        f"- **Latest verifier clean**: {summary.get('latest_verifier_clean', 'missing')}",
        f"- **Latest verifier report**: `{summary.get('latest_verifier_report') or 'missing'}`",
        f"- **Run ledger**: `{summary.get('ledger_path', '?')}`",
        "",
        "## Verification",
        "",
        f"- **Commands detected/run**: {len(command_lines)}",
        f"- **Environment problems**: {len(summary.get('environment_problems', []))}",
        "",
    ]
    if command_lines:
        lines.extend(f"- `{command}`" for command in command_lines)
        lines.append("")
    if verification_results:
        lines.extend(["## Command Results", ""])
        for result in verification_results:
            lines.append(
                f"- `{result.get('command', '?')}` -> {result.get('status', '?')} "
                f"(rc={result.get('returncode', '?')}, timeout={result.get('timed_out', '?')})"
            )
        lines.append("")
    gate_status = "passed" if summary["status"] == "clean" else "needs review"
    lines.extend(
        [
            "## Final QA Gate",
            "",
            f"- **Security & credential audit**: {gate_status}",
            f"- **Performance / latency**: {gate_status}",
            f"- **Logging + monitoring hooks**: {gate_status}",
            f"- **Documentation updated**: {gate_status}",
            f"- **Staff engineer bar**: {gate_status}",
            "",
            "## Artifacts",
            "",
            "- `tasks/requirements.md` - the input requirements",
            "- `tasks/plan.md` - architect's plan",
            "- `tasks/todo.md` - architect's TODO list",
            "- `tasks/coder_report.md` - coder evidence",
            "- `tasks/bug_report.md` - final tester/verifier report",
            "- `tasks/bug_report_iterN.md` - per-iteration archive",
            "- `tasks/verification_iterN.md` - deterministic command verification",
            "- `tasks/pipeline_state.json` - resumable run state",
            "- `logs/` - per-agent execution logs",
        ]
    )
    (project_dir / "tasks" / "summary.md").write_text("\n".join(lines), encoding="utf-8")


def run_pipeline(
    requirements: str,
    name: str,
    max_iterations: int,
    config: dict,
    resume_dir: Path | None = None,
) -> int:
    started = datetime.now()
    project_dir = resume_dir or create_project_dir(name, config)
    info(f"Workspace: {project_dir}")

    requirements_path = project_dir / "tasks" / "requirements.md"
    if not requirements_path.exists() or requirements:
        requirements_path.write_text(requirements, encoding="utf-8")

    architect = ArchitectAgent(config)
    coder = CoderAgent(config)
    tester = TesterAgent(config)
    bug_fixer = BugFixerAgent(config)
    verifier = VerificationRunner(config)
    run = PipelineRun(project_dir, name, config)
    current_requirements = requirements_path.read_text(encoding="utf-8", errors="replace")
    stale_resume = invalidate_stale_state(
        run.state,
        hash_requirements_text(current_requirements),
        has_reusable_artifacts=reusable_resume_artifacts_exist(project_dir),
    )
    if stale_resume:
        warn("Requirements changed since the saved state; invalidating resumed stage/iteration status.")
        run.save()

    iterations_done = 0
    final_bug_count = 0
    status = "in_progress"
    retries = int(config.get("agent_retries", 1))

    try:
        banner("Stage 1 â€” Architect (Claude): plan + todo")
        if run.stage_completed("architect") and artifact_nonempty(project_dir, "tasks/plan.md") and artifact_nonempty(project_dir, "tasks/todo.md"):
            ok("Architect already completed; resuming from saved state.")
        elif not stale_resume and artifact_nonempty(project_dir, "tasks/plan.md") and artifact_nonempty(project_dir, "tasks/todo.md"):
            run.mark_stage("architect", "completed", {"resumed_from_artifacts": True})
            ok("Plan + TODO already exist; resuming after Architect.")
        else:
            run_stage(run, "architect", lambda: architect.run(requirements, project_dir), retries)
        ok("Plan + TODO written to tasks/plan.md and tasks/todo.md")

        banner("Stage 2 â€” Coder: initial implementation")
        if run.stage_completed("coder") and artifact_nonempty(project_dir, "tasks/coder_report.md"):
            coder_result = {"backend": config.get("coder_backend", "auto"), "resumed": True}
            ok("Coder already completed; resuming from saved state.")
        else:
            coder_result = run_stage(run, "coder", lambda: coder.run(project_dir), retries)
        ok(f"Coder finished (backend={coder_result.get('backend')})")

        zero_iteration_status = status_for_zero_iterations(max_iterations)
        if zero_iteration_status:
            status = zero_iteration_status
            final_bug_count = 1
            warn("max_iterations must be at least 1 for a clean run; no tester/verifier review was performed.")

        for iteration in range(1, max_iterations + 1):
            iterations_done = iteration
            iteration_state = run.state.get("iterations", {}).get(str(iteration), {})
            if iteration_state.get("status") == "clean":
                resume_clean, resume_blockers = clean_iteration_artifacts_consistent(
                    project_dir,
                    iteration_state,
                    iteration,
                )
                if resume_clean:
                    final_bug_count = int(iteration_state.get("bugs_count", 0))
                    status = "clean"
                    ok(f"Iteration {iteration} already clean; resuming complete state.")
                    break
                warn(
                    "Saved clean iteration is missing current evidence; re-running review. "
                    f"Blockers: {', '.join(resume_blockers)}"
                )

            banner(f"Stage 3 â€” Tester (Claude): review #{iteration}")
            test_result = run_stage(
                run,
                f"tester-{iteration}",
                lambda iteration=iteration: tester.run(project_dir, iteration),
                retries,
            )

            banner(f"Stage 3b - Verifier: command checks #{iteration}")
            verification_result = verifier.run(project_dir, iteration)
            append_verification_issues(project_dir, iteration, verification_result)

            clean, clean_blockers = final_iteration_clean(project_dir, test_result, verification_result)
            final_bug_count = 0 if clean else max(count_bug_report_issues(project_dir), len(clean_blockers))
            run.mark_iteration(
                iteration,
                {
                    "status": "clean" if clean else "issues_found",
                    "bugs_count": final_bug_count,
                    "tester": _jsonable(test_result),
                    "verification": _jsonable(verification_result),
                    "review_fingerprint": compute_review_fingerprint(project_dir, iteration),
                    "clean_blockers": clean_blockers,
                },
            )

            if clean:
                ok(f"Clean review on iteration {iteration} â€” pipeline complete.")
                status = "clean"
                break

            warn(f"Found {final_bug_count} issue(s) on iteration {iteration}.")

            if iteration == max_iterations:
                warn(f"Reached max_iterations={max_iterations}; stopping with open issues.")
                status = "max_iterations_reached"
                break

            banner(f"Stage 4 â€” Bug Fixer: fix pass #{iteration}")
            fix_result = run_stage(
                run,
                f"bug-fixer-{iteration}",
                lambda iteration=iteration: bug_fixer.run(project_dir, iteration),
                retries,
            )
            ok(f"Bug Fixer finished (backend={fix_result.get('backend')})")

        if status == "in_progress":
            status = "max_iterations_reached"
            final_bug_count = max(final_bug_count, 1)

    except Exception as exc:
        err(f"Pipeline aborted: {exc}")
        traceback.print_exc()
        status = f"error: {exc.__class__.__name__}"

    finished = datetime.now()
    result_details = {
        "iterations": iterations_done,
        "final_bug_count": final_bug_count,
        "ledger_path": str(run.ledger_path),
    }
    run.finish(status, result_details)
    write_summary(
        project_dir,
        build_summary_model(
            name=name,
            started=started,
            finished=finished,
            status=status,
            iterations_done=iterations_done,
            final_bug_count=final_bug_count,
            coder_backend=config.get("coder_backend", "auto"),
            ledger_path=run.ledger_path,
            run_state=run.state,
        ),
    )

    banner("Pipeline complete")
    info(f"Project:    {project_dir}")
    info(f"Status:     {status}")
    info(f"Iterations: {iterations_done}")
    info(f"Open bugs:  {final_bug_count}")
    info(f"Summary:    {project_dir / 'tasks' / 'summary.md'}")
    info(f"Ledger:     {run.ledger_path}")
    return 0 if status == "clean" else 1


# ---------------------------- entry point -----------------------------------

def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Multi-Agent Software Development System")
    p.add_argument("-r", "--requirements", help="Project requirements as a string.")
    p.add_argument("--requirements-file", help="Read requirements from a file path.")
    p.add_argument("-n", "--name", help="Project folder name under workspace/. Default: timestamp.")
    p.add_argument("--max-iterations", type=int, help="Override max test/fix iterations.")
    p.add_argument("--resume", help="Resume an existing workspace project directory.")
    p.add_argument("--profile", help="Execution profile from config.codex_profiles, e.g. autonomous or ci.")
    p.add_argument("--no-verify", action="store_true", help="Disable deterministic verifier commands for this run.")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    config = load_config()
    if args.profile:
        config["execution_profile"] = args.profile
        profile_args = config.get("codex_profiles", {}).get(args.profile)
        if profile_args:
            config["codex_args"] = profile_args
    if args.no_verify:
        config.setdefault("verification", {})["enabled"] = False

    if not check_environment(config):
        return 2

    resume_dir = use_existing_project_dir(args.resume) if args.resume else None
    if resume_dir and not args.requirements and not args.requirements_file:
        req_path = resume_dir / "tasks" / "requirements.md"
        requirements = req_path.read_text(encoding="utf-8").strip() if req_path.exists() else ""
    else:
        requirements = gather_requirements(args)
    if not requirements:
        err("No requirements provided. Aborting.")
        return 2

    name = args.name or (resume_dir.name if resume_dir else f"project_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    max_iterations = args.max_iterations if args.max_iterations is not None else int(config.get("max_iterations", 5))

    banner("Multi-Agent Software Development System")
    info(f"Project name : {name}")
    info(f"Max iterations: {max_iterations}")
    info(f"Coder backend: {config.get('coder_backend', 'auto')}")
    info(f"Claude model : {config.get('claude_model')}")
    info(f"OpenAI model : {config.get('openai_model')}")

    return run_pipeline(requirements, name, max_iterations, config, resume_dir=resume_dir)


if __name__ == "__main__":
    raise SystemExit(main())
