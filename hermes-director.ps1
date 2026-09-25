param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$HermesArgs
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

Write-Host "[hermes-director] Starting Hermes in $Root" -ForegroundColor Cyan
Write-Host "[hermes-director] Director context: AGENTS.md + multi-agent-dev-system-director skill" -ForegroundColor DarkCyan

$skillArgs = @("-s", "multi-agent-dev-system-director")
if ($HermesArgs) {
    & hermes @skillArgs @HermesArgs
} else {
    & hermes @skillArgs
}
exit $LASTEXITCODE
