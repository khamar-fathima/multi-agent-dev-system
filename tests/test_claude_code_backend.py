from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agents.architect import ArchitectAgent
from orchestrator import check_environment


class ClaudeCodeBackendTests(unittest.TestCase):
    def test_architect_uses_claude_code_cli_without_anthropic_api_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            (project_dir / "tasks").mkdir()
            (project_dir / "logs").mkdir()
            config = {
                "claude_backend": "claude_code",
                "claude_command": "claude",
                "claude_code_model": "opus",
                "claude_code_max_turns": 12,
            }

            def fake_run(cmd, **kwargs):
                self.assertIn("claude", cmd[0])
                self.assertIn("-p", cmd)
                self.assertIn("--append-system-prompt-file", cmd)
                self.assertIn("--no-session-persistence", cmd)
                self.assertIn("--model", cmd)
                self.assertEqual(cmd[cmd.index("--model") + 1], "opus")
                self.assertIn("--max-turns", cmd)
                self.assertEqual(cmd[cmd.index("--max-turns") + 1], "12")
                self.assertEqual(kwargs["cwd"], str(project_dir))
                self.assertIn("# Project Requirements", kwargs["input"])
                return subprocess.CompletedProcess(
                    cmd,
                    0,
                    stdout=(
                        "=== PLAN.md ===\nplan\n=== END PLAN.md ===\n"
                        "=== TODO.md ===\n- [ ] todo\n=== END TODO.md ===\n"
                    ),
                    stderr="",
                )

            with (
                patch.object(ArchitectAgent, "load_system_prompt", return_value="system"),
                patch("agents.base.shutil.which", return_value="C:/Tools/claude.cmd"),
                patch("agents.base.subprocess.run", side_effect=fake_run),
            ):
                result = ArchitectAgent(config).run("build it", project_dir)

            self.assertEqual((project_dir / "tasks" / "plan.md").read_text(encoding="utf-8"), "plan")
            self.assertEqual((project_dir / "tasks" / "todo.md").read_text(encoding="utf-8"), "- [ ] todo")
            self.assertTrue((project_dir / "tasks" / ".claude_prompts" / "architect_user.md").exists())
            self.assertEqual(result["plan_path"], project_dir / "tasks" / "plan.md")

    def test_environment_accepts_subscription_cli_auth_without_api_keys(self) -> None:
        config = {
            "claude_backend": "claude_code",
            "claude_command": "claude",
            "coder_backend": "codex",
            "codex_command": "codex",
        }

        def fake_which(command: str) -> str | None:
            return f"C:/Tools/{command}.cmd" if command in {"claude", "codex"} else None

        with (
            patch.dict(os.environ, {}, clear=True),
            patch("orchestrator.shutil.which", side_effect=fake_which),
        ):
            self.assertTrue(check_environment(config))

    def test_environment_requires_anthropic_key_only_for_api_backend(self) -> None:
        config = {
            "claude_backend": "anthropic_api",
            "coder_backend": "codex",
            "codex_command": "codex",
        }

        with (
            patch.dict(os.environ, {}, clear=True),
            patch("orchestrator.shutil.which", return_value="C:/Tools/codex.cmd"),
        ):
            self.assertFalse(check_environment(config))


if __name__ == "__main__":
    unittest.main()
