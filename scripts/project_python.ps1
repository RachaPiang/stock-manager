# Windows venv launchers may redirect to the base Python executable. Match the
# exact venv executable at the start of the command line as well as its path.
function Test-ProjectPython($Process, [string]$PythonPath) {
    $prefix = '^\s*"?' + [regex]::Escape($PythonPath) + '"?(?:\s|$)'
    return (($Process.ExecutablePath -eq $PythonPath) -or
            ($Process.CommandLine -and $Process.CommandLine -match $prefix))
}
