$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $projectRoot '.venv\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw 'Python environment missing. Install using the steps in README.md first.'
}

# Keep the launcher project-owned and limited to this exact checkout.
$startup = [Environment]::GetFolderPath('Startup')
$launcher = Join-Path $startup 'StockManager-AutoStart.vbs'
if (Test-Path -LiteralPath $launcher) {
    $existing = Get-Content -LiteralPath $launcher -Raw -ErrorAction Stop
    if (-not $existing.Contains($projectRoot)) { throw 'A different file already uses the Stock Manager startup name; it was left unchanged.' }
}
$vbs = @"
Set shell = CreateObject("WScript.Shell")
shell.CurrentDirectory = "$projectRoot"
shell.Run Chr(34) & "$python" & Chr(34) & " -m app.line_webhook", 0, False
shell.Run Chr(34) & "$python" & Chr(34) & " -m app.market_daemon", 0, False
"@
Set-Content -LiteralPath $launcher -Value $vbs -Encoding ascii
$oldLauncher = Join-Path $startup 'StockManager-LINEBot.cmd'
if (Test-Path -LiteralPath $oldLauncher) {
    $oldContents = Get-Content -LiteralPath $oldLauncher -Raw -ErrorAction SilentlyContinue
    if ($oldContents -and $oldContents.Contains($projectRoot)) { Remove-Item -LiteralPath $oldLauncher -ErrorAction SilentlyContinue }
}
try { Disable-ScheduledTask -TaskName 'StockManager-Market5Minutes' -ErrorAction Stop | Out-Null } catch { }
try { Disable-ScheduledTask -TaskName 'StockManager-LINEBot-AtLogon' -ErrorAction Stop | Out-Null } catch { }
# The LINE research worker now owns discovery and catch-up while this computer
# is running; the legacy morning task is redundant for local-server use.
try { Disable-ScheduledTask -TaskName 'StockManager-ResearchDaily' -ErrorAction Stop | Out-Null } catch { }

# Start immediately as well as after future sign-ins. Each worker has a
# project-specific lock, so starting twice will not duplicate it.
Start-Process -FilePath $python -ArgumentList '-m app.line_webhook' -WorkingDirectory $projectRoot -WindowStyle Hidden
Start-Process -FilePath $python -ArgumentList '-m app.market_daemon' -WorkingDirectory $projectRoot -WindowStyle Hidden
Write-Output 'Stock Manager started quietly. LINE and market checks will start after future Windows sign-ins too.'
