param(
    [ValidateSet("local", "mock")][string]$Provider = "local",
    [ValidateSet("", "local", "mock", "openai")][string]$RecommendationProvider = "",
    [string]$Python = "python",
    [ValidateRange(1024, 65535)][int]$Port = 8000,
    [switch]$PrepareOnly,
    [switch]$SkipSeed,
    [switch]$RequireFrontend
)
$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Split-Path $PSScriptRoot -Parent)).Path
$names = @("APP_ENV", "VISION_PROVIDER", "RECOMMENDER_PROVIDER", "SERVE_FRONTEND", "FRONTEND_DIST")
$previous = @{}
foreach ($name in $names) { $previous[$name] = [Environment]::GetEnvironmentVariable($name, "Process") }
function Check-Native([string]$Step) {
    if ($LASTEXITCODE -ne 0) { throw "$Step failed (exit $LASTEXITCODE)" }
}
Push-Location $projectRoot
try {
    if (-not $PrepareOnly) {
        $occupied = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
        if ($occupied) { throw "Port $Port is already in use. Choose another -Port or stop your previous demo." }
    }
    & $Python -c "import sys; assert sys.version_info[:2] == (3, 12), 'Activate conda univ18 (Python 3.12)'"
    Check-Native "Python environment"
    if (-not (Test-Path -LiteralPath ".env")) { Copy-Item -LiteralPath ".env.example" -Destination ".env" }
    & $Python -m scripts.init_auth_secret
    Check-Native "JWT configuration"
    $env:APP_ENV = "demo"
    $env:VISION_PROVIDER = $Provider
    $recommendationMode = if ($RecommendationProvider) { $RecommendationProvider } else { $Provider }
    $env:RECOMMENDER_PROVIDER = $recommendationMode
    $env:FRONTEND_DIST = Join-Path $projectRoot "frontend/dist"
    $hasFrontend = Test-Path -LiteralPath (Join-Path $env:FRONTEND_DIST "index.html")
    if ($RequireFrontend -and -not $hasFrontend) { throw "Build frontend/dist/index.html before using -RequireFrontend." }
    $env:SERVE_FRONTEND = if ($hasFrontend) { "true" } else { "false" }
    docker compose up -d --wait --wait-timeout 60 db
    Check-Native "PostgreSQL startup"
    & $Python -m alembic -c backend/alembic.ini upgrade head
    Check-Native "Database migration"
    & $Python -m alembic -c backend/alembic.ini check
    Check-Native "Database schema check"
    if (-not $SkipSeed) {
        & $Python -m scripts.seed_demo
        Check-Native "Demo seed"
    }
    if ($PrepareOnly) { Write-Host "Demo preparation complete."; return }
    $runtimeDir = Join-Path $projectRoot ".demo"
    New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
    $pythonPath = (Get-Command $Python -ErrorAction Stop).Source
    $stateFile = Join-Path $runtimeDir "server-$Port.json"
    $stdout = Join-Path $runtimeDir "server-$Port.log"
    $stderr = Join-Path $runtimeDir "server-$Port.err.log"
    # One process, no reload. No external tunnel is started by this script.
    $server = Start-Process -FilePath $pythonPath -WorkingDirectory $projectRoot -WindowStyle Hidden `
        -ArgumentList @("-m", "uvicorn", "backend.app.main:app", "--host", "127.0.0.1", "--port", "$Port") `
        -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
    $state = @{ pid = $server.Id; started_at_utc = $server.StartTime.ToUniversalTime().ToString("o"); port = $Port; root = $projectRoot }
    $state | ConvertTo-Json | Set-Content -LiteralPath $stateFile -Encoding UTF8
    try {
        $timer = [Diagnostics.Stopwatch]::StartNew()
        $ready = $false
        while ($timer.Elapsed.TotalSeconds -lt 60) {
            $server.Refresh()
            if ($server.HasExited) { throw "Demo server exited. See $stderr" }
            try {
                $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 2
                $ready = $health.db -eq "ok" -and $health.app_env -eq "demo" `
                    -and $health.adapters.vision -eq $Provider -and $health.adapters.recommender -eq $recommendationMode
            } catch { $ready = $false }
            if ($ready) { break }
            Start-Sleep -Milliseconds 250
        }
        if (-not $ready) { throw "Demo readiness check timed out. See $stderr" }
    } catch {
        $server.Refresh()
        if (-not $server.HasExited) { Stop-Process -InputObject $server }
        Remove-Item -LiteralPath $stateFile -ErrorAction SilentlyContinue
        throw
    }
    Write-Host "Demo ready: http://127.0.0.1:$Port/docs (vision=$Provider, recommendation=$recommendationMode, PID=$($server.Id))"
    if ($hasFrontend) { Write-Host "Frontend: http://127.0.0.1:$Port/" }
    else { Write-Host "Frontend build is absent; use Swagger /docs for the API demonstration." }
    Write-Host "Credentials: .env.demo (not printed). Stop: .\scripts\stop_demo.ps1 -Port $Port"
} finally {
    foreach ($name in $names) { [Environment]::SetEnvironmentVariable($name, $previous[$name], "Process") }
    Pop-Location
}
