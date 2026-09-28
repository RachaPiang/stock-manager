# Run manually only when you want automatic research. It creates one daily task, never overwrites one.
param([string]$TaskName = 'StockManager-ResearchDaily')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$runner = Join-Path $PSScriptRoot 'run_research.ps1'
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw 'Create the project virtual environment before registering the task.'
}
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    throw 'Task already exists. Review it in Task Scheduler instead of overwriting it.'
}
# Tue-Sat 07:15 Bangkok time follows the prior US market day; avoids weekend API use.
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument ('-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $runner + '"') -WorkingDirectory $projectRoot
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Tuesday,Wednesday,Thursday,Friday,Saturday -At 7:15AM
$taskSettings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 25)
$account = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $account -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $taskSettings -Principal $principal -Description 'Weekday official-company-release sync after US market day and weekly-deduplicated portfolio review. No trading, no LINE required. Python caps REST credits below 800/day.'
