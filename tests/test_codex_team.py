from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parent.parent


class CodexTeamScriptTests(unittest.TestCase):
    def test_clean_status_requires_successful_verifier_exit(self):
        script = (ROOT / "codex-team.ps1").read_text(encoding="utf-8")

        self.assertIn("function Add-VerifierProcessIssue", script)
        self.assertIn("function Test-VerifierIssuePresent", script)
        self.assertIn("($lastVerifier.ExitCode -eq 0) -and (Test-BugReportClean -BugReportPath $bugReportPath)", script)
        self.assertIn("($lastVerifier.ExitCode -ne 0) -and (-not (Test-VerifierIssuePresent -BugReportPath $bugReportPath))", script)
        self.assertNotIn("if ((Test-Path $bugReportPath) -and ((Get-Content -LiteralPath $bugReportPath -Raw).Trim() -eq \"NO_ISSUES_FOUND\"))", script)

    def test_clean_status_requires_artifact_and_verifier_command_evidence(self):
        script = (ROOT / "codex-team.ps1").read_text(encoding="utf-8")

        self.assertIn("function Test-CleanEvidence", script)
        self.assertIn("function Test-VerifierReportHasPassedCommand", script)
        self.assertIn("function Add-CleanEvidenceIssue", script)
        self.assertIn('"plan.md", "todo.md", "coder_report.md"', script)
        self.assertIn("bug_report_iter$Iteration.md", script)
        self.assertIn("- \\*\\*Commands\\*\\*: [1-9][0-9]*", script)
        self.assertIn("- \\*\\*Return code\\*\\*: 0", script)
        self.assertIn("- \\*\\*Timed out\\*\\*: false", script)
        self.assertIn("-and $cleanEvidence", script)
        self.assertIn("Clean status is missing required evidence", script)

    def test_summary_includes_tester_status_and_verifier_command_summary(self):
        script = (ROOT / "codex-team.ps1").read_text(encoding="utf-8")

        self.assertIn("function Get-VerifierCommandSummary", script)
        self.assertIn("- Tester status: $lastTesterStatus", script)
        self.assertIn("- Tester report: $lastTesterReport", script)
        self.assertIn("Verifier command summary:", script)
        self.assertIn("$verifierCommandSummary = Get-VerifierCommandSummary -ReportPath $verifierReport", script)

    def test_open_issue_count_ignores_fix_log_sections(self):
        script = (ROOT / "codex-team.ps1").read_text(encoding="utf-8")

        self.assertIn("function Remove-FixLogSections", script)
        self.assertIn("$activeText = Remove-FixLogSections -Text $text", script)
        self.assertIn("$activeExisting = Remove-FixLogSections -Text $existing", script)


if __name__ == "__main__":
    unittest.main()
