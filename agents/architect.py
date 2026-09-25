"""Architect agent: reads requirements and produces plan.md and todo.md."""

from __future__ import annotations

from pathlib import Path

from .base import BaseAgent, extract_fenced_section


class ArchitectAgent(BaseAgent):
    def run(self, requirements: str, project_dir: Path) -> dict:
        system_prompt = self.load_system_prompt("architect")

        user_message = (
            "# Project Requirements\n\n"
            f"{requirements.strip()}\n\n"
            "# Your Task\n\n"
            "Produce two artifacts:\n"
            "1. PLAN.md: full implementation plan with goal, tech stack and justification, complete "
            "file/directory structure, file-by-file specs, assumptions, non-goals, external dependencies "
            "with versions, exact verification commands, and acceptance criteria tied to deterministic checks.\n"
            "2. TODO.md: ordered, atomic checkable items (`- [ ] item`). Each item must be small enough to "
            "implement in isolation. Setup/scaffolding tasks first. TODO items must not be marked complete "
            "until the Coder has implemented and verified them.\n\n"
            "Wrap each artifact in fenced markers exactly as shown:\n\n"
            "=== PLAN.md ===\n"
            "<plan content>\n"
            "=== END PLAN.md ===\n\n"
            "=== TODO.md ===\n"
            "<todo content>\n"
            "=== END TODO.md ===\n"
        )

        output = self.run_claude_stage(
            project_dir,
            stage="architect",
            system_prompt=system_prompt,
            user_message=user_message,
            artifact_hint="PLAN.md and TODO.md",
            max_tokens_key="max_tokens_architect",
        )

        plan_text = extract_fenced_section(output, "PLAN.md")
        todo_text = extract_fenced_section(output, "TODO.md")

        if not plan_text or not todo_text:
            (project_dir / "tasks" / "architect_raw.md").write_text(output, encoding="utf-8")
            raise RuntimeError(
                "Architect output missing PLAN.md or TODO.md markers; see tasks/architect_raw.md"
            )

        (project_dir / "tasks" / "plan.md").write_text(plan_text, encoding="utf-8")
        (project_dir / "tasks" / "todo.md").write_text(todo_text, encoding="utf-8")

        self.log(project_dir, f"plan: {len(plan_text)} chars, todo: {len(todo_text)} chars")
        return {
            "plan_path": project_dir / "tasks" / "plan.md",
            "todo_path": project_dir / "tasks" / "todo.md",
        }
