param([string]$Python = 'python')

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$tailscaleExe = Join-Path $env:ProgramFiles 'Tailscale\tailscale.exe'
if (-not (Test-Path -LiteralPath $tailscaleExe)) {
    throw 'Install and sign in to Tailscale before starting the fixed-URL demo.'
}
$env:FITJANG_SKIP_DOTENV = '1'
Push-Location -LiteralPath $projectRoot
try {
    $nodeState = (& $tailscaleExe status --json | ConvertFrom-Json)
    if ($LASTEXITCODE -ne 0 -or $nodeState.BackendState -ne 'Running') {
        throw 'Tailscale is not signed in or connected.'
    }
    $dnsName = $nodeState.Self.DNSName.TrimEnd('.')
    if ($dnsName -notmatch '^[a-z0-9-]+\.[a-z0-9-]+\.ts\.net$') {
        throw 'Unexpected Tailscale HTTPS hostname.'
    }
    $endpoint = "${dnsName}:8443"
    $publicUrl = "https://$endpoint"
    $serveState = (& $tailscaleExe funnel status --json | ConvertFrom-Json)
    if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect current Funnel configuration.' }
    # Preserve every existing service, including the private Serve listener on 443.
    if ($serveState.Web) {
        foreach ($property in $serveState.Web.PSObject.Properties) {
            if ($property.Name -notlike '*:8443') { continue }
            $handlers = @($property.Value.Handlers.PSObject.Properties)
            if ($property.Name -ne $endpoint -or $handlers.Count -ne 1 -or
                $handlers[0].Name -ne '/' -or $handlers[0].Value.Proxy -ne 'http://127.0.0.1:3001') {
                throw 'Port 8443 already serves another application; configuration was not changed.'
            }
        }
    }
    # --bg persists the proxy across app restarts; no domain purchase or .env needed.
    & $tailscaleExe funnel --bg --https=8443 --yes http://127.0.0.1:3001
    if ($LASTEXITCODE -ne 0) { throw 'Funnel failed. Complete its account approval if prompted.' }
    Write-Host "Fixed demo URL: $publicUrl"
    & $Python -m scripts.mentor_demo --local-only --shared-local --tryon --public-url $publicUrl
    if ($LASTEXITCODE -ne 0) { throw 'Mentor server failed. Check .demo/mentor-demo logs.' }
}
finally {
    Pop-Location
}
