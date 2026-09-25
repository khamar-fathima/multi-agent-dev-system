param(
    [switch]$SkipDependencyInstall
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

function Write-Step($Message) {
    Write-Host "[multi-agent-dev-system] $Message" -ForegroundColor Cyan
}

function Find-Python {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        $version = & python --version 2>&1
        if ($LASTEXITCODE -eq 0 -and "$version" -notmatch "Microsoft Store") {
            return "python"
        }
    }

    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        & py -3 --version | Out-Null
        if ($LASTEXITCODE -eq 0) {
            return "py -3"
        }
    }

    return $null
}

Write-Step "Checking Python"
$Python = Find-Python
if (-not $Python) {
    throw "Python 3.10+ was not found. Install with: winget install Python.Python.3.12"
}
Write-Host "  Python: $Python"

Write-Step "Checking API environment"
if (-not $env:ANTHROPIC_API_KEY) {
    Write-Host "  ANTHROPIC_API_KEY is not set. The pipeline will need it before running." -ForegroundColor Yellow
}
if (-not $env:OPENAI_API_KEY) {
    Write-Host "  OPENAI_API_KEY is not set. Codex CLI mode can still work if Codex is logged in." -ForegroundColor Yellow
}

Write-Step "Checking Codex CLI"
$Codex = Get-Command codex -ErrorAction SilentlyContinue
if ($Codex) {
    Write-Host "  Codex: $($Codex.Source)"
} else {
    Write-Host "  Codex CLI not found on PATH. The runner will fall back to OpenAI API mode when configured for auto." -ForegroundColor Yellow
}

if (-not $SkipDependencyInstall) {
    Write-Step "Installing Python dependencies"
    Invoke-Expression "$Python -m pip install -r requirements.txt"
}

Write-Step "Running unit tests"
Invoke-Expression "$Python -m unittest discover -s tests"

Write-Step "Install check complete"
