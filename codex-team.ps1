param(
    [Parameter(Position = 0)]
    [string]$Requirements,

    [string]$RequirementsFile,
    [string]$Name,
    [int]$MaxIterations = 5,
    [string]$WorkspaceRoot = "workspace",
    [string]$Model = "gpt-5.5",
    [switch]$NoBypass
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$TasksLessons = Join-Path $Root "tasks\lessons.md"
$StandardsPath = Join-Path $Root "CLAUDE.md"

function Write-Step {
    param([string]$Message)
    Write-Host "[codex-team] $Message" -ForegroundColor Cyan
}

function Resolve-CodexCommand {
    $cmd = Get-Command codex -ErrorAction SilentlyContinue
    if ($cmd) {
        return $cmd.Source
    }

    $known = @(
        (Join-Path $env:APPDATA "npm\codex.cmd"),
        (Join-Path $env:USERPROFILE ".vscode\extensions\openai.chatgpt-26.519.32039-win32-x64\bin\windows-x86_64\codex.exe")
    )

    foreach ($candidate in $known) {
        if (Test-Path $candidate) {
            return $candidate
        }
    }

    throw "Could not find Codex CLI. Install it or make sure codex --version works in PowerShell."
}

function Get-PythonTailArgs {
    param([string[]]$PythonCommand)
    if ($PythonCommand.Count -le 1) {
        return @()
    }
    return $PythonCommand[1..($PythonCommand.Count - 1)]
}

function Test-PythonCommand {
    param([string[]]$PythonCommand)
    $env:PYTHONHOME = $null
    $env:UV_INTERNAL__PYTHONHOME = $null
    $env:PYTHONNOUSERSITE = "1"

    $exe = $PythonCommand[0]
    $tail = Get-PythonTailArgs -PythonCommand $PythonCommand
    & $exe @tail -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >$null 2>&1
    return $LASTEXITCODE -eq 0
}

function Resolve-PythonCommand {
    $candidates = New-Object System.Collections.Generic.List[object]
    $venvPython = Join-Path $Root ".venv\Scripts\python.exe"
    if (Test-Path $venvPython) {
        $candidates.Add(@($venvPython)) | Out-Null
    }
    $candidates.Add(@("py", "-3.12")) | Out-Null
    $candidates.Add(@("py", "-3.11")) | Out-Null
    $candidates.Add(@("py", "-3")) | Out-Null
    $candidates.Add(@("python")) | Out-Null
    $candidates.Add(@("python3")) | Out-Null

    foreach ($candidate in $candidates) {
        $exe = $candidate[0]
        if (-not (Test-Path $exe) -and -not (Get-Command $exe -ErrorAction SilentlyContinue)) {
            continue
        }
        if (Test-PythonCommand -PythonCommand $candidate) {
            return [string[]]$candidate
        }
    }

    throw "Could not find a valid Python 3.10+ command for deterministic verification."
}

function Invoke-PythonCommand {
    param(
        [string[]]$PythonCommand,
        [string[]]$Arguments
    )
    $env:PYTHONHOME = $null
    $env:UV_INTERNAL__PYTHONHOME = $null
    $env:PYTHONNOUSERSITE = "1"

    $exe = $PythonCommand[0]
    $tail = Get-PythonTailArgs -PythonCommand $PythonCommand
    & $exe @tail @Arguments
    return $LASTEXITCODE
}

function Invoke-DeterministicVerifier {
    param(
        [int]$Iteration,
        [string]$ProjectDir,
        [string[]]$PythonCommand
    )
    $verifierPath = Join-Path $Root "verifier.py"
    $configPath = Join-Path $Root "config.json"
    $arguments = @(
        $verifierPath,
        "--project-dir", $ProjectDir,
        "--iteration", [string]$Iteration,
        "--label", "codex-team",
        "--config", $configPath,
        "--append-bug-report"
    )

    Write-Step "Verifier Iteration $Iteration"
    $exitCode = Invoke-PythonCommand -PythonCommand $PythonCommand -Arguments $arguments
    $reportPath = Join-Path $ProjectDir "tasks\verification_iter$Iteration.md"
    $status = if ($exitCode -eq 0) { "passed" } else { "failed" }
    return @{
        Status = $status
        ExitCode = $exitCode
        ReportPath = $reportPath
    }
}

function Remove-FixLogSections {
    param([string]$Text)
    if (-not $Text) {
        return ""
    }
    $match = [regex]::Match($Text, "(?m)^##\s+Fix Log\b.*$")
    if ($match.Success) {
        return $Text.Substring(0, $match.Index)
    }
    return $Text
}

function Get-OpenIssueCount {
    param([string]$BugReportPath)
    if (-not (Test-Path $BugReportPath)) {
        return 1
    }
    $text = Get-Content -LiteralPath $BugReportPath -Raw
    if ($text.Trim() -eq "NO_ISSUES_FOUND") {
        return 0
    }
    $activeText = Remove-FixLogSections -Text $text
    $count = [regex]::Matches($activeText, "(?m)^##\s+Issue\s+\d+\b").Count
    if ($count -eq 0) {
        return 1
    }
    return $count
}

function Test-BugReportClean {
    param([string]$BugReportPath)
    if (-not (Test-Path $BugReportPath)) {
        return $false
    }
    return ((Get-Content -LiteralPath $BugReportPath -Raw).Trim() -eq "NO_ISSUES_FOUND")
}

function Test-NonEmptyFile {
    param([string]$Path)
    if (-not $Path -or -not (Test-Path -LiteralPath $Path)) {
        return $false
    }
    return -not [string]::IsNullOrWhiteSpace((Get-Content -LiteralPath $Path -Raw))
}

function Test-VerifierReportHasPassedCommand {
    param([string]$ReportPath)
    if (-not (Test-NonEmptyFile -Path $ReportPath)) {
        return $false
    }

    $text = Get-Content -LiteralPath $ReportPath -Raw
    if ($text -notmatch "(?m)^- \*\*Status\*\*: passed\s*$") {
        return $false
    }
    if ($text -notmatch "(?m)^- \*\*Commands\*\*: [1-9][0-9]*\s*$") {
        return $false
    }
    return ($text -match "(?ms)^##\s+[^\r\n]+.*?- \*\*Status\*\*: passed\s*.*?- \*\*Return code\*\*: 0\s*.*?- \*\*Timed out\*\*: false\s*")
}

function Test-CleanEvidence {
    param(
        [string]$TasksDir,
        [string]$BugReportPath,
        [int]$Iteration,
        [hashtable]$VerifierResult
    )

    foreach ($name in @("plan.md", "todo.md", "coder_report.md")) {
        if (-not (Test-NonEmptyFile -Path (Join-Path $TasksDir $name))) {
            return $false
        }
    }

    $iterReportPath = Join-Path $TasksDir "bug_report_iter$Iteration.md"
    if (-not (Test-BugReportClean -BugReportPath $BugReportPath)) {
        return $false
    }
    if (-not (Test-BugReportClean -BugReportPath $iterReportPath)) {
        return $false
    }
    if (-not $VerifierResult -or ($VerifierResult.ExitCode -ne 0)) {
        return $false
    }
    return (Test-VerifierReportHasPassedCommand -ReportPath $VerifierResult.ReportPath)
}

function Test-VerifierIssuePresent {
    param([string]$BugReportPath)
    if (-not (Test-Path $BugReportPath)) {
        return $false
    }
    $text = Remove-FixLogSections -Text (Get-Content -LiteralPath $BugReportPath -Raw)
    return (
        $text -match "(?m)^##\s+Deterministic Verification Failures\b" -or
        $text -match "Deterministic verifier process exited" -or
        $text -match "Clean status is missing required evidence"
    )
}

function Add-VerifierProcessIssue {
    param(
        [string]$BugReportPath,
        [int]$Iteration,
        [hashtable]$VerifierResult
    )

    $existing = if (Test-Path $BugReportPath) { Get-Content -LiteralPath $BugReportPath -Raw } else { "" }
    if ([string]::IsNullOrWhiteSpace($existing) -or ($existing.Trim() -eq "NO_ISSUES_FOUND")) {
        $existing = "# Bug Report - Iteration $Iteration`r`n"
    }

    $activeExisting = Remove-FixLogSections -Text $existing
    $issueNumber = [regex]::Matches($activeExisting, "(?m)^##\s+Issue\s+\d+\b").Count + 1
    $reportPath = $VerifierResult["ReportPath"]
    $exitCode = $VerifierResult["ExitCode"]
    $lines = @(
        $existing.TrimEnd(),
        "",
        "## Issue $issueNumber",
        "- **File**: project verification",
        "- **Line**: n/a",
        "- **Severity**: high",
        "- **Type**: test",
        "- **Description**: Deterministic verifier process exited with code $exitCode before a clean verification result was available.",
        "- **Suggested Fix**: Inspect $reportPath and fix the verifier startup, configuration, or command failure. Clean status requires verifier exit code 0.",
        ""
    ) -join [Environment]::NewLine

    Set-Content -LiteralPath $BugReportPath -Value $lines -Encoding UTF8
    $iterReportPath = Join-Path (Split-Path -Parent $BugReportPath) "bug_report_iter$Iteration.md"
    Copy-Item -LiteralPath $BugReportPath -Destination $iterReportPath -Force
}

function Add-CleanEvidenceIssue {
    param(
        [string]$BugReportPath,
        [int]$Iteration,
        [hashtable]$VerifierResult
    )

    $existing = if (Test-Path $BugReportPath) { Get-Content -LiteralPath $BugReportPath -Raw } else { "" }
    if ([string]::IsNullOrWhiteSpace($existing) -or ($existing.Trim() -eq "NO_ISSUES_FOUND")) {
        $existing = "# Bug Report - Iteration $Iteration`r`n"
    }

    $activeExisting = Remove-FixLogSections -Text $existing
    $issueNumber = [regex]::Matches($activeExisting, "(?m)^##\s+Issue\s+\d+\b").Count + 1
    $reportPath = if ($VerifierResult) { $VerifierResult["ReportPath"] } else { "tasks\verification_iter$Iteration.md" }
    $lines = @(
        $existing.TrimEnd(),
        "",
        "## Issue $issueNumber",
        "- **File**: pipeline clean gate",
        "- **Line**: n/a",
        "- **Severity**: high",
        "- **Type**: test",
        "- **Description**: Clean status is missing required evidence. Clean requires nonempty plan, TODO, coder report, clean per-iteration bug report, verifier report, and at least one passed verifier command result.",
        "- **Suggested Fix**: Produce the missing artifact or configure/run deterministic verification so $reportPath records a passed command result.",
        ""
    ) -join [Environment]::NewLine

    Set-Content -LiteralPath $BugReportPath -Value $lines -Encoding UTF8
    $iterReportPath = Join-Path (Split-Path -Parent $BugReportPath) "bug_report_iter$Iteration.md"
    Copy-Item -LiteralPath $BugReportPath -Destination $iterReportPath -Force
}

function Get-VerifierCommandSummary {
    param([string]$ReportPath)
    if (-not $ReportPath -or -not (Test-Path $ReportPath)) {
        return @("- No verifier report was recorded.")
    }

    $text = Get-Content -LiteralPath $ReportPath -Raw
    if ($text -match "Verification is disabled in config\.") {
        return @("- Verification disabled in config.")
    }
    if ($text -match "No deterministic verifier commands were detected\.") {
        return @("- No deterministic verifier commands were detected.")
    }

    $pattern = '(?ms)^##\s+(?<name>[^\r\n]+).*?- \*\*Status\*\*: (?<status>[^\r\n]+).*?- \*\*Command\*\*: `(?<command>[^`]+)`.*?- \*\*Return code\*\*: (?<returncode>[^\r\n]+).*?- \*\*Timed out\*\*: (?<timeout>[^\r\n]+)'
    $matches = [regex]::Matches($text, $pattern)
    if ($matches.Count -eq 0) {
        return @("- No command results were recorded.")
    }

    $lines = New-Object System.Collections.Generic.List[string]
    foreach ($match in $matches) {
        $command = $match.Groups["command"].Value
        $status = $match.Groups["status"].Value.Trim()
        $returnCode = $match.Groups["returncode"].Value.Trim()
        $timedOut = $match.Groups["timeout"].Value.Trim()
        $lines.Add("- ``$command`` -> $status (rc=$returnCode, timeout=$timedOut)") | Out-Null
    }
    return [string[]]$lines
}

function Get-RequirementsText {
    if ($RequirementsFile) {
        return (Get-Content -LiteralPath $RequirementsFile -Raw).Trim()
    }
    if ($Requirements) {
        return $Requirements.Trim()
    }

    Write-Host "Enter project requirements. End with a line containing only END."
    $lines = New-Object System.Collections.Generic.List[string]
    while ($true) {
        $line = Read-Host
        if ($line.Trim() -eq "END") {
            break
        }
        $lines.Add($line)
    }
    return ($lines -join [Environment]::NewLine).Trim()
}

function New-Slug {
    param([string]$Text)
    $slug = ($Text.ToLowerInvariant() -replace "[^a-z0-9]+", "-" -replace "^-|-$", "")
    if ($slug.Length -gt 42) {
        $slug = $slug.Substring(0, 42).Trim("-")
    }
    if (-not $slug) {
        $slug = "codex-project"
    }
    return $slug
}

function Invoke-CodexStage {
    param(
        [string]$Stage,
        [string]$Prompt,
        [string]$ProjectDir,
        [string]$CodexCmd
    )

    $promptDir = Join-Path $ProjectDir "tasks\.codex_prompts"
    New-Item -ItemType Directory -Path $promptDir -Force | Out-Null

    $safeStage = $Stage.ToLowerInvariant() -replace "[^a-z0-9]+", "-"
    $promptPath = Join-Path $promptDir "$safeStage.md"
    $outPath = Join-Path $ProjectDir "tasks\codex_${safeStage}_last_message.md"
    Set-Content -LiteralPath $promptPath -Value $Prompt -Encoding UTF8

    $args = @("exec", "--skip-git-repo-check", "-C", $ProjectDir, "-o", $outPath)
    if (-not $NoBypass) {
        $args += "--dangerously-bypass-approvals-and-sandbox"
    }
    if ($Model) {
        $args += @("-m", $Model)
    }
    $args += "-"

    Write-Step "$Stage"
    Get-Content -LiteralPath $promptPath -Raw | & $CodexCmd @args
    if ($LASTEXITCODE -ne 0) {
        throw "Codex stage '$Stage' failed with exit code $LASTEXITCODE. Last prompt: $promptPath"
    }
}

function Get-UnhandledGapReports {
    param([string]$ProjectDir)

    $gapDir = Join-Path $ProjectDir "tasks\gap_reports"
    if (-not (Test-Path $gapDir)) {
        return @()
    }

    $handledPath = Join-Path $gapDir ".handled"
    $handled = @{}
    if (Test-Path $handledPath) {
        Get-Content -LiteralPath $handledPath | ForEach-Object {
            $line = $_.Trim()
            if ($line) {
                $handled[$line] = $true
            }
        }
    }

    return @(Get-ChildItem -LiteralPath $gapDir -Filter "*.md" -File |
        Sort-Object FullName |
        Where-Object { -not $handled.ContainsKey($_.FullName) })
}

function Mark-GapReportsHandled {
    param(
        [string]$ProjectDir,
        [object[]]$Reports
    )

    if (-not $Reports -or $Reports.Count -eq 0) {
        return
    }

    $gapDir = Join-Path $ProjectDir "tasks\gap_reports"
    New-Item -ItemType Directory -Path $gapDir -Force | Out-Null
    $handledPath = Join-Path $gapDir ".handled"
    $lines = $Reports | ForEach-Object { $_.FullName }
    Add-Content -LiteralPath $handledPath -Value $lines -Encoding UTF8
}

function Invoke-WeaknessRouter {
    param(
        [string]$Stage,
        [string]$ProjectDir,
        [string]$CodexCmd,
        [string]$SharedContext
    )

    $reports = @(Get-UnhandledGapReports -ProjectDir $ProjectDir)
    if ($reports.Count -eq 0) {
        return
    }

    New-Item -ItemType Directory -Path (Join-Path $ProjectDir "tasks\temporary_agents") -Force | Out-Null

    $reportBlocks = @()
    foreach ($report in $reports) {
        $body = Get-Content -LiteralPath $report.FullName -Raw
        $reportBlocks += @"
## $($report.Name)

Path: $($report.FullName)

$body
"@
    }
    $reportText = $reportBlocks -join "`n`n---`n`n"

    $routerPrompt = @"
You are the Weakness Router in a Codex-only multi-agent development pipeline.

$SharedContext

# Current Phase

$Stage

# Unhandled Gap Reports

$reportText

# Task

Route each gap to the smallest reliable intervention:

- prompt_instruction for underspecified behavior
- checklist for procedural risk
- test for mechanically verifiable behavior
- tool for missing capability or missing current facts
- reviewer for high-risk or subjective changes
- temporary_specialist only when domain judgment is needed
- no_action when the gap is low-signal or already covered

Temporary agents only. Do not create or edit persistent agent definitions.

If a temporary specialist is warranted, instantiate it as a one-off constrained role inside this stage. Write its spec and result to tasks/temporary_agents/<slug>.md.

Write or append tasks/weakness_router_log.md with each gap, selected intervention, reason, artifacts, and outcome.

If the route creates actionable repair work, write it to tasks/bug_report.md using tester issue format so the Bug Fixer can repair it automatically. If tasks/bug_report.md already contains issues, append the router findings instead of replacing them.

You may update coordination artifacts, focused checklists, and focused tests when the chosen intervention requires it. Do not broaden product scope.
"@

    Invoke-CodexStage -Stage "Weakness Router After $Stage" -Prompt $routerPrompt -ProjectDir $ProjectDir -CodexCmd $CodexCmd
    Mark-GapReportsHandled -ProjectDir $ProjectDir -Reports $reports
}

$CodexCmd = Resolve-CodexCommand
& $CodexCmd --version
$PythonCommand = Resolve-PythonCommand

$requirementsText = Get-RequirementsText
if (-not $requirementsText) {
    throw "No requirements provided."
}

if (-not $Name) {
    $Name = New-Slug $requirementsText
}

$Workspace = Join-Path $Root $WorkspaceRoot
$ProjectDir = Join-Path $Workspace $Name
$TasksDir = Join-Path $ProjectDir "tasks"
$LogsDir = Join-Path $ProjectDir "logs"
New-Item -ItemType Directory -Path $TasksDir -Force | Out-Null
New-Item -ItemType Directory -Path $LogsDir -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $TasksDir "gap_reports") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $TasksDir "temporary_agents") -Force | Out-Null

