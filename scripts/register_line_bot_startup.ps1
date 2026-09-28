# Register once: start the private LINE assistant when this Windows account logs in.
# The app's own lock prevents duplicate workers when Open Portfolio.cmd is used too.
param([string]$TaskName = 'StockManager-LINEBot-AtLogon')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw 'Create the project virtual environment before registering the LINE bot task.'
}
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Write-Host 'The LINE auto-start task already exists; it was left unchanged.'
    exit 0
}
try {
    $action = New-ScheduledTaskAction -Execute $pythonPath -Argument '-m app.line_webhook' -WorkingDirectory $projectRoot
    $trigger = New-ScheduledTaskTrigger -AtLogOn
    $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Days 30)
    $account = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    $principal = New-ScheduledTaskPrincipal -UserId $account -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description 'Starts the private Stock Manager LINE assistant after Windows sign-in. No trading.' -ErrorAction Stop | Out-Null
    Write-Host 'LINE bot auto-start is enabled through Task Scheduler.'
    exit 0
} catch {
    # Some Windows policies deny creating an interactive scheduled task. The
    # per-user Startup folder needs no administrator access and starts the same
    # hidden pythonw worker after sign-in.
    $startup = [Environment]::GetFolderPath('Startup')
    $launcher = Join-Path $startup 'StockManager-LINEBot.cmd'
    # This file is owned by this installer; rewrite it so a project-path
    # change or a previous partial creation cannot leave a broken launcher.
    @(
        '@echo off',
        ('start "" /b "{0}" -m app.line_webhook' -f $pythonPath)
    ) | Set-Content -LiteralPath $launcher -Encoding ascii
    Write-Host 'Windows denied the scheduled task. LINE bot auto-start is enabled through this account Startup folder.'
}
