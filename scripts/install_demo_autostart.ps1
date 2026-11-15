param([string]$Python = (Get-Command python.exe -ErrorAction Stop).Source)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$taskName = 'Fitjang-Mentor-Autostart'
$description = 'Fitjang project logon supervisor; shared data; no dotenv; GPU and fixed HTTPS.'
$pythonExe = (Resolve-Path -LiteralPath $Python).Path
$pythonWindowless = Join-Path (Split-Path -Parent $pythonExe) 'pythonw.exe'
$dockerExe = (Get-Command docker.exe -ErrorAction Stop).Source
$tailscaleExe = Join-Path $env:ProgramFiles 'Tailscale\tailscale.exe'
if (-not (Test-Path -LiteralPath $pythonWindowless)) { throw 'pythonw.exe is required.' }
$oldTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($oldTask -and $oldTask.Description -ne $description) { throw 'A different task already uses this name.' }
$node = (& $tailscaleExe status --json | ConvertFrom-Json)
if ($LASTEXITCODE -ne 0 -or $node.BackendState -ne 'Running') { throw 'Tailscale must be connected.' }
$dnsName = $node.Self.DNSName.TrimEnd('.')
if ($dnsName -notmatch '^[a-z0-9-]+\.[a-z0-9-]+\.ts\.net$') { throw 'Unexpected hostname.' }
$runtime = Join-Path $projectRoot '.demo\autostart'
New-Item -ItemType Directory -Path $runtime -Force | Out-Null
$config = @{
    python = $pythonExe
    docker = $dockerExe
    tailscale = $tailscaleExe
    public_url = "https://${dnsName}:8443"
    volume = 'univ_hackathon_18_pgdata'
    container = 'univ_hackathon_18-db-1'
}
[IO.File]::WriteAllText((Join-Path $runtime 'config.json'), ($config | ConvertTo-Json), [Text.UTF8Encoding]::new($false))
$identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction -Execute $pythonWindowless -Argument '-m scripts.demo_autostart' -WorkingDirectory $projectRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $identity
$trigger.Delay = 'PT20S'
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 10 -RestartInterval (New-TimeSpan -Minutes 1) -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -StartWhenAvailable
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal `
    -Settings $settings -Description $description -Force | Out-Null
Write-Output "Registered $taskName for $identity (20 seconds after logon). No password stored."