Set-Content -LiteralPath (Join-Path $TasksDir "requirements.md") -Value $requirementsText -Encoding UTF8

$standards = Get-Content -LiteralPath $StandardsPath -Raw
$lessons = if (Test-Path $TasksLessons) { Get-Content -LiteralPath $TasksLessons -Raw } else { "" }

$sharedContext = @"
# Shared Standards

$standards

# Lessons Learned

$lessons

# Workspace Rules

- You are operating inside the project directory: $ProjectDir
- Keep all project artifacts under this directory.
- The tasks/ directory is reserved for coordination artifacts.
- Do not ask the user questions during a stage. Make conservative assumptions and record them in the relevant task artifact.
- Prefer small, boring, testable implementations over speculative architecture.
- If you detect a weakness that your stage cannot confidently resolve, write a structured gap report under tasks/gap_reports/.
- A gap report must include observed weakness, evidence, risk if ignored, affected area, confidence, recommended response, needed context, and exit condition.
- The weakness router will choose the smallest intervention: prompt instruction, checklist, deterministic test, tool/search step, reviewer step, temporary specialist, or no action.
- Temporary specialists are one-off only and must write results under tasks/temporary_agents/.
- Do not create or edit persistent agent definitions.
- Before completing a stage, review tasks/weakness_router_log.md and tasks/temporary_agents/ if they exist and are relevant to your work.

