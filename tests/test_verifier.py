from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import os
import unittest

from verifier import (
    PythonCandidate,
    VerificationCommand,
    VerificationRunner,
    append_verification_issues,
    main,
)


class VerificationRunnerTests(unittest.TestCase):
    def test_uses_configured_commands_before_detection(self):
        runner = VerificationRunner(
            {
                "verification": {
                    "enabled": True,
                    "commands": [{"name": "custom", "command": "echo ok"}],
                }
            }
        )

        with TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            (project_dir / "package.json").write_text(
                '{"scripts": {"test": "vitest"}}',
                encoding="utf-8",
            )
            commands = runner._commands_for(project_dir)

        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0].name, "custom")
        self.assertEqual(commands[0].command, "echo ok")
        self.assertTrue(commands[0].shell)
        self.assertEqual(commands[0].env_overrides, {})

    def test_configured_python_commands_get_sanitized_environment(self):
        for configured_command in [
            "python -m unittest",
            "python3 -m pytest",
            "py -3.11 -m pytest",
            ".venv\\Scripts\\python.exe -m unittest",
        ]:
            with self.subTest(command=configured_command):
                runner = VerificationRunner(
                    {
                        "verification": {
                            "enabled": True,
                            "commands": [{"name": "unit", "command": configured_command}],
                        }
                    }
                )

                command = runner._commands_for(Path("."))[0]

                self.assertTrue(command.shell)
                self.assertEqual(command.env_overrides["PYTHONHOME"], None)
                self.assertEqual(command.env_overrides["UV_INTERNAL__PYTHONHOME"], None)
                self.assertEqual(command.env_overrides["PYTHONNOUSERSITE"], "1")
                self.assertIn("PYTHONHOME", " ".join(command.environment_notes))

    def test_configured_python_command_clears_fragile_environment_when_run(self):
        runner = VerificationRunner(
            {
                "verification": {
                    "enabled": True,
                    "commands": ["python -m unittest"],
                }
            }
        )
        command = runner._commands_for(Path("."))[0]
        captured = {}

        def fake_run(*args, **kwargs):
            captured["env"] = kwargs["env"]
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"PYTHONHOME": "bad", "UV_INTERNAL__PYTHONHOME": "bad"}):
                with patch("verifier.subprocess.run", side_effect=fake_run):
                    runner._run_command(Path(tmp), command)

        self.assertNotIn("PYTHONHOME", captured["env"])
        self.assertNotIn("UV_INTERNAL__PYTHONHOME", captured["env"])
        self.assertEqual(captured["env"]["PYTHONNOUSERSITE"], "1")

    def test_tests_directory_without_pytest_config_uses_unittest(self):
        runner = self._runner_with_python()

        with TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            (project_dir / "tests").mkdir()
            (project_dir / "tests" / "test_sample.py").write_text(
                "import unittest\n\nclass T(unittest.TestCase):\n    pass\n",
                encoding="utf-8",
            )
            commands = runner._commands_for(project_dir)

        self.assertEqual(commands[0].name, "python unittest")
        self.assertIn("-m unittest discover -s tests -v", commands[0].command)
        self.assertTrue(any(command.name == "python compileall" for command in commands))

    def test_pytest_config_uses_pytest(self):
        runner = self._runner_with_python()

        with TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            (project_dir / "tests").mkdir()
            (project_dir / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
            commands = runner._commands_for(project_dir)

        self.assertEqual(commands[0].name, "python pytest")
        self.assertIn("-m pytest", commands[0].command)

    def test_pytest_dependency_mention_without_config_uses_unittest(self):
        runner = self._runner_with_python()

        with TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            (project_dir / "tests").mkdir()
            (project_dir / "tests" / "test_sample.py").write_text(
                "import unittest\n\nclass T(unittest.TestCase):\n    pass\n",
                encoding="utf-8",
            )
            (project_dir / "pyproject.toml").write_text(
                '[project.optional-dependencies]\ndev = ["pytest"]\n',
                encoding="utf-8",
            )
            commands = runner._commands_for(project_dir)

        self.assertEqual(commands[0].name, "python unittest")
        self.assertIn("-m unittest discover -s tests -v", commands[0].command)

    def test_placeholder_npm_test_is_ignored_and_scripts_keep_order(self):
        runner = VerificationRunner({"verification": {"enabled": True, "commands": []}})

        with TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            (project_dir / "package.json").write_text(
                '{"scripts": {"test": "echo \\"Error: no test specified\\" && exit 1", '
                '"typecheck": "tsc --noEmit", "build": "vite build", "lint": "eslint ."}}',
                encoding="utf-8",
            )
            commands = runner._commands_for(project_dir)

        self.assertEqual(
            [item.command for item in commands],
            ["npm run build", "npm run lint", "npm run typecheck"],
        )

    def test_node_package_manager_selected_from_lockfile(self):
        runner = VerificationRunner({"verification": {"enabled": True, "commands": []}})

        with TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            (project_dir / "pnpm-lock.yaml").write_text("lock", encoding="utf-8")
            (project_dir / "package.json").write_text(
                '{"scripts": {"test": "vitest", "build": "vite build"}}',
                encoding="utf-8",
            )
            commands = runner._commands_for(project_dir)

        self.assertEqual([item.command for item in commands], ["pnpm test", "pnpm build"])

    def test_node_commands_resolve_common_hermes_npm_when_not_on_path(self):
        runner = VerificationRunner({"verification": {"enabled": True, "commands": []}})

        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            project_dir = base / "project"
            project_dir.mkdir()
            hermes_node = base / "local" / "hermes" / "node"
            hermes_node.mkdir(parents=True)
            npm_cmd = hermes_node / ("npm.cmd" if os.name == "nt" else "npm")
            npm_cmd.write_text("", encoding="utf-8")
            (project_dir / "package.json").write_text(
                '{"scripts": {"test": "vitest"}}',
                encoding="utf-8",
            )

            with patch.dict(os.environ, {"LOCALAPPDATA": str(base / "local"), "PATH": ""}):
                with patch("verifier.shutil.which", return_value=None):
                    commands = runner._commands_for(project_dir)

        self.assertEqual(commands[0].command, "npm test")
        self.assertEqual(commands[0].args[:2], [str(npm_cmd), "test"])
        self.assertTrue(commands[0].env_overrides["PATH"].startswith(str(hermes_node)))
        self.assertIn("Node package manager resolved", commands[0].environment_notes[0])

    def test_failed_verification_appends_actionable_bug_report_issue(self):
        with TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            (project_dir / "tasks").mkdir()
            verification = {
                "clean": False,
                "status": "failed",
                "report_path": str(project_dir / "tasks" / "verification_iter1.md"),
                "results": [
                    {
                        "command": "python -m unittest",
                        "returncode": 1,
                        "timed_out": False,
                        "missing_executable": False,
                        "stdout": "test failed",
                        "stderr": "AssertionError",
                    }
                ],
            }

            append_verification_issues(project_dir, 1, verification)
            report = (project_dir / "tasks" / "bug_report.md").read_text(encoding="utf-8")

        self.assertIn("## Issue 1", report)
        self.assertIn("python -m unittest", report)
        self.assertIn("AssertionError", report)
        self.assertIn("- **Type**: test", report)
        self.assertNotIn("- **Type**: verification", report)

    def test_appended_verification_issue_stays_before_existing_fix_log(self):
        with TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            tasks = project_dir / "tasks"
            tasks.mkdir()
            (tasks / "bug_report.md").write_text(
                "# Bug Report\n\n"
                "## Issue 1\n"
                "- **File**: app.py\n\n"
                "## Fix Log - Iteration 1\n\n"
                "## Issue 1\n"
                "- fixed app.py\n",
                encoding="utf-8",
            )
            verification = {
                "clean": False,
                "status": "failed",
                "report_path": str(tasks / "verification_iter2.md"),
                "results": [
                    {
                        "command": "python -m unittest",
                        "returncode": 1,
                        "timed_out": False,
                        "missing_executable": False,
                        "stdout": "",
                        "stderr": "AssertionError",
                    }
                ],
            }

            append_verification_issues(project_dir, 2, verification)
            report = (tasks / "bug_report.md").read_text(encoding="utf-8")
            active_report = report.split("## Fix Log", 1)[0]

        self.assertIn("## Issue 2", active_report)
        self.assertIn("## Deterministic Verification Failures", active_report)
        self.assertIn("## Fix Log - Iteration 1", report)

    def test_no_command_status_is_not_clean_when_required_and_appends_issue(self):
        runner = VerificationRunner(
            {
                "verification": {
                    "enabled": True,
                    "commands": [],
                    "require_commands_for_clean": True,
                }
            }
        )

        with TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            (project_dir / "tasks").mkdir()
            verification = runner.run(project_dir, 1)
            append_verification_issues(project_dir, 1, verification)
            report = (project_dir / "tasks" / "bug_report.md").read_text(encoding="utf-8")

        self.assertFalse(verification["clean"])
        self.assertEqual(verification["status"], "no_commands")
        self.assertIn("No deterministic verifier commands were detected", report)
        self.assertIn("- **Type**: test", report)

    def test_clean_no_command_status_still_appends_evidence_issue(self):
        runner = VerificationRunner(
            {
                "verification": {
                    "enabled": True,
                    "commands": [],
                    "require_commands_for_clean": False,
                }
            }
        )

        with TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            (project_dir / "tasks").mkdir()
            verification = runner.run(project_dir, 1)
            append_verification_issues(project_dir, 1, verification)
            report = (project_dir / "tasks" / "bug_report.md").read_text(encoding="utf-8")

        self.assertTrue(verification["clean"])
        self.assertEqual(verification["status"], "no_commands")
        self.assertIn("## Issue 1", report)
        self.assertIn("clean status requires verifier status `passed`", report)

    def test_disabled_verification_appends_evidence_issue(self):
        runner = VerificationRunner({"verification": {"enabled": False}})

        with TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            (project_dir / "tasks").mkdir()
            verification = runner.run(project_dir, 1)
            append_verification_issues(project_dir, 1, verification)
            report = (project_dir / "tasks" / "bug_report.md").read_text(encoding="utf-8")

        self.assertTrue(verification["clean"])
        self.assertEqual(verification["status"], "disabled")
        self.assertIn("## Issue 1", report)
        self.assertIn("Deterministic verification is disabled", report)

    def test_cli_returns_nonzero_for_clean_state_without_command_evidence(self):
        with TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            (project_dir / "tasks").mkdir()
            config_path = project_dir / "config.json"
            config_path.write_text(
                '{"verification": {"enabled": true, "commands": [], "require_commands_for_clean": false}}',
                encoding="utf-8",
            )

            exit_code = main(
                [
                    "--project-dir",
                    str(project_dir),
                    "--iteration",
                    "1",
                    "--config",
                    str(config_path),
                    "--append-bug-report",
                ]
            )
            report = (project_dir / "tasks" / "bug_report.md").read_text(encoding="utf-8")

        self.assertEqual(exit_code, 1)
        self.assertIn("No deterministic verifier commands were detected", report)

    def test_python_command_environment_is_sanitized(self):
        runner = VerificationRunner({"verification": {"enabled": True, "commands": []}})
        command = VerificationCommand(
            name="python",
            command="python -V",
            args=["python", "-V"],
            env_overrides={
                "PYTHONHOME": None,
                "UV_INTERNAL__PYTHONHOME": None,
                "PYTHONNOUSERSITE": "1",
            },
        )
        captured = {}

        def fake_run(*args, **kwargs):
            captured["env"] = kwargs["env"]
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"PYTHONHOME": "bad", "UV_INTERNAL__PYTHONHOME": "bad"}):
                with patch("verifier.subprocess.run", side_effect=fake_run):
                    runner._run_command(Path(tmp), command)

        self.assertNotIn("PYTHONHOME", captured["env"])
        self.assertNotIn("UV_INTERNAL__PYTHONHOME", captured["env"])
        self.assertEqual(captured["env"]["PYTHONNOUSERSITE"], "1")


    def test_futures_bot_external_target_gets_broad_verification_suite(self):
        runner = self._runner_with_python()

        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            workspace = base / "workspace"
            target = base / "futures-bot-v2"
            (workspace / "tasks").mkdir(parents=True)
            for relative in [
                "app",
                "broker",
                "execution",
                "intelligence",
                "persistence",
                "runtime",
                "watchlist",
                "zone_engine",
                "tests",
            ]:
                (target / relative).mkdir(parents=True)
            (workspace / "tasks" / "requirements.md").write_text(
                f"Update external target {target}",
                encoding="utf-8",
            )

            commands = runner._commands_for(workspace)

        names = [command.name for command in commands]
        self.assertIn("external futures-bot-v2 pytest", names)
        self.assertIn("external futures-bot-v2 guardrails", names)
        self.assertIn("external futures-bot-v2 sim smoke", names)
        self.assertIn("external futures-bot-v2 sim readiness", names)
        self.assertIn("external futures-bot-v2 healthcheck", names)
        self.assertIn("external futures-bot-v2 compileall", names)
        self.assertTrue(all(command.cwd and command.cwd.endswith("futures-bot-v2") for command in commands))

    def _runner_with_python(self) -> VerificationRunner:
        runner = VerificationRunner({"verification": {"enabled": True, "commands": []}})
        runner._resolve_python = lambda project_dir: PythonCandidate("python", ["python"])
        return runner


if __name__ == "__main__":
    unittest.main()
