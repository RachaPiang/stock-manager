param([string]$PythonPath = '', [switch]$UseTwelveData)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not $PythonPath) {
    $PythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
}
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw 'Python virtual environment not found. Create .venv and install requirements first.'
}
Push-Location -LiteralPath $projectRoot
try {
    & $PythonPath -m app.benchmark
    $newsExit = 0
    if ($UseTwelveData) {
        & $PythonPath -m app.main news-sync
        $newsExit = $LASTEXITCODE
    }
    & $PythonPath -m app.official_news
    & $PythonPath -m app.fundamentals
    # LINE's durable scheduler owns end-of-week/month analysis and Monday web news.
    & $PythonPath -m app.main report
    if ($newsExit -ne 0) { exit 1 }
}
finally {
    Pop-Location
}
