param([string]$PythonPath = '')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not $PythonPath) {
    $PythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
}
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw 'Python virtual environment not found. Create .venv and install requirements first.'
}
$resultCode = 2
Push-Location -LiteralPath $projectRoot
try {
    & $PythonPath -m app.main check --scheduled
    $resultCode = $LASTEXITCODE
}
finally {
    Pop-Location
}
exit $resultCode