# Subagent Spawning Policy

- You may spawn helper agents when useful for parallel read-only investigation, focused testing, security review, frontend review, backend review, docs review, or other bounded audit work.
- When spawning a full-history or forked helper agent, omit agent_type, model, and reasoning_effort. Full-history forked helpers inherit those values from the parent.
- Only pass the task instructions and scope needed by the helper.
- If a specialized agent_type, model, or reasoning_effort is required, do not use a full-history fork. Spawn with a self-contained prompt and only the specific context needed.
- Helper agents should normally be read-only unless explicitly assigned a fix task. The parent stage remains responsible for coordinating findings, applying final edits, and running verification.
"@

$architectPrompt = @"
You are the Architect agent in a Codex-only multi-agent development pipeline.

$sharedContext

# Requirements

$requirementsText

# Task

Create exactly these two planning artifacts:

1. tasks/plan.md
2. tasks/todo.md

`tasks/plan.md` must include goal, requirements traceability, assumptions, non-goals, stack choice, file tree, file-by-file specifications, dependencies, exact verification commands, acceptance criteria, and test strategy.

`tasks/todo.md` must be an ordered checklist of atomic items using `- [ ]`. Setup and scaffolding items come first. The coder agent must be able to implement from this without asking follow-up questions, and TODOs must remain unchecked until implementation and verification are complete.

