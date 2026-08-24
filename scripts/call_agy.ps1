# Thin Windows launcher for the host-agnostic Python wrapper.
$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonScript = Join-Path $ScriptDir "call_agy.py"

$PythonCommand = $null
$PythonPrefix = @()

if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" 2>$null
    if ($LASTEXITCODE -eq 0) {
        $PythonCommand = "py"
        $PythonPrefix = @("-3")
    }
}

if (-not $PythonCommand -and (Get-Command python -ErrorAction SilentlyContinue)) {
    & python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" 2>$null
    if ($LASTEXITCODE -eq 0) {
        $PythonCommand = "python"
    }
}

if (-not $PythonCommand -and (Get-Command python3 -ErrorAction SilentlyContinue)) {
    & python3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" 2>$null
    if ($LASTEXITCODE -eq 0) {
        $PythonCommand = "python3"
    }
}

if (-not $PythonCommand) {
    Write-Error "[call-agy] Python 3.10+ is required to run scripts/call_agy.py"
    exit 1
}

$PreviousOutputEncoding = $OutputEncoding
$PreviousConsoleOutputEncoding = [Console]::OutputEncoding
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$ExitCode = 1
try {
    $OutputEncoding = $Utf8NoBom
    [Console]::OutputEncoding = $Utf8NoBom
    if ($MyInvocation.ExpectingInput) {
        $input | & $PythonCommand @PythonPrefix $PythonScript @args
    } else {
        & $PythonCommand @PythonPrefix $PythonScript @args
    }
    $ExitCode = $LASTEXITCODE
} finally {
    $OutputEncoding = $PreviousOutputEncoding
    [Console]::OutputEncoding = $PreviousConsoleOutputEncoding
}
exit $ExitCode
