param([string]$Python = "python", [switch]$E2EOnly)
$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Split-Path $PSScriptRoot -Parent)).Path
$names = @("APP_ENV", "TEST_POSTGRES", "E2E_REPORT_DIR")
$previous = @{}
foreach ($name in $names) { $previous[$name] = [Environment]::GetEnvironmentVariable($name, "Process") }
function Check-Native([string]$Step) { if ($LASTEXITCODE -ne 0) { throw "$Step failed (exit $LASTEXITCODE)" } }
Push-Location $projectRoot
try {
    $env:APP_ENV = "test"
    $env:TEST_POSTGRES = "1"
    $env:E2E_REPORT_DIR = Join-Path $projectRoot ".demo/reports"
    docker compose up -d --wait --wait-timeout 60 db
    Check-Native "PostgreSQL startup"
    & $Python -m alembic -c backend/alembic.ini upgrade head
    Check-Native "Test database migration"
    & $Python -m ruff check .
    Check-Native "Lint"
    $testPath = if ($E2EOnly) { "backend/tests/e2e" } else { "backend/tests" }
    $testTemp = Join-Path $projectRoot (".pytest_cache/demo-" + [Guid]::NewGuid().ToString("N"))
    & $Python -m pytest $testPath --basetemp $testTemp --tb=short -o faulthandler_timeout=30
    Check-Native "PostgreSQL tests"
    Write-Host "Verification passed. HTTP E2E reports: .demo/reports/e2e-mock.json and e2e-local.json"
} finally {
    foreach ($name in $names) { [Environment]::SetEnvironmentVariable($name, $previous[$name], "Process") }
    Pop-Location
}
