param([ValidateRange(1024, 65535)][int]$Port = 8000)
$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Split-Path $PSScriptRoot -Parent)).Path
$stateFile = Join-Path $projectRoot ".demo/server-$Port.json"
if (-not (Test-Path -LiteralPath $stateFile)) { Write-Host "No managed demo at port $Port."; return }
$state = Get-Content -LiteralPath $stateFile -Raw | ConvertFrom-Json
if ($state.root -ne $projectRoot -or $state.port -ne $Port) { throw "Demo process metadata does not match this workspace." }
$server = Get-Process -Id $state.pid -ErrorAction SilentlyContinue
if ($server) {
    # PowerShell 7 may deserialize ISO strings into DateTime already; Parse() would
    # stringify that value and lose fractional seconds before comparing it.
    $expectedStart = ([DateTime]$state.started_at_utc).ToUniversalTime()
    if ($server.StartTime.ToUniversalTime() -ne $expectedStart) { throw "PID was reused; refusing to stop an unrelated process." }
    Stop-Process -InputObject $server
    $server.WaitForExit(10000) | Out-Null
}
Remove-Item -LiteralPath $stateFile
Write-Host "Stopped managed demo at port $Port. PostgreSQL and stored demo data are preserved."
