"""Deterministic verification commands for pipeline outputs."""

from __future__ import annotations

import argparse
import configparser
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

NO_ISSUES = "NO_ISSUES_FOUND"
SKIP_DIRS = {
    ".git",
    ".mypy_cache",
    ".next",
    ".pytest_cache",
    ".ruff_cache",
    ".turbo",
    ".venv",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "logs",
    "node_modules",
    "runs",
    "tasks",
    "target",
    "venv",
    "workspace",
}
PLACEHOLDER_NPM_TEST_RE = re.compile(
    r"echo\s+['\"]?Error:\s+no\s+test\s+specified['\"]?\s*&&\s*exit\s+1",
    re.IGNORECASE,
)
PYTHON_ENV_NOTE = "Python commands run with PYTHONHOME and UV_INTERNAL__PYTHONHOME cleared; PYTHONNOUSERSITE=1."


@dataclass
class PythonCandidate:
    display: str
    args: list[str]


@dataclass
class VerificationCommand:
    name: str
    command: str
    args: list[str] | None = None
    shell: bool = False
    cwd: str | None = None
    env_overrides: dict[str, str | None] = field(default_factory=dict)
    environment_notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class VerificationRunner:
    """Detect and run project verification commands."""

    def __init__(self, config: dict):
        verification = config.get("verification", {})
        self.enabled = verification.get("enabled", True)
        self.timeout_seconds = int(verification.get("timeout_seconds", 300))
        self.configured_commands = verification.get("commands", [])
        self.require_commands_for_clean = verification.get("require_commands_for_clean", True)
        configured_python = config.get("python_command")
        self.configured_python = _split_command(configured_python) if configured_python else None

    def run(self, project_dir: Path, iteration: int, label: str = "review") -> dict:
        project_dir = project_dir.resolve()
        if not self.enabled:
            return self._write_report(project_dir, iteration, label, [], [], "disabled", True)

        commands = self._commands_for(project_dir)
        if not commands:
            clean = not self.require_commands_for_clean
            return self._write_report(project_dir, iteration, label, [], [], "no_commands", clean)

        results = [self._run_command(project_dir, item) for item in commands]
        clean = all(result["returncode"] == 0 and not result["timed_out"] for result in results)
        return self._write_report(project_dir, iteration, label, commands, results, "passed" if clean else "failed", clean)

    def _commands_for(self, project_dir: Path) -> list[VerificationCommand]:
        if self.configured_commands:
            return self._configured_commands()

        commands: list[VerificationCommand] = []
        commands.extend(self._external_project_commands(project_dir))
        commands.extend(self._node_commands(project_dir))
        commands.extend(self._python_commands(project_dir))

        if (project_dir / "go.mod").exists():
            commands.append(_argv_command("go test", ["go", "test", "./..."]))

        if (project_dir / "Cargo.toml").exists():
            commands.append(_argv_command("cargo test", ["cargo", "test"]))

        if (project_dir / "pom.xml").exists():
            commands.append(_argv_command("maven test", ["mvn", "test"]))

        if (project_dir / "build.gradle").exists() or (project_dir / "build.gradle.kts").exists():
            commands.append(_argv_command("gradle test", ["gradle", "test"]))

        return commands

    def _external_project_commands(self, project_dir: Path) -> list[VerificationCommand]:
        requirements_path = project_dir / "tasks" / "requirements.md"
        if not requirements_path.exists():
            return []
        text = requirements_path.read_text(encoding="utf-8", errors="replace")
        roots = _extract_external_project_roots(text)
        commands: list[VerificationCommand] = []
        for root in roots:
            if root.name.lower() == "futures-bot-v2":
                commands.extend(self._futures_bot_v2_commands(root))
        return commands

    def _futures_bot_v2_commands(self, root: Path) -> list[VerificationCommand]:
        python = self._resolve_python(root)
        if not python:
            return [
                VerificationCommand(
                    name="external futures-bot-v2 python interpreter",
                    command="python",
                    args=["python", "-c", "import sys"],
                    cwd=str(root),
                    environment_notes=["No valid Python 3.10+ interpreter was found."],
                )
            ]
        env_overrides = _python_env_overrides()
        notes = [
            PYTHON_ENV_NOTE,
            "External futures-bot-v2 verifier runs the broad safety/readiness suite from the target repo, not only workspace wrapper tests.",
        ]
        compile_targets = [
            item
            for item in [
                "app",
                "broker",
                "execution",
                "intelligence",
                "persistence",
                "runtime",
                "watchlist",
                "zone_engine",
                "tests",
            ]
            if (root / item).exists()
        ]
        command_specs: list[tuple[str, list[str], str]] = [
            (
                "external futures-bot-v2 pytest",
                [*python.args, "-m", "pytest", "-q"],
                f"{python.display} -m pytest -q",
            ),
            (
                "external futures-bot-v2 guardrails",
                [
                    *python.args,
                    "-m",
                    "pytest",
                    "tests/test_scope_guardrails.py",
                    "tests/test_phase7_1_scope_guardrails.py",
                    "tests/test_phase7_scope_guardrails.py",
                    "-q",
                ],
                (
                    f"{python.display} -m pytest tests/test_scope_guardrails.py "
                    "tests/test_phase7_1_scope_guardrails.py "
                    "tests/test_phase7_scope_guardrails.py -q"
                ),
            ),
            (
                "external futures-bot-v2 sim smoke",
                [
                    *python.args,
                    "-m",
                    "app.sim_smoke_cli",
                    "--config",
                    "configs/sim_bridge.example.toml",
                    "--fake-readiness",
                    "--allow-repo-runtime",
                ],
                (
                    f"{python.display} -m app.sim_smoke_cli --config "
                    "configs/sim_bridge.example.toml --fake-readiness --allow-repo-runtime"
                ),
            ),
            (
                "external futures-bot-v2 sim readiness",
                [
                    *python.args,
                    "-m",
                    "app.sim_readiness_suite",
                    "--config",
                    "configs/sim_bridge.example.toml",
                    "--fake-readiness",
                ],
                (
                    f"{python.display} -m app.sim_readiness_suite --config "
                    "configs/sim_bridge.example.toml --fake-readiness"
                ),
            ),
            (
                "external futures-bot-v2 healthcheck",
                [
                    *python.args,
                    "-m",
                    "app.healthcheck_cli",
                    "--config",
                    "configs/shadow.example.toml",
                    "--json",
                ],
                f"{python.display} -m app.healthcheck_cli --config configs/shadow.example.toml --json",
            ),
        ]
        if compile_targets:
            command_specs.append(
                (
                    "external futures-bot-v2 compileall",
                    [*python.args, "-m", "compileall", "-q", *compile_targets],
                    (
                        f"{python.display} -m compileall -q "
                        f"{' '.join(_quote(part) for part in compile_targets)}"
                    ),
                )
            )
        return [
            _argv_command(
                name,
                args,
                display=display,
                cwd=str(root),
                env_overrides=env_overrides,
                environment_notes=notes,
            )
            for name, args, display in command_specs
        ]

    def _configured_commands(self) -> list[VerificationCommand]:
        commands: list[VerificationCommand] = []
        for index, item in enumerate(self.configured_commands, start=1):
            python_env = False
            if isinstance(item, str):
                command = item
                name = item
            else:
                command = str(item.get("command", "")).strip()
                name = str(item.get("name") or command or f"command-{index}")
                python_env = bool(item.get("python_env", False))
            if command:
                if python_env or _command_invokes_python(command):
                    commands.append(
                        VerificationCommand(
                            name=name,
                            command=command,
                            shell=True,
                            env_overrides=_python_env_overrides(),
                            environment_notes=[PYTHON_ENV_NOTE],
                        )
                    )
                else:
                    commands.append(VerificationCommand(name=name, command=command, shell=True))
        return commands

    def _python_commands(self, project_dir: Path) -> list[VerificationCommand]:
        has_tests = (project_dir / "tests").is_dir()
        has_pytest_config = _has_pytest_config(project_dir)
        compile_targets = _python_compile_targets(project_dir)
        if not has_tests and not has_pytest_config and not compile_targets:
            return []

        python = self._resolve_python(project_dir)
        if not python:
            return [
                VerificationCommand(
                    name="python interpreter",
                    command="python",
                    args=["python", "-c", "import sys"],
                    environment_notes=["No valid Python 3.10+ interpreter was found."],
                )
            ]

        commands: list[VerificationCommand] = []
        env_overrides = _python_env_overrides()
        notes = [PYTHON_ENV_NOTE]

        if has_pytest_config:
            commands.append(
                _argv_command(
                    "python pytest",
                    [*python.args, "-m", "pytest"],
                    display=f"{python.display} -m pytest",
                    env_overrides=env_overrides,
                    environment_notes=notes,
                )
            )
        elif has_tests:
            commands.append(
                _argv_command(
                    "python unittest",
                    [*python.args, "-m", "unittest", "discover", "-s", "tests", "-v"],
                    display=f"{python.display} -m unittest discover -s tests -v",
                    env_overrides=env_overrides,
                    environment_notes=notes,
                )
            )

        if compile_targets:
            commands.append(
                _argv_command(
                    "python compileall",
                    [*python.args, "-m", "compileall", "-q", *compile_targets],
                    display=f"{python.display} -m compileall -q {' '.join(_quote(part) for part in compile_targets)}",
                    env_overrides=env_overrides,
                    environment_notes=notes,
                )
            )

        return commands

    def _node_commands(self, project_dir: Path) -> list[VerificationCommand]:
        package_json = project_dir / "package.json"
        if not package_json.exists():
            return []

        try:
            package = json.loads(package_json.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return [
                _argv_command(
                    "package.json parse",
                    ["node", "-e", "JSON.parse(require('fs').readFileSync('package.json', 'utf8'))"],
                )
            ]

        scripts = package.get("scripts", {})
        if not isinstance(scripts, dict):
            return []

        manager = _select_node_package_manager(project_dir)
        commands: list[VerificationCommand] = []
        for script_name in ["test", "build", "lint", "typecheck"]:
            script = scripts.get(script_name)
            if not isinstance(script, str) or not script.strip():
                continue
            if script_name == "test" and _is_placeholder_npm_test(script):
                continue
            commands.append(_node_script_command(manager, script_name))
        return commands

    def _resolve_python(self, project_dir: Path) -> PythonCandidate | None:
        candidates: list[PythonCandidate] = []

        for relative in [Path(".venv/Scripts/python.exe"), Path(".venv/bin/python")]:
            path = project_dir / relative
            if path.exists():
                candidates.append(PythonCandidate(str(path), [str(path)]))

        if sys.executable:
            candidates.append(PythonCandidate(sys.executable, [sys.executable]))

        if self.configured_python:
            candidates.append(PythonCandidate(" ".join(self.configured_python), self.configured_python))

        candidates.extend(
            [
                PythonCandidate("py -3.12", ["py", "-3.12"]),
                PythonCandidate("py -3.11", ["py", "-3.11"]),
                PythonCandidate("py -3", ["py", "-3"]),
                PythonCandidate("python", ["python"]),
                PythonCandidate("python3", ["python3"]),
            ]
        )

        seen: set[tuple[str, ...]] = set()
        for candidate in candidates:
            key = tuple(candidate.args)
            if key in seen:
                continue
            seen.add(key)
            executable = candidate.args[0]
            if not Path(executable).exists() and shutil.which(executable) is None:
                continue
            if self._validate_python(candidate):
                return candidate
        return None

    def _validate_python(self, candidate: PythonCandidate) -> bool:
        try:
            result = subprocess.run(
                [
                    *candidate.args,
                    "-c",
                    "import re, sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=_sanitized_python_env(),
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return result.returncode == 0

    def _run_command(self, project_dir: Path, item: VerificationCommand) -> dict:
        env = os.environ.copy()
        for key, value in item.env_overrides.items():
            if value is None:
                env.pop(key, None)
            else:
                env[key] = value

        try:
            result = subprocess.run(
                item.args if item.args is not None else item.command,
                cwd=str(Path(item.cwd) if item.cwd else project_dir),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                shell=item.shell,
                env=env,
                timeout=self.timeout_seconds,
            )
            return {
                "name": item.name,
                "command": item.command,
                "cwd": str(Path(item.cwd) if item.cwd else project_dir),
                "returncode": result.returncode,
                "stdout": _tail(result.stdout),
                "stderr": _tail(result.stderr),
                "timed_out": False,
                "missing_executable": False,
                "status": "passed" if result.returncode == 0 else "failed",
                "environment_notes": item.environment_notes,
            }
        except subprocess.TimeoutExpired as exc:
            return {
                "name": item.name,
                "command": item.command,
                "cwd": str(Path(item.cwd) if item.cwd else project_dir),
                "returncode": 124,
                "stdout": _tail(exc.stdout if isinstance(exc.stdout, str) else ""),
                "stderr": _tail(exc.stderr if isinstance(exc.stderr, str) else ""),
                "timed_out": True,
                "missing_executable": False,
                "status": "timeout",
                "environment_notes": item.environment_notes,
            }
        except FileNotFoundError as exc:
            return {
                "name": item.name,
                "command": item.command,
                "cwd": str(Path(item.cwd) if item.cwd else project_dir),
                "returncode": 127,
                "stdout": "",
                "stderr": str(exc),
                "timed_out": False,
                "missing_executable": True,
                "status": "missing_executable",
                "environment_notes": item.environment_notes,
            }

    def _write_report(
        self,
        project_dir: Path,
        iteration: int,
        label: str,
        commands: list[VerificationCommand],
        results: list[dict],
        status: str,
        clean: bool,
    ) -> dict:
        if status == "no_commands" and not clean:
            issues_count = 1
        elif clean:
            issues_count = 0
        else:
            issues_count = sum(1 for result in results if result["returncode"] != 0 or result["timed_out"])

        report_path = project_dir / "tasks" / f"verification_iter{iteration}.md"
        report_path.parent.mkdir(parents=True, exist_ok=True)

        lines = [
            f"# Verification Report - Iteration {iteration}",
            "",
            f"- **Label**: {label}",
            f"- **Status**: {status}",
            f"- **Clean**: {clean}",
            f"- **CWD**: `{project_dir}`",
            f"- **Commands**: {len(commands)}",
            f"- **Issues**: {issues_count}",
            "",
        ]

        if status == "disabled":
            lines.append("Verification is disabled in config.")
        elif status == "no_commands":
            lines.append("No deterministic verifier commands were detected.")
        else:
            for result in results:
                lines.extend(
                    [
                        f"## {result['name']}",
                        "",
                        f"- **Status**: {result['status']}",
                        f"- **CWD**: `{result['cwd']}`",
                        f"- **Command**: `{result['command']}`",
                        f"- **Return code**: {result['returncode']}",
                        f"- **Timed out**: {result['timed_out']}",
                        f"- **Missing executable**: {result['missing_executable']}",
                    ]
                )
                if result["environment_notes"]:
                    lines.append(f"- **Environment**: {' '.join(result['environment_notes'])}")
                lines.extend(
                    [
                        "",
                        "### stdout",
                        "",
                        "```",
                        result["stdout"].strip(),
                        "```",
                        "",
                        "### stderr",
                        "",
                        "```",
                        result["stderr"].strip(),
                        "```",
                        "",
                    ]
                )

        report_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

        return {
            "clean": clean,
            "status": status,
            "issues_count": issues_count,
            "commands": [item.to_dict() for item in commands],
            "results": results,
            "report_path": str(report_path),
        }


def append_verification_issues(project_dir: Path, iteration: int, verification: dict) -> None:
    """Convert verifier failures into bug report issues for the Bug Fixer."""
    evidence_blocker = _verification_evidence_blocker(verification)
    if verification["clean"] and not evidence_blocker:
        return

    bug_report_path = project_dir / "tasks" / "bug_report.md"
    existing = bug_report_path.read_text(encoding="utf-8") if bug_report_path.exists() else ""
    if not existing.strip() or existing.strip() == NO_ISSUES:
        existing = f"# Bug Report - Iteration {iteration}\n"

    active_report, fix_log_suffix = _split_fix_log_suffix(existing)
    issue_number = _count_issue_headings(active_report) + 1
    lines = [active_report.rstrip(), "", "## Deterministic Verification Failures", ""]

    if verification["status"] == "no_commands":
        lines.extend(
            [
                f"## Issue {issue_number}",
                "- **File**: project verification",
                "- **Line**: n/a",
                "- **Severity**: high",
                "- **Type**: test",
                "- **Description**: No deterministic verifier commands were detected, and clean status requires verifier status `passed` with at least one passed command result.",
                f"- **Suggested Fix**: Add a test/build command or configure `verification.commands`. See `{Path(verification['report_path']).name}`.",
                "",
            ]
        )
    elif verification["status"] == "disabled":
        lines.extend(
            [
                f"## Issue {issue_number}",
                "- **File**: project verification",
                "- **Line**: n/a",
                "- **Severity**: high",
                "- **Type**: test",
                "- **Description**: Deterministic verification is disabled, but clean status requires verifier status `passed` with at least one passed command result.",
                f"- **Suggested Fix**: Enable deterministic verification and configure a test/build command. See `{Path(verification['report_path']).name}`.",
                "",
            ]
        )
    elif verification["clean"] and evidence_blocker:
        description, suggested_fix = evidence_blocker
        lines.extend(
            [
                f"## Issue {issue_number}",
                "- **File**: project verification",
                "- **Line**: n/a",
                "- **Severity**: high",
                "- **Type**: test",
                f"- **Description**: {description}",
                f"- **Suggested Fix**: {suggested_fix}",
                "",
            ]
        )
    else:
        appended = False
        for result in verification.get("results", []):
            if result["returncode"] == 0 and not result["timed_out"]:
                continue
            reason = "timed out" if result["timed_out"] else f"failed with return code {result['returncode']}"
            if result.get("missing_executable"):
                reason = "could not start because the executable was not found"
            lines.extend(
                [
                    f"## Issue {issue_number}",
                    "- **File**: project verification",
                    "- **Line**: n/a",
                    "- **Severity**: high",
                    "- **Type**: test",
                    f"- **Description**: `{result['command']}` {reason}.",
                    f"- **Suggested Fix**: Inspect `{Path(verification['report_path']).name}` and fix the underlying test/build/environment failure.",
                ]
            )
            output = _issue_output_snippet(result)
            if output:
                lines.append(f"- **Output**: {output}")
            lines.append("")
            issue_number += 1
            appended = True

        if not appended and evidence_blocker:
            description, suggested_fix = evidence_blocker
            lines.extend(
                [
                    f"## Issue {issue_number}",
                    "- **File**: project verification",
                    "- **Line**: n/a",
                    "- **Severity**: high",
                    "- **Type**: test",
                    f"- **Description**: {description}",
                    f"- **Suggested Fix**: {suggested_fix}",
                    "",
                ]
            )

    updated_report = "\n".join(lines).rstrip() + "\n"
    if fix_log_suffix.strip():
        updated_report = updated_report.rstrip() + "\n\n" + fix_log_suffix.lstrip("\n")
    bug_report_path.write_text(updated_report, encoding="utf-8")
    (project_dir / "tasks" / f"bug_report_iter{iteration}.md").write_text(
        bug_report_path.read_text(encoding="utf-8"),
        encoding="utf-8",
    )


def load_config(path: Path | None) -> dict:
    if path and path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"verification": {"enabled": True, "commands": [], "timeout_seconds": 300}}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run deterministic verification commands.")
    parser.add_argument("--project-dir", required=True, help="Project directory to verify.")
    parser.add_argument("--iteration", type=int, required=True, help="Pipeline iteration number.")
    parser.add_argument("--label", default="review", help="Label for the verification report.")
    parser.add_argument("--config", help="Path to config.json.")
    parser.add_argument("--append-bug-report", action="store_true", help="Append failures to tasks/bug_report.md.")
    args = parser.parse_args(argv)

    project_dir = Path(args.project_dir).resolve()
    config = load_config(Path(args.config).resolve() if args.config else None)
    verification = VerificationRunner(config).run(project_dir, args.iteration, args.label)
    if args.append_bug_report:
        append_verification_issues(project_dir, args.iteration, verification)

    print(f"verification status: {verification['status']}")
    print(f"clean: {verification['clean']}")
    print(f"report: {verification['report_path']}")
    return 0 if verification["clean"] and not _verification_evidence_blocker(verification) else 1


def _argv_command(
    name: str,
    args: list[str],
    display: str | None = None,
    cwd: str | None = None,
    env_overrides: dict[str, str | None] | None = None,
    environment_notes: list[str] | None = None,
) -> VerificationCommand:
    return VerificationCommand(
        name=name,
        command=display or " ".join(_quote(part) for part in args),
        args=args,
        shell=False,
        cwd=cwd,
        env_overrides=env_overrides or {},
        environment_notes=environment_notes or [],
    )


def _extract_external_project_roots(text: str) -> list[Path]:
    candidates: list[Path] = []
    patterns = [
        r'[A-Za-z]:[/\\][^\s`\'"]*futures-bot-v2',
        r'/[A-Za-z]/[^\s`\'"]*futures-bot-v2',
    ]
    for pattern in patterns:
        for match in re.findall(pattern, text, flags=re.IGNORECASE):
            path = Path(_normalize_external_path(match.rstrip(".,;:)]}")))
            if path.exists() and path not in candidates:
                candidates.append(path)
    return candidates


def _normalize_external_path(value: str) -> str:
    value = value.replace("\\", "/")
    if re.fullmatch(r"/[A-Za-z]/.*", value):
        return f"{value[1]}:/{value[3:]}"
    return value


def _split_command(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return [command]


def _command_invokes_python(command: str) -> bool:
    normalized_command = command.replace("\\", "/")
    if re.search(
        r"(?i)(^|[\s\"'/])(?:py|python\d*(?:\.\d+)?)(?:\.exe)?(?=$|[\s\"'])",
        normalized_command,
    ):
        return True

    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()

    for token in tokens:
        normalized = token.strip("\"'")
        if not normalized or normalized.startswith("-"):
            continue
        executable = normalized.replace("\\", "/").rsplit("/", 1)[-1].lower()
        if executable in {"py", "py.exe", "python", "python.exe", "python3", "python3.exe"}:
            return True
        if re.fullmatch(r"python\d+(\.\d+)?(\.exe)?", executable):
            return True
    return False


def _quote(value: str) -> str:
    return shlex.quote(value) if re.search(r"\s", value) else value


def _tail(value: str, limit: int = 8000) -> str:
    return (value or "")[-limit:]


def _sanitized_python_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONHOME", None)
    env.pop("UV_INTERNAL__PYTHONHOME", None)
    env["PYTHONNOUSERSITE"] = "1"
    return env


def _python_env_overrides() -> dict[str, str | None]:
    return {
        "PYTHONHOME": None,
        "UV_INTERNAL__PYTHONHOME": None,
        "PYTHONNOUSERSITE": "1",
    }


def _has_pytest_config(project_dir: Path) -> bool:
    if (project_dir / "pytest.ini").exists():
        return True
    pyproject = project_dir / "pyproject.toml"
    if pyproject.exists():
        text = pyproject.read_text(encoding="utf-8", errors="replace")
        if re.search(r"(?m)^\s*\[\s*tool\.pytest\.ini_options\s*\]\s*$", text):
            return True
    for name in ["setup.cfg", "tox.ini"]:
        path = project_dir / name
        if not path.exists():
            continue
        parser = configparser.ConfigParser()
        try:
            parser.read(path, encoding="utf-8")
        except configparser.Error:
            text = path.read_text(encoding="utf-8", errors="replace")
            if re.search(r"(?m)^\s*\[\s*(?:pytest|tool:pytest)\s*\]\s*$", text):
                return True
            continue
        sections = {section.lower().strip() for section in parser.sections()}
        if sections.intersection({"pytest", "tool:pytest"}):
            return True
    return False


def _python_compile_targets(project_dir: Path) -> list[str]:
    targets: list[str] = []
    for child in sorted(project_dir.iterdir(), key=lambda item: item.name.lower()):
        if child.name in SKIP_DIRS or child.name.startswith("."):
            continue
        if child.is_file() and child.suffix == ".py":
            targets.append(child.name)
        elif child.is_dir() and _dir_contains_python(child):
            targets.append(child.name)
    return targets


def _dir_contains_python(path: Path) -> bool:
    for root, dirs, files in os.walk(path):
        dirs[:] = [name for name in dirs if name not in SKIP_DIRS and not name.startswith(".")]
        if any(name.endswith(".py") for name in files):
            return True
    return False


def _select_node_package_manager(project_dir: Path) -> str:
    if (project_dir / "pnpm-lock.yaml").exists():
        return "pnpm"
    if (project_dir / "yarn.lock").exists():
        return "yarn"
    if (project_dir / "bun.lockb").exists() or (project_dir / "bun.lock").exists():
        return "bun"
    return "npm"


def _node_script_command(manager: str, script_name: str) -> VerificationCommand:
    executable, env_overrides, environment_notes = _resolve_node_package_manager(manager)
    if manager == "npm":
        args = [executable, "test"] if script_name == "test" else [executable, "run", script_name]
    elif manager == "yarn":
        args = [executable, script_name]
    elif manager == "pnpm":
        args = [executable, script_name]
    elif manager == "bun":
        args = [executable, "run", script_name]
    else:
        args = [executable, "run", script_name]
    display = f"{manager} {script_name}" if manager != "npm" or script_name != "test" else "npm test"
    if manager == "npm" and script_name != "test":
        display = f"npm run {script_name}"
    return _argv_command(
        f"{manager} {script_name}",
        args,
        display=display,
        env_overrides=env_overrides,
        environment_notes=environment_notes,
    )


def _resolve_node_package_manager(manager: str) -> tuple[str, dict[str, str | None], list[str]]:
    executable = _resolve_executable(manager, _node_package_manager_candidates(manager))
    if not executable:
        return manager, {}, []

    executable_path = Path(executable)
    env_overrides: dict[str, str | None] = {}
    if executable_path.is_absolute():
        env_overrides["PATH"] = _prepend_path(str(executable_path.parent), os.environ.get("PATH", ""))
        return executable, env_overrides, [f"Node package manager resolved to {executable}."]
    return executable, env_overrides, []


def _resolve_executable(command: str, candidates: list[Path]) -> str | None:
    found = shutil.which(command)
    if found:
        return found
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return None


def _node_package_manager_candidates(manager: str) -> list[Path]:
    names = _executable_names(manager)
    home = Path(os.environ.get("USERPROFILE") or os.environ.get("HOME") or str(Path.home()))
    appdata = Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")
    local = Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local")
    program_files = Path(os.environ.get("ProgramFiles") or "C:/Program Files")
    program_files_x86 = Path(os.environ.get("ProgramFiles(x86)") or "C:/Program Files (x86)")
    roots = [
        local / "hermes" / "node",
        program_files / "nodejs",
        program_files_x86 / "nodejs",
        appdata / "npm",
        local / "Programs" / "nodejs",
    ]
    return [root / name for root in roots for name in names]


def _executable_names(command: str) -> list[str]:
    if os.name != "nt" or Path(command).suffix:
        return [command]
    return [f"{command}{extension.lower()}" for extension in [".cmd", ".exe", ".bat", ""]]


def _prepend_path(path: str, existing: str) -> str:
    parts = [part for part in existing.split(os.pathsep) if part]
    key = (lambda value: value.lower()) if os.name == "nt" else (lambda value: value)
    existing_keys = {key(part) for part in parts}
    if key(path) not in existing_keys:
        parts.insert(0, path)
    return os.pathsep.join(parts)


def _is_placeholder_npm_test(script: str) -> bool:
    return bool(PLACEHOLDER_NPM_TEST_RE.search(script.strip()))


def _count_issue_headings(report: str) -> int:
    return len(re.findall(r"(?m)^##\s+Issue\s+\d+\b", _active_report_section(report)))


def _active_report_section(report: str) -> str:
    match = re.search(r"(?m)^##\s+Fix Log\b.*$", report)
    return report[: match.start()] if match else report


def _split_fix_log_suffix(report: str) -> tuple[str, str]:
    match = re.search(r"(?m)^##\s+Fix Log\b.*$", report)
    if not match:
        return report, ""
    return report[: match.start()].rstrip() + "\n", report[match.start():]


def _verification_has_passed_command(verification: dict) -> bool:
    for result in verification.get("results", []):
        if (
            result.get("status") == "passed"
            and result.get("returncode") == 0
            and not result.get("timed_out")
        ):
            return True
    return False


def _verification_evidence_blocker(verification: dict) -> tuple[str, str] | None:
    status = verification.get("status", "unknown")
    report_name = Path(verification.get("report_path") or "verification report").name
    if status == "disabled":
        return (
            "Deterministic verification is disabled, but clean status requires verifier status `passed` with at least one passed command result.",
            f"Enable deterministic verification and configure a test/build command. See `{report_name}`.",
        )
    if status == "no_commands":
        return (
            "No deterministic verifier commands were detected, and clean status requires verifier status `passed` with at least one passed command result.",
            f"Add a test/build command or configure `verification.commands`. See `{report_name}`.",
        )
    if status != "passed":
        return (
            f"Verifier status is `{status}`, but clean status requires `passed` with at least one passed command result.",
            f"Inspect `{report_name}` and fix the verifier startup, configuration, command, or reporting failure.",
        )
    if not verification.get("clean"):
        return (
            "Deterministic verifier result is not clean.",
            f"Inspect `{report_name}` and fix the underlying deterministic verification failure.",
        )
    if not _verification_has_passed_command(verification):
        return (
            "Verifier status is `passed`, but the verifier report has no passed command result evidence.",
            f"Configure or run deterministic verification so `{report_name}` records at least one passed command result.",
        )
    return None


def _issue_output_snippet(result: dict) -> str:
    snippets: list[str] = []
    stdout = (result.get("stdout") or "").strip()
    stderr = (result.get("stderr") or "").strip()
    if stdout:
        snippets.append("stdout: " + _one_line(stdout[-500:]))
    if stderr:
        snippets.append("stderr: " + _one_line(stderr[-500:]))
    return " | ".join(snippets)


def _one_line(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


if __name__ == "__main__":
    raise SystemExit(main())
