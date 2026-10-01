$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$pythonPath = Join-Path $projectRoot '.venv\Scripts\pythonw.exe'
. (Join-Path $PSScriptRoot 'project_python.ps1')
$workers = @(Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe'" | Where-Object { Test-ProjectPython $_ $pythonPath })
foreach ($module in @('line_webhook', 'market_daemon')) {
    $running = @($workers | Where-Object { $_.CommandLine -match ("\s-m\s+app\." + $module + "(?:\s|$)") }).Count -gt 0
    Write-Output "$module running: $running"
}
$launcher = Join-Path ([Environment]::GetFolderPath('Startup')) 'StockManager-AutoStart.vbs'
$enabled = (Test-Path -LiteralPath $launcher) -and (Get-Content -LiteralPath $launcher -Raw).Contains($projectRoot)
Write-Output "Start after Windows sign-in: $enabled"
try {
    $response = Invoke-WebRequest -Uri 'http://127.0.0.1:8787/health' -UseBasicParsing -TimeoutSec 3
    Write-Output "Local LINE webhook responding: $($response.StatusCode -eq 200)"
} catch { Write-Output 'Local LINE webhook responding: False' }
Write-Output 'Process status does not confirm Internet delivery. Use the API check or send a LINE command to verify.'
