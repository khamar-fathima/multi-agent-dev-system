from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import sys
import types
import unittest

from agents.architect import ArchitectAgent
from agents.bug_fixer import BugFixerAgent
from agents.coder import CoderAgent
from agents.tester import (
    BUG_REPORT_TYPE_SCHEMA,
    TesterAgent as PipelineTesterAgent,
    count_report_issues,
)


class CoderAgentTests(unittest.TestCase):
    def test_agent_modules_import_without_optional_sdks(self):
        self.assertEqual(ArchitectAgent.__name__, "ArchitectAgent")
        self.assertEqual(CoderAgent.__name__, "CoderAgent")
        self.assertEqual(PipelineTesterAgent.__name__, "TesterAgent")
        self.assertEqual(BugFixerAgent.__name__, "BugFixerAgent")

    def test_codex_backend_raises_on_nonzero_exit(self):
        with TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            (project_dir / "tasks").mkdir()
            (project_dir / "logs").mkdir()
            agent = CoderAgent({"codex_command": "codex", "codex_args": []})

            result = SimpleNamespace(returncode=7, stdout="out", stderr="err")
            with patch("agents.coder.subprocess.run", return_value=result):
                with self.assertRaisesRegex(RuntimeError, "return code 7"):
                    agent._run_codex(project_dir)

    def test_codex_backend_resolves_windows_cli_wrapper_before_subprocess(self):
        with TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            (project_dir / "tasks").mkdir()
            (project_dir / "logs").mkdir()
            agent = CoderAgent({"codex_command": "codex", "codex_args": []})
            codex_cmd = r"C:\Users\jorda\AppData\Roaming\npm\codex.CMD"

            result = SimpleNamespace(returncode=0, stdout="ok", stderr="")
            with (
                patch("agents.base.shutil.which", return_value=codex_cmd),
                patch("agents.coder.subprocess.run", return_value=result) as run,
            ):
                agent._run_codex(project_dir)

            self.assertEqual(run.call_args.args[0][0], codex_cmd)

    def test_codex_backend_streams_prompt_on_stdin_to_avoid_windows_arg_limit(self):
        with TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            (project_dir / "tasks").mkdir()
            (project_dir / "logs").mkdir()
            (project_dir / "tasks" / "requirements.md").write_text("x" * 20000, encoding="utf-8")
            agent = CoderAgent({"codex_command": "codex", "codex_args": ["exec"]})

            result = SimpleNamespace(returncode=0, stdout="ok", stderr="")
            with patch("agents.coder.subprocess.run", return_value=result) as run:
                agent._run_codex(project_dir)

            cmd = run.call_args.args[0]
            kwargs = run.call_args.kwargs
            self.assertEqual(cmd[-1], "-")
            self.assertNotIn("x" * 1000, cmd)
            self.assertIn("x" * 1000, kwargs["input"])

    def test_bug_fixer_codex_backend_streams_prompt_on_stdin_to_avoid_windows_arg_limit(self):
        with TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            (project_dir / "tasks").mkdir()
            (project_dir / "logs").mkdir()
            (project_dir / "tasks" / "bug_report.md").write_text("issue\n" + ("y" * 20000), encoding="utf-8")
            agent = BugFixerAgent({"codex_command": "codex", "codex_args": ["exec"]})

            result = SimpleNamespace(returncode=0, stdout="ok", stderr="")
            with patch("agents.bug_fixer.subprocess.run", return_value=result) as run:
                agent._run_codex(project_dir, 1)

            cmd = run.call_args.args[0]
            kwargs = run.call_args.kwargs
            self.assertEqual(cmd[-1], "-")
            self.assertNotIn("y" * 1000, cmd)
            self.assertIn("# Your Task (Fix Iteration 1)", kwargs["input"])

    def test_openai_backend_missing_sdk_has_actionable_error(self):
        with TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            (project_dir / "tasks").mkdir()
            (project_dir / "tasks" / "plan.md").write_text("plan", encoding="utf-8")
            (project_dir / "tasks" / "todo.md").write_text("- [ ] item", encoding="utf-8")
            agent = CoderAgent({"openai_model": "gpt-5.5"})

            real_import = __import__

            def fake_import(name, *args, **kwargs):
                if name == "openai":
                    raise ImportError("missing openai")
                return real_import(name, *args, **kwargs)

            with patch("builtins.__import__", side_effect=fake_import):
                with self.assertRaisesRegex(RuntimeError, "OpenAI Python SDK is required"):
                    agent._run_openai(project_dir)

    def test_tester_issue_count_ignores_fix_log_headings(self):
        report = """# Bug Report

## Issue 1
- **File**: app.py

## Fix Log - Iteration 1

## Issue 1
- fixed app.py
"""

        self.assertEqual(count_report_issues(report), 1)

    def test_tester_prompt_uses_shared_bug_report_type_schema(self):
        self.assertEqual(BUG_REPORT_TYPE_SCHEMA, "bug | security | performance | structural | spec | test")
        self.assertNotIn("leak", BUG_REPORT_TYPE_SCHEMA.split(" | "))

    def test_architect_rejects_output_missing_required_markers(self):
        with TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            (project_dir / "tasks").mkdir()
            agent = ArchitectAgent({"claude_backend": "anthropic_api", "claude_model": "test-model"})

            with self._fake_anthropic("PLAN\nTODO"):
                with self.assertRaisesRegex(RuntimeError, "missing PLAN.md or TODO.md markers"):
                    agent.run("requirements", project_dir)

            self.assertTrue((project_dir / "tasks" / "architect_raw.md").exists())
            self.assertFalse((project_dir / "tasks" / "plan.md").exists())

    def test_tester_rejects_clean_sentinel_missing_required_markers(self):
        with TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            (project_dir / "tasks").mkdir()
            agent = PipelineTesterAgent(
                {"claude_backend": "anthropic_api", "claude_model": "test-model"}
            )

            with self._fake_anthropic("NO_ISSUES_FOUND"):
                with self.assertRaisesRegex(RuntimeError, "missing BUG_REPORT.md markers"):
                    agent.run(project_dir, 1)

            self.assertTrue((project_dir / "tasks" / "tester_raw_iter1.md").exists())
            self.assertFalse((project_dir / "tasks" / "bug_report.md").exists())

    def _fake_anthropic(self, output: str):
        module = types.ModuleType("anthropic")

        class FakeMessages:
            def create(self, **kwargs):
                return SimpleNamespace(
                    content=[SimpleNamespace(type="text", text=output)]
                )

        module.Anthropic = lambda: SimpleNamespace(messages=FakeMessages())
        return patch.dict(sys.modules, {"anthropic": module})


if __name__ == "__main__":
    unittest.main()
