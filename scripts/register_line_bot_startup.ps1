# Install a quiet per-user logon launcher for the LINE bot and market monitor.
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw 'Create the project virtual environment with pythonw.exe before enabling automatic start.'
}

$startup = [Environment]::GetFolderPath('Startup')
$launcher = Join-Path $startup 'StockManager-AutoStart.vbs'
if (Test-Path -LiteralPath $launcher) {
    $existing = Get-Content -LiteralPath $launcher -Raw -ErrorAction Stop
    if (-not $existing.Contains($projectRoot)) { throw 'A different file already uses the Stock Manager startup name; it was left unchanged.' }
}
$vbs = @"
Set shell = CreateObject("WScript.Shell")
shell.CurrentDirectory = "$projectRoot"
shell.Run Chr(34) & "$pythonPath" & Chr(34) & " -m app.line_webhook", 0, False
shell.Run Chr(34) & "$pythonPath" & Chr(34) & " -m app.market_daemon", 0, False
"@
Set-Content -LiteralPath $launcher -Value $vbs -Encoding ascii
$oldLauncher = Join-Path $startup 'StockManager-LINEBot.cmd'
if (Test-Path -LiteralPath $oldLauncher) {
    $oldContents = Get-Content -LiteralPath $oldLauncher -Raw -ErrorAction SilentlyContinue
    if ($oldContents -and $oldContents.Contains($projectRoot)) {
        Remove-Item -LiteralPath $oldLauncher -ErrorAction SilentlyContinue
    }
}
try { Disable-ScheduledTask -TaskName 'StockManager-Market5Minutes' -ErrorAction Stop | Out-Null } catch { }
try { Disable-ScheduledTask -TaskName 'StockManager-LINEBot-AtLogon' -ErrorAction Stop | Out-Null } catch { }
Write-Host 'หน้าต่างไม่แสดงตอนล็อกอิน · LINE และเครื่องตรวจตลาดจะทำงานเบื้องหลัง'
