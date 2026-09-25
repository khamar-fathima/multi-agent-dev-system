"""Bug Fixer agent: reads bug_report.md and applies fixes.

The OpenAI SDK is imported lazily so Codex-only runs and tests do not require it.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .base import BaseAgent, collect_code_files, parse_file_blocks, write_files


class BugFixerAgent(BaseAgent):
    def run(self, project_dir: Path, iteration: int) -> dict:
        backend = self._resolve_backend()
        self.log(project_dir, f"iter={iteration} backend={backend}")
        if backend == "codex":
            return self._run_codex(project_dir, iteration)
        return self._run_openai(project_dir, iteration)

    # ------------------------- backend selection -------------------------

    def _resolve_backend(self) -> str:
        choice = self.config.get("coder_backend", "auto")
        if choice == "codex":
            return "codex"
        if choice == "openai":
            return "openai"
        return "codex" if shutil.which(self.config.get("codex_command", "codex")) else "openai"

    # ------------------------- codex CLI backend -------------------------

    def _run_codex(self, project_dir: Path, iteration: int) -> dict:
        system_prompt = self.load_system_prompt("bug_fixer")
        prompt = (
            f"{system_prompt}\n\n"
            "# Working Directory\n"
            f"You are running in: {project_dir}\n\n"
            f"# Your Task (Fix Iteration {iteration})\n"
            "1. Read tasks/bug_report.md (the issue list).\n"
            "2. For EVERY issue: read the affected code and caller context, then apply the minimal "
            "root-cause fix.\n"
            "3. Rerun relevant deterministic checks where practical and record the exact command/result.\n"
            f"4. Append a section `## Fix Log - Iteration {iteration}` (or the existing dash style) to "
            "tasks/bug_report.md. For each issue, list the change, files touched, verification evidence, "
            "or explicit false-positive evidence.\n"
        )

        # Keep the prompt off argv. Large reports/plans exceed the Windows
        # command-line limit when launched through npm's codex.CMD wrapper.
        cmd = [self.resolve_command("codex_command", "codex"), *self.config.get("codex_args", []), "-"]
        self.log(project_dir, f"exec: {' '.join(cmd[:-1])} <stdin {len(prompt)} chars>")

        result = subprocess.run(
            cmd,
            cwd=str(project_dir),
            input=prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        self.log(project_dir, f"returncode={result.returncode}")
        if result.stdout:
            self.log(project_dir, f"stdout[:2000]={result.stdout[:2000]}")
        if result.stderr:
            self.log(project_dir, f"stderr[:2000]={result.stderr[:2000]}")
        if result.returncode != 0:
            raise RuntimeError(
                f"Codex bug fixer failed with return code {result.returncode}. "
                "See logs/BugFixerAgent.log for stdout/stderr."
            )
        return {"backend": "codex", "success": True}

    # ------------------------- openai API backend ------------------------

    def _run_openai(self, project_dir: Path, iteration: int) -> dict:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError(
                "OpenAI Python SDK is required for coder_backend='openai'. "
                "Install requirements.txt or use coder_backend='codex'."
            ) from exc

        bug_report = (project_dir / "tasks" / "bug_report.md").read_text(encoding="utf-8")
        plan = self._read_optional(project_dir / "tasks" / "plan.md")

        relevant = self._files_mentioned_in_report(project_dir, bug_report)
        if not relevant:
            relevant = collect_code_files(
                project_dir,
                max_bytes=self.config.get("tester_max_file_bytes", 200_000),
            )

        code_dump = self._build_code_dump(project_dir, relevant)

        system_prompt = self.load_system_prompt("bug_fixer") + (
            "\n\n# Output Protocol\n"
            "You cannot edit files directly. For every file you change, emit:\n\n"
            "=== FILE: relative/path.ext ===\n"
            "<entire updated file content>\n"
            "=== END FILE ===\n\n"
            "Rules:\n"
            "- Use forward slashes. Paths relative to project root.\n"
            "- Emit the FULL new content of each file (no diffs / no partials).\n"
            "- Only emit files you actually changed.\n"
            "- After the FILE blocks for code, emit one FILE block for tasks/bug_report.md containing the "
            f"original report plus an appended `## Fix Log - Iteration {iteration}` section. The log must "
            "cover each issue, files touched, verification commands/results, and explicit evidence for any "
            "false positive.\n"
        )

        user_message = (
            f"# Plan\n\n{plan or '(no plan)'}\n\n"
            f"# Bug Report (iteration {iteration})\n\n{bug_report}\n\n"
            f"# Current Code\n\n{code_dump}\n\n"
            "Apply the minimal root-cause fix for every issue now. Treat deterministic verifier failures "
            "as real issues unless the fix log proves an environment-only blocker."
        )

        client = OpenAI()
        response = client.chat.completions.create(
            model=self.config["openai_model"],
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            max_completion_tokens=self.config.get("max_tokens_coder", 32000),
        )
        output = response.choices[0].message.content or ""

        blocks = parse_file_blocks(output)
        written = write_files(project_dir, blocks)

        (project_dir / "tasks" / f"bug_fixer_raw_iter{iteration}.md").write_text(
            output, encoding="utf-8"
        )
        self.log(project_dir, f"iter={iteration} openai backend wrote {len(written)} files")

        if not written:
            raise RuntimeError(
                f"Bug Fixer produced no FILE blocks; see tasks/bug_fixer_raw_iter{iteration}.md"
            )
        return {"backend": "openai", "files_written": len(written)}

    # --------------------------------------------------------------------

    def _read_optional(self, path: Path) -> str:
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def _files_mentioned_in_report(self, project_dir: Path, report: str) -> list[Path]:
        all_files = collect_code_files(
            project_dir,
            max_bytes=self.config.get("tester_max_file_bytes", 200_000),
        )
        mentioned: list[Path] = []
        for path in all_files:
            rel_posix = path.relative_to(project_dir).as_posix()
            rel_win = rel_posix.replace("/", "\\")
            if rel_posix in report or rel_win in report or path.name in report:
                mentioned.append(path)
        return mentioned

    def _build_code_dump(self, project_dir: Path, files: list[Path]) -> str:
        chunks: list[str] = []
        for path in files:
            rel = path.relative_to(project_dir).as_posix()
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except Exception as exc:
                chunks.append(f"### {rel}\n(could not read: {exc})\n")
                continue
            chunks.append(f"### {rel}\n```\n{content}\n```\n")
        return "\n".join(chunks) if chunks else "(no code files found)"
