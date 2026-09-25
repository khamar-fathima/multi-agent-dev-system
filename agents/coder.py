"""Coder agent: reads plan + todo, writes code.

Two backends:
- "codex": shell out to the OpenAI Codex CLI for autonomous file edits
- "openai": call the OpenAI Python SDK and parse FILE blocks from the response

The OpenAI SDK is imported lazily so Codex-only runs and tests do not require it.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .base import BaseAgent, parse_file_blocks, write_files


class CoderAgent(BaseAgent):
    def run(self, project_dir: Path) -> dict:
        backend = self._resolve_backend()
        self.log(project_dir, f"backend={backend}")
        if backend == "codex":
            return self._run_codex(project_dir)
        return self._run_openai(project_dir)

    # ------------------------- backend selection -------------------------

    def _resolve_backend(self) -> str:
        choice = self.config.get("coder_backend", "auto")
        if choice == "codex":
            return "codex"
        if choice == "openai":
            return "openai"
        return "codex" if shutil.which(self.config.get("codex_command", "codex")) else "openai"

    # ------------------------- codex CLI backend -------------------------

    def _run_codex(self, project_dir: Path) -> dict:
        system_prompt = self.load_system_prompt("coder")
        requirements = self._read_optional(project_dir / "tasks" / "requirements.md")
        prompt = (
            f"{system_prompt}\n\n"
            "# Working Directory\n"
            f"You are running in: {project_dir}\n\n"
            "# Requirements\n\n"
            f"{requirements or '(tasks/requirements.md not found)'}\n\n"
            "# Your Task\n"
            "1. Read tasks/requirements.md, tasks/plan.md, and tasks/todo.md before editing.\n"
            "2. Implement the plan exactly. Do not add speculative features or broaden scope.\n"
            "3. Create/edit project files directly in this directory. Use tasks/ only for coordination notes.\n"
            "4. Update tasks/todo.md as you go, but only mark an item complete after the code and relevant "
            "verification for that item are done.\n"
            "5. Leave incomplete or blocked TODO items unchecked and explain why in tasks/coder_report.md.\n"
            "6. When finished, write tasks/coder_report.md with files changed, verification commands run, "
            "exact results or blockers, assumptions, and any deviations from the plan.\n"
        )

        # Codex accepts ``-`` to read the initial prompt from stdin. Passing the
        # full prompt as argv works on POSIX for small jobs but fails on Windows
        # once requirements/plans are realistic: npm's codex.CMD wrapper reports
        # "The command line is too long" before Codex can start. Always stream
        # the prompt through stdin so large project specs are safe.
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
                f"Codex coder failed with return code {result.returncode}. "
                "See logs/CoderAgent.log for stdout/stderr."
            )
        return {"backend": "codex", "success": True}

    # ------------------------- openai API backend ------------------------

    def _run_openai(self, project_dir: Path) -> dict:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError(
                "OpenAI Python SDK is required for coder_backend='openai'. "
                "Install requirements.txt or use coder_backend='codex'."
            ) from exc

        requirements = self._read_optional(project_dir / "tasks" / "requirements.md")
        plan = (project_dir / "tasks" / "plan.md").read_text(encoding="utf-8")
        todo = (project_dir / "tasks" / "todo.md").read_text(encoding="utf-8")

        system_prompt = self.load_system_prompt("coder") + (
            "\n\n# Output Protocol\n"
            "You cannot edit files directly. Emit every file you want to create using this exact block:\n\n"
            "=== FILE: relative/path/from/project/root.ext ===\n"
            "<entire file content>\n"
            "=== END FILE ===\n\n"
            "Rules:\n"
            "- Use forward slashes in paths. Paths are relative to the project root.\n"
            "- Emit one FILE block per file. Emit every file the plan requires.\n"
            "- Do not add out-of-scope files or speculative features.\n"
            "- Emit one block named tasks/todo.md. Mark an item `- [x]` only if the implementation and "
            "relevant verification are complete; leave blocked/incomplete items unchecked.\n"
            "- Emit one block named tasks/coder_report.md listing changed files, verification commands, "
            "exact results, assumptions, blockers, and deviations from the plan.\n"
        )

        user_message = (
            "# tasks/requirements.md\n\n"
            f"{requirements or '(no requirements file available)'}\n\n"
            "# tasks/plan.md\n\n"
            f"{plan}\n\n"
            "# tasks/todo.md\n\n"
            f"{todo}\n\n"
            "Implement the plan now. Record any blocker or skipped check explicitly."
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

        (project_dir / "tasks" / "coder_raw.md").write_text(output, encoding="utf-8")
        self.log(project_dir, f"openai backend wrote {len(written)} files")

        if not written:
            raise RuntimeError(
                "Coder produced no FILE blocks; see tasks/coder_raw.md for the raw response."
            )
        return {"backend": "openai", "files_written": len(written)}

    def _read_optional(self, path: Path) -> str:
        return path.read_text(encoding="utf-8") if path.exists() else ""