Do not implement application code in this stage.
"@

Invoke-CodexStage -Stage "Architect" -Prompt $architectPrompt -ProjectDir $ProjectDir -CodexCmd $CodexCmd
Invoke-WeaknessRouter -Stage "Architect" -ProjectDir $ProjectDir -CodexCmd $CodexCmd -SharedContext $sharedContext

$coderPrompt = @"
You are the Coder agent in a Codex-only multi-agent development pipeline.

$sharedContext

# Task

Read:

- tasks/requirements.md
- tasks/plan.md
- tasks/todo.md

Implement every checklist item. Create and edit the project files directly in this directory. Keep coordination notes in tasks/.

As you complete items, update tasks/todo.md from - [ ] to - [x].

When finished, write tasks/coder_report.md with:

- files created or changed
- tests or checks you ran with exact command results
- any assumptions or deviations from the plan
- blockers, skipped checks, missing dependencies, or incomplete TODOs left unchecked
"@

Invoke-CodexStage -Stage "Coder" -Prompt $coderPrompt -ProjectDir $ProjectDir -CodexCmd $CodexCmd
Invoke-WeaknessRouter -Stage "Coder" -ProjectDir $ProjectDir -CodexCmd $CodexCmd -SharedContext $sharedContext

$finalStatus = "max_iterations_reached"
$lastVerifier = $null
$lastTesterStatus = "not_run"
$lastTesterReport = "n/a"
for ($iteration = 1; $iteration -le $MaxIterations; $iteration++) {
    $testerPrompt = @"
You are the Tester agent in a Codex-only multi-agent development pipeline.

$sharedContext

# Review Iteration

$iteration

# Task

Review the current project against:

- `tasks/requirements.md`
- `tasks/plan.md`
- `tasks/todo.md`
- `tasks/coder_report.md`
- the current code
- the prior tasks/bug_report.md, if it exists

Look for real bugs, security flaws, broken edge cases, missing acceptance criteria, performance problems, spec drift, TODO dishonesty, missing coder-report evidence, and missing verification evidence. Audit prior fix sites and adjacent caller semantics for newly introduced defects before clearing or re-flagging an issue.

If there are no issues, write exactly this to tasks/bug_report.md:

NO_ISSUES_FOUND

Otherwise write tasks/bug_report.md in this format:

# Bug Report - Iteration $iteration

## Issue 1
- **File**: relative/path
- **Line**: line or range, or unknown
- **Severity**: critical | high | medium | low
- **Type**: bug | security | performance | structural | spec | test
- **Description**: specific defect
- **Suggested Fix**: minimal corrective action

Also copy the same report to tasks/bug_report_iter$iteration.md.
"@

    Invoke-CodexStage -Stage "Tester Iteration $iteration" -Prompt $testerPrompt -ProjectDir $ProjectDir -CodexCmd $CodexCmd
    Invoke-WeaknessRouter -Stage "Tester Iteration $iteration" -ProjectDir $ProjectDir -CodexCmd $CodexCmd -SharedContext $sharedContext

    $bugReportPath = Join-Path $TasksDir "bug_report.md"
    $lastTesterReport = Join-Path $TasksDir "bug_report_iter$iteration.md"
    if (Test-BugReportClean -BugReportPath $bugReportPath) {
        $lastTesterStatus = "clean"
    } elseif (Test-Path $bugReportPath) {
        $lastTesterStatus = "issues_found"
    } else {
        $lastTesterStatus = "missing_report"
    }
    $lastVerifier = Invoke-DeterministicVerifier -Iteration $iteration -ProjectDir $ProjectDir -PythonCommand $PythonCommand
    if (($lastVerifier.ExitCode -ne 0) -and (-not (Test-VerifierIssuePresent -BugReportPath $bugReportPath))) {
        Add-VerifierProcessIssue -BugReportPath $bugReportPath -Iteration $iteration -VerifierResult $lastVerifier
    }
    $cleanEvidence = Test-CleanEvidence -TasksDir $TasksDir -BugReportPath $bugReportPath -Iteration $iteration -VerifierResult $lastVerifier
    if (($lastVerifier.ExitCode -eq 0) -and (Test-BugReportClean -BugReportPath $bugReportPath) -and $cleanEvidence) {
        $finalStatus = "clean"
        break
    }
    if (($lastVerifier.ExitCode -eq 0) -and (Test-BugReportClean -BugReportPath $bugReportPath) -and (-not $cleanEvidence) -and (-not (Test-VerifierIssuePresent -BugReportPath $bugReportPath))) {
        Add-CleanEvidenceIssue -BugReportPath $bugReportPath -Iteration $iteration -VerifierResult $lastVerifier
    }

    if ($iteration -eq $MaxIterations) {
        break
    }

    $fixerPrompt = @"
You are the Bug Fixer agent in a Codex-only multi-agent development pipeline.

$sharedContext

# Fix Iteration

$iteration

# Task

Read `tasks/bug_report.md` and fix every listed issue with the smallest correct root-cause code changes. Treat deterministic verifier failures as actionable issues unless you prove an environment-only blocker. Verify with relevant deterministic checks where practical.

Append a section to tasks/bug_report.md:

## Fix Log - Iteration $iteration

For each issue, state the root cause, files changed, what changed, and how it was verified. If you believe an item is a false positive, explain the evidence clearly.
"@

    Invoke-CodexStage -Stage "Bug Fixer Iteration $iteration" -Prompt $fixerPrompt -ProjectDir $ProjectDir -CodexCmd $CodexCmd
    Invoke-WeaknessRouter -Stage "Bug Fixer Iteration $iteration" -ProjectDir $ProjectDir -CodexCmd $CodexCmd -SharedContext $sharedContext
}

