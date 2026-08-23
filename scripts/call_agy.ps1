# Thin Windows launcher for the host-agnostic Python wrapper.
$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonScript = Join-Path $ScriptDir "call_agy.py"

if (Get-Command py -ErrorAction SilentlyContinue) {
    if ($MyInvocation.ExpectingInput) {
        $input | & py -3 $PythonScript @args
    } else {
        & py -3 $PythonScript @args
    }
    exit $LASTEXITCODE
}

if (Get-Command python -ErrorAction SilentlyContinue) {
    if ($MyInvocation.ExpectingInput) {
        $input | & python $PythonScript @args
    } else {
        & python $PythonScript @args
    }
    exit $LASTEXITCODE
}

if (Get-Command python3 -ErrorAction SilentlyContinue) {
    if ($MyInvocation.ExpectingInput) {
        $input | & python3 $PythonScript @args
    } else {
        & python3 $PythonScript @args
    }
    exit $LASTEXITCODE
}

Write-Error "[call-agy] Python 3 is required to run scripts/call_agy.py"
exit 1
