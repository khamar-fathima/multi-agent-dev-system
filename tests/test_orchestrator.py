from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from orchestrator import (
    build_summary_model,
    clean_iteration_artifacts_consistent,
    count_bug_report_issues,
    compute_review_fingerprint,
    final_iteration_clean,
    hash_requirements_text,
    invalidate_stale_state,
    reusable_resume_artifacts_exist,
    status_for_zero_iterations,
)


class OrchestratorAccuracyGateTests(unittest.TestCase):
    def test_final_status_requires_tester_verifier_and_coder_evidence(self):
        with TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            (project_dir / "tasks").mkdir()
            tester = {"clean": True}
            verifier = {
                "clean": True,
                "status": "passed",
                "results": [{"status": "passed", "returncode": 0, "timed_out": False}],
            }

            clean, blockers = final_iteration_clean(project_dir, tester, verifier)
            self.assertFalse(clean)
            self.assertIn("missing tasks/coder_report.md", blockers)

            (project_dir / "tasks" / "coder_report.md").write_text("changed files", encoding="utf-8")
            (project_dir / "tasks" / "bug_report.md").write_text("NO_ISSUES_FOUND\n", encoding="utf-8")
            clean, blockers = final_iteration_clean(project_dir, tester, verifier)

        self.assertTrue(clean)
        self.assertEqual(blockers, [])

    def test_final_status_requires_passed_verifier_command_evidence(self):
        with TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            tasks = project_dir / "tasks"
            tasks.mkdir()
            (tasks / "coder_report.md").write_text("done", encoding="utf-8")
            (tasks / "bug_report.md").write_text("NO_ISSUES_FOUND\n", encoding="utf-8")
            tester = {"clean": True}

            for verifier, expected in [
                ({"clean": True, "status": "disabled", "results": []}, "verifier status is disabled; expected passed"),
                ({"clean": True, "status": "no_commands", "results": []}, "verifier status is no_commands; expected passed"),
                ({"clean": True, "status": "passed", "results": []}, "verifier result has no passed command evidence"),
            ]:
                clean, blockers = final_iteration_clean(project_dir, tester, verifier)
                self.assertFalse(clean)
                self.assertIn(expected, blockers)

    def test_bug_report_issue_count_ignores_fix_log_issue_headings(self):
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

            count = count_bug_report_issues(project_dir)

        self.assertEqual(count, 1)

    def test_max_iterations_zero_is_not_clean(self):
        self.assertEqual(status_for_zero_iterations(0), "not_reviewed")
        self.assertEqual(status_for_zero_iterations(-1), "not_reviewed")
        self.assertIsNone(status_for_zero_iterations(1))

    def test_stale_requirements_hash_invalidates_saved_state(self):
        state = {
            "requirements_hash": hash_requirements_text("old"),
            "stages": {"coder": {"status": "completed"}},
            "iterations": {"1": {"status": "clean"}},
            "status": "clean",
        }

        stale = invalidate_stale_state(state, hash_requirements_text("new"))

        self.assertTrue(stale)
        self.assertEqual(state["stages"], {})
        self.assertEqual(state["iterations"], {})
        self.assertEqual(state["status"], "running")

    def test_missing_legacy_requirements_hash_with_artifacts_invalidates_state(self):
        with TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            tasks = project_dir / "tasks"
            tasks.mkdir()
            (tasks / "plan.md").write_text("old plan", encoding="utf-8")
            (tasks / "todo.md").write_text("- [x] old work", encoding="utf-8")
            (tasks / "coder_report.md").write_text("old coder report", encoding="utf-8")

            state = {
                "stages": {"architect": {"status": "completed"}, "coder": {"status": "completed"}},
                "iterations": {"1": {"status": "clean"}},
                "status": "clean",
            }

            stale = invalidate_stale_state(
                state,
                hash_requirements_text("new requirements"),
                has_reusable_artifacts=reusable_resume_artifacts_exist(project_dir),
            )

        self.assertTrue(stale)
        self.assertEqual(state["stages"], {})
        self.assertEqual(state["iterations"], {})
        self.assertEqual(state["status"], "running")
        self.assertTrue(state["stale_resume_detected"])

    def test_clean_resume_requires_current_reports(self):
        with TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            tasks = project_dir / "tasks"
            tasks.mkdir()
            (tasks / "coder_report.md").write_text("done", encoding="utf-8")
            (tasks / "bug_report.md").write_text("NO_ISSUES_FOUND\n", encoding="utf-8")
            state = {
                "tester": {"clean": True},
                "verification": {
                    "clean": True,
                    "status": "passed",
                    "report_path": str(tasks / "verification_iter1.md"),
                    "results": [{"status": "passed", "returncode": 0, "timed_out": False}],
                },
            }

            consistent, blockers = clean_iteration_artifacts_consistent(project_dir, state, 1)
            self.assertFalse(consistent)
            self.assertIn("missing tasks/bug_report_iter1.md", blockers)

            (tasks / "bug_report_iter1.md").write_text("NO_ISSUES_FOUND\n", encoding="utf-8")
            (tasks / "verification_iter1.md").write_text("passed", encoding="utf-8")
            state["review_fingerprint"] = compute_review_fingerprint(project_dir, 1)
            consistent, blockers = clean_iteration_artifacts_consistent(project_dir, state, 1)

        self.assertTrue(consistent)
        self.assertEqual(blockers, [])

    def test_clean_resume_rejects_changed_reviewed_code(self):
        with TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            tasks = project_dir / "tasks"
            tasks.mkdir()
            (project_dir / "app.py").write_text("print('old')\n", encoding="utf-8")
            (tasks / "requirements.md").write_text("same requirements", encoding="utf-8")
            (tasks / "plan.md").write_text("plan", encoding="utf-8")
            (tasks / "todo.md").write_text("- [x] done\n", encoding="utf-8")
            (tasks / "coder_report.md").write_text("done", encoding="utf-8")
            (tasks / "bug_report.md").write_text("NO_ISSUES_FOUND\n", encoding="utf-8")
            (tasks / "bug_report_iter1.md").write_text("NO_ISSUES_FOUND\n", encoding="utf-8")
            (tasks / "verification_iter1.md").write_text("passed", encoding="utf-8")
            state = {
                "tester": {"clean": True},
                "verification": {
                    "clean": True,
                    "status": "passed",
                    "report_path": str(tasks / "verification_iter1.md"),
                    "results": [{"status": "passed", "returncode": 0, "timed_out": False}],
                },
                "review_fingerprint": compute_review_fingerprint(project_dir, 1),
            }

            (project_dir / "app.py").write_text("print('new')\n", encoding="utf-8")
            consistent, blockers = clean_iteration_artifacts_consistent(project_dir, state, 1)

        self.assertFalse(consistent)
        self.assertIn("review fingerprint changed since clean iteration", blockers)

    def test_clean_resume_rejects_changed_large_reviewed_code(self):
        with TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            tasks = project_dir / "tasks"
            tasks.mkdir()
            large_file = project_dir / "large.py"
            large_file.write_text("# old\n" + ("x = 1\n" * 40_000), encoding="utf-8")
            (tasks / "requirements.md").write_text("same requirements", encoding="utf-8")
            (tasks / "plan.md").write_text("plan", encoding="utf-8")
            (tasks / "todo.md").write_text("- [x] done\n", encoding="utf-8")
            (tasks / "coder_report.md").write_text("done", encoding="utf-8")
            (tasks / "bug_report.md").write_text("NO_ISSUES_FOUND\n", encoding="utf-8")
            (tasks / "bug_report_iter1.md").write_text("NO_ISSUES_FOUND\n", encoding="utf-8")
            (tasks / "verification_iter1.md").write_text("passed", encoding="utf-8")
            state = {
                "tester": {"clean": True},
                "verification": {
                    "clean": True,
                    "status": "passed",
                    "report_path": str(tasks / "verification_iter1.md"),
                    "results": [{"status": "passed", "returncode": 0, "timed_out": False}],
                },
                "review_fingerprint": compute_review_fingerprint(project_dir, 1),
            }

            large_file.write_text("# new\n" + ("x = 1\n" * 40_000), encoding="utf-8")
            consistent, blockers = clean_iteration_artifacts_consistent(project_dir, state, 1)

        self.assertFalse(consistent)
        self.assertIn("review fingerprint changed since clean iteration", blockers)

    def test_summary_model_includes_verifier_status_and_open_issues(self):
        now = datetime(2026, 5, 28, 9, 0, 0)
        state = {
            "iterations": {
                "1": {
                    "tester": {"clean": False},
                    "verification": {
                        "clean": False,
                        "status": "failed",
                        "report_path": "tasks/verification_iter1.md",
                        "commands": [{"command": "python -m unittest"}],
                        "results": [
                            {
                                "command": "python -m unittest",
                                "status": "failed",
                                "returncode": 1,
                                "timed_out": False,
                            }
                        ],
                    },
                }
            }
        }

        summary = build_summary_model(
            name="proj",
            started=now,
            finished=now,
            status="max_iterations_reached",
            iterations_done=1,
            final_bug_count=2,
            coder_backend="codex",
            ledger_path=Path("runs/run.json"),
            run_state=state,
        )

        self.assertEqual(summary["latest_verifier_status"], "failed")
        self.assertEqual(summary["final_bug_count"], 2)
        self.assertEqual(summary["verification_commands"], ["python -m unittest"])


if __name__ == "__main__":
    unittest.main()