$modelLabel = if ($Model) { $Model } else { "(default Codex config)" }
$bugReportPath = Join-Path $TasksDir "bug_report.md"
$openIssues = Get-OpenIssueCount -BugReportPath $bugReportPath
$verifierStatus = if ($lastVerifier) { $lastVerifier.Status } else { "not_run" }
$verifierExit = if ($lastVerifier) { $lastVerifier.ExitCode } else { "n/a" }
$verifierReport = if ($lastVerifier) { $lastVerifier.ReportPath } else { "n/a" }
$verifierEnvironmentBlocker = if (($lastVerifier) -and ($lastVerifier.ExitCode -ne 0)) {
    "Verifier did not pass; inspect $verifierReport for command or environment details."
} else {
    "None recorded."
}
$verifierCommandSummary = Get-VerifierCommandSummary -ReportPath $verifierReport
$summary = @(
    "# Codex Team Summary",
    "",
    "- Project: $Name",
    "- Directory: $ProjectDir",
    "- Status: $finalStatus",
    "- Open issues: $openIssues",
    "- Tester status: $lastTesterStatus",
    "- Tester report: $lastTesterReport",
    "- Verifier status: $verifierStatus",
    "- Verifier exit code: $verifierExit",
    "- Verifier report: $verifierReport",
    "- Verifier environment blocker: $verifierEnvironmentBlocker",
    "- Max iterations: $MaxIterations",
    "- Model override: $modelLabel",
    "- Finished: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')",
    "",
    "Verifier command summary:",
    "",
    $verifierCommandSummary,
    "",
    "Artifacts:",
    "",
    "- ``tasks/requirements.md``",
    "- ``tasks/plan.md``",
    "- ``tasks/todo.md``",
    "- ``tasks/coder_report.md``",
    "- ``tasks/bug_report.md``",
    "- ``tasks/gap_reports/``",
    "- ``tasks/temporary_agents/``",
    "- ``tasks/weakness_router_log.md``",
    "- ``tasks/verification_iterN.md``"
) -join [Environment]::NewLine

Set-Content -LiteralPath (Join-Path $TasksDir "summary.md") -Value $summary -Encoding UTF8

Write-Step "Complete"
Write-Host "Project: $ProjectDir"
Write-Host "Status:  $finalStatus"
Write-Host "Summary: $(Join-Path $TasksDir 'summary.md')"

if ($finalStatus -eq "clean") {
    exit 0
}
exit 1
