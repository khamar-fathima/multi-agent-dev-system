"""Tester agent: reviews code for bugs, leaks, perf, security, and spec drift."""

from __future__ import annotations

import re
from pathlib import Path

from .base import BaseAgent, collect_code_files, extract_fenced_section

NO_ISSUES = "NO_ISSUES_FOUND"
BUG_REPORT_TYPE_SCHEMA = "bug | security | performance | structural | spec | test"


class TesterAgent(BaseAgent):
    def run(self, project_dir: Path, iteration: int) -> dict:
        system_prompt = self.load_system_prompt("tester")

        requirements = self._read_optional(project_dir / "tasks" / "requirements.md")
        plan = self._read_optional(project_dir / "tasks" / "plan.md")
        todo = self._read_optional(project_dir / "tasks" / "todo.md")
        coder_report = self._read_optional(project_dir / "tasks" / "coder_report.md")
        prior_report = self._read_optional(project_dir / "tasks" / "bug_report.md")

        max_bytes = self.config.get("tester_max_file_bytes", 200_000)
        code_files = collect_code_files(project_dir, max_bytes=max_bytes)
        dump = self._build_code_dump(project_dir, code_files)

        user_message = (
            f"# Requirements\n\n{requirements or '(no requirements available)'}\n\n"
            f"# Plan (spec to verify against)\n\n{plan or '(no plan available)'}\n\n"
            f"# TODO Checklist\n\n{todo or '(no todo available)'}\n\n"
            f"# Coder Report\n\n{coder_report or '(missing coder report)'}\n\n"
            f"# Prior Bug Report (iteration {iteration - 1})\n\n{prior_report or '(none - first review)'}\n\n"
            f"# Code Under Review (iteration {iteration})\n\n{dump}\n\n"
            "# Your Task\n\n"
            "Review every file. Report bugs, memory leaks, performance issues, structural problems, "
            "security flaws, and any deviation from the requirements, plan, or TODO checklist.\n\n"
            "Also verify TODO honesty, missing tasks/coder_report.md, missing verification evidence, "
            "unrecorded skipped checks, and falsely checked TODO items. For prior fixes, audit the actual "
            "fix site and adjacent caller semantics for newly introduced defects before clearing or "
            "re-flagging an issue. Cite relative file paths and line numbers wherever practical.\n\n"
            "If there are NO issues, output exactly:\n\n"
            "=== BUG_REPORT.md ===\n"
            f"{NO_ISSUES}\n"
            "=== END BUG_REPORT.md ===\n\n"
            "Otherwise output:\n\n"
            "=== BUG_REPORT.md ===\n"
            f"# Bug Report - Iteration {iteration}\n\n"
            "## Issue 1\n"
            "- **File**: <relative path>\n"
            "- **Line**: <line or range>\n"
            "- **Severity**: critical | high | medium | low\n"
            f"- **Type**: {BUG_REPORT_TYPE_SCHEMA}\n"
            "- **Description**: <what's wrong>\n"
            "- **Suggested Fix**: <minimal change>\n\n"
            "## Issue 2 ...\n"
            "=== END BUG_REPORT.md ===\n"
        )

        output = self.run_claude_stage(
            project_dir,
            stage=f"tester-iteration-{iteration}",
            system_prompt=system_prompt,
            user_message=user_message,
            artifact_hint="BUG_REPORT.md",
            max_tokens_key="max_tokens_tester",
        )
        report = extract_fenced_section(output, "BUG_REPORT.md")
        if not report:
            (project_dir / "tasks" / f"tester_raw_iter{iteration}.md").write_text(
                output, encoding="utf-8"
            )
            raise RuntimeError(
                f"Tester output missing BUG_REPORT.md markers; see tasks/tester_raw_iter{iteration}.md"
            )

        (project_dir / "tasks" / "bug_report.md").write_text(report, encoding="utf-8")
        (project_dir / "tasks" / f"bug_report_iter{iteration}.md").write_text(
            report, encoding="utf-8"
        )

        clean = report.strip() == NO_ISSUES
        bugs_count = 0 if clean else count_report_issues(report)
        self.log(project_dir, f"iter={iteration} clean={clean} bugs={bugs_count}")
        return {"clean": clean, "bugs_count": bugs_count, "iteration": iteration}

    # --------------------------------------------------------------------

    def _read_optional(self, path: Path) -> str:
        return path.read_text(encoding="utf-8") if path.exists() else ""

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


def count_report_issues(report: str) -> int:
    """Count real issue headings without counting fix-log or summary headings."""
    return len(re.findall(r"(?m)^##\s+Issue\s+\d+\b", active_report_section(report)))


def active_report_section(report: str) -> str:
    """Return the active issue report before any appended fix log."""
    match = re.search(r"(?m)^##\s+Fix Log\b.*$", report)
    return report[: match.start()] if match else report
