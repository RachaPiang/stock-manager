$ErrorActionPreference = 'Continue'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$startup = [Environment]::GetFolderPath('Startup')
$launchers = @((Join-Path $startup 'StockManager-AutoStart.vbs'), (Join-Path $startup 'StockManager-LINEBot.cmd'))
$stopFailed = $false

# Stop only this project’s known scheduled tasks, never unrelated Windows tasks.
foreach ($name in @('StockManager-LINEBot-AtLogon','StockManager-Market5Minutes','StockManager-ResearchDaily')) {
    try {
        $task = Get-ScheduledTask -TaskName $name -ErrorAction Stop
        if ($task.State -eq 'Running') { Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue }
        Disable-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue | Out-Null
        Write-Output "Disabled project task: $name"
    } catch { }
}

# Remove only the launcher created for this project, and only if it points here.
foreach ($launcher in $launchers) {
    if (Test-Path -LiteralPath $launcher) {
        $contents = Get-Content -LiteralPath $launcher -Raw -ErrorAction SilentlyContinue
        if ($contents -and $contents.Contains($projectRoot)) {
            Remove-Item -LiteralPath $launcher -ErrorAction SilentlyContinue
            Write-Output "Disabled this project's Windows Startup launcher."
        }
    }
}

# Match exact Python path plus a known Stock Manager module before terminating.
$pythonPath = [IO.Path]::GetFullPath((Join-Path $projectRoot '.venv\Scripts\pythonw.exe'))
. (Join-Path $PSScriptRoot 'project_python.ps1')
try {
    $workers = Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe'" -ErrorAction Stop |
        Where-Object {
            (Test-ProjectPython $_ $pythonPath) -and
            $_.CommandLine -match '\s-m\s+app\.(line_webhook|market_daemon|main\s+check)(?:\s|$)'
        }
    foreach ($worker in $workers) {
        Stop-Process -Id $worker.ProcessId -Force -ErrorAction Stop
        Write-Output "Stopped Stock Manager worker PID $($worker.ProcessId)."
    }
} catch {
    $stopFailed = $true
    Write-Warning 'Could not inspect Windows process details. Close only the Stock Manager background process from Task Manager if it remains active.'
}

# The LINE worker may have spawned its own temporary Cloudflare tunnel child.
# Stop only the binary shipped inside this project's ignored data/tools folder.
$tunnelPath = [IO.Path]::GetFullPath((Join-Path $projectRoot 'data\tools\cloudflared.exe'))
if (Test-Path -LiteralPath $tunnelPath) {
    try {
        $tunnels = Get-CimInstance Win32_Process -Filter "Name = 'cloudflared.exe'" -ErrorAction Stop |
            Where-Object {
                $_.ExecutablePath -and [IO.Path]::GetFullPath($_.ExecutablePath) -eq $tunnelPath -and
                $_.CommandLine -match 'tunnel\s+--no-autoupdate\s+--url\s+http://127\.0\.0\.1:8787'
            }
        foreach ($tunnel in $tunnels) {
            Stop-Process -Id $tunnel.ProcessId -Force -ErrorAction Stop
            Write-Output "Stopped this project's LINE tunnel PID $($tunnel.ProcessId)."
        }
    } catch {
        $stopFailed = $true
        Write-Warning 'Could not inspect the project LINE tunnel; check Task Manager if it remains active.'
    }
}

if ($stopFailed) {
    Write-Warning 'Automatic start was disabled, but one or more running workers may remain active.'
} else {
    Write-Output 'Stock Manager is stopped and its automatic startup is disabled. Other Python apps and Windows tasks were not touched.'
}
