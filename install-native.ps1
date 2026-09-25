param(
    [switch]$NoBackup
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$UserClaudeDir = Join-Path $env:USERPROFILE ".claude"
$UserAgentsDir = Join-Path $UserClaudeDir "agents"
$UserCommandsDir = Join-Path $UserClaudeDir "commands"
$ProjectClaudeDir = Join-Path $Root ".claude"
$ProjectAgentsDir = Join-Path $ProjectClaudeDir "agents"
$ProjectCommandsDir = Join-Path $ProjectClaudeDir "commands"
$NativeDir = Join-Path $Root "native"
$CodexCmd = Join-Path $env:APPDATA "npm\codex.cmd"

function Write-Step($Message) {
    Write-Host "[native-devteam] $Message" -ForegroundColor Cyan
}

function Copy-NativeFile {
    param(
        [string]$Source,
        [string]$Destination
    )

    if ((Test-Path $Destination) -and -not $NoBackup) {
        $timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
        Copy-Item -LiteralPath $Destination -Destination "$Destination.$timestamp.bak" -Force
    }

    $content = Get-Content -LiteralPath $Source -Raw
    $content = $content.Replace("{{DEVTEAM_ROOT}}", $Root)
    $content = $content.Replace("{{CODEX_CMD}}", $CodexCmd)
    Set-Content -LiteralPath $Destination -Value $content -Encoding UTF8
}

Write-Step "Checking Claude Code install directories"
New-Item -ItemType Directory -Path $UserAgentsDir -Force | Out-Null
New-Item -ItemType Directory -Path $UserCommandsDir -Force | Out-Null
New-Item -ItemType Directory -Path $ProjectAgentsDir -Force | Out-Null
New-Item -ItemType Directory -Path $ProjectCommandsDir -Force | Out-Null

Write-Step "Checking Node and Codex CLI"
if (-not (Test-Path "C:\Program Files\nodejs\node.exe")) {
    throw "Node was not found at C:\Program Files\nodejs\node.exe. Install Node LTS first."
}
if (-not (Test-Path $CodexCmd)) {
    throw "Codex CLI was not found at $CodexCmd. Install with: npm.cmd install -g @openai/codex"
}

$env:Path = "C:\Program Files\nodejs;$env:APPDATA\npm;" + $env:Path
& $CodexCmd --version | Write-Host

Write-Step "Installing native agents"
Get-ChildItem -LiteralPath (Join-Path $NativeDir "agents") -Filter "*.md" | ForEach-Object {
    Copy-NativeFile -Source $_.FullName -Destination (Join-Path $UserAgentsDir $_.Name)
    Copy-NativeFile -Source $_.FullName -Destination (Join-Path $ProjectAgentsDir $_.Name)
}

Write-Step "Installing slash commands"
Get-ChildItem -LiteralPath (Join-Path $NativeDir "commands") -Filter "*.md" | ForEach-Object {
    Copy-NativeFile -Source $_.FullName -Destination (Join-Path $UserCommandsDir $_.Name)
    Copy-NativeFile -Source $_.FullName -Destination (Join-Path $ProjectCommandsDir $_.Name)
}

Write-Step "Installed files"
Write-Host "User agents:    $UserAgentsDir"
Get-ChildItem -LiteralPath $UserAgentsDir -Filter "*.md" | Sort-Object Name | Select-Object Name
Write-Host "User commands:  $UserCommandsDir"
Get-ChildItem -LiteralPath $UserCommandsDir -Filter "*.md" | Sort-Object Name | Select-Object Name
Write-Host "Project agents: $ProjectAgentsDir"
Get-ChildItem -LiteralPath $ProjectAgentsDir -Filter "*.md" | Sort-Object Name | Select-Object Name
Write-Host "Project commands: $ProjectCommandsDir"
Get-ChildItem -LiteralPath $ProjectCommandsDir -Filter "*.md" | Sort-Object Name | Select-Object Name

Write-Step "Native dev team is installed for Claude Code"
