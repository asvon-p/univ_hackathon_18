param(
    [Parameter(Mandatory=$true)]
    [ValidateSet('training', 'tryon', 'status')]
    [string]$Mode,
    [string]$Python = 'python',
    [string]$ProjectRoot = (Split-Path $PSScriptRoot -Parent)
)
$ErrorActionPreference = 'Stop'
$env:FITJANG_SKIP_DOTENV = '1'
$action = @{ training = 'pause'; tryon = 'resume'; status = 'status' }[$Mode]
Push-Location (Split-Path $PSScriptRoot -Parent)
try {
    & $Python -m scripts.tryon_control $action --project-root $ProjectRoot
    $result = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $result
