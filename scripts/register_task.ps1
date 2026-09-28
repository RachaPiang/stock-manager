# Run this manually only when you want to enable scheduling.
param([string]$TaskName = 'StockManager-Market5Minutes')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw 'Create the project virtual environment with pythonw.exe before registering the task.'
}
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    throw 'Task already exists. Review it in Task Scheduler instead of overwriting it.'
}
$action = New-ScheduledTaskAction -Execute $pythonPath -Argument '-m app.main check --scheduled' -WorkingDirectory $projectRoot
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddMinutes(([math]::Floor((Get-Date).TimeOfDay.TotalMinutes / 5) + 1) * 5) -RepetitionInterval (New-TimeSpan -Minutes 5)
$taskSettings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 25)
$account = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $account -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $taskSettings -Principal $principal -Description 'Five-minute regular US market sessions only; no trading. Requires logged-in user and awake computer. Python enforces holidays and API budget.'
