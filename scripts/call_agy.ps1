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
    # Native exit status comes from $LASTEXITCODE. On Windows PowerShell 5.1 an
    # $ErrorActionPreference of "Stop" promotes the child's stderr to a terminating
    # NativeCommandError, which aborts this launcher mid-run: $ExitCode keeps its 1
    # initial value (so success looks like failure) and agy is left running detached
    # because call_agy.py starts it with CREATE_NEW_PROCESS_GROUP. The wrapper writes
    # a HEADLESS_PERMISSION_RISK warning to stderr on every dispatch, so under 5.1 any
    # caller that lets PowerShell read that stderr - the documented & call_agy.ps1
    # shape - loses everything after the warning. And $PSNativeCommandUseErrorActionPreference
    # (PS 7.3) does not exist on 5.1, so scope the preference around the child process.
    $PreviousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        if ($MyInvocation.ExpectingInput) {
            $input | & $PythonCommand @PythonPrefix $PythonScript @args
        } else {
            & $PythonCommand @PythonPrefix $PythonScript @args
        }
    } finally {
        $ErrorActionPreference = $PreviousErrorActionPreference
    }
    $ExitCode = $LASTEXITCODE
} finally {
    $OutputEncoding = $PreviousOutputEncoding
    [Console]::OutputEncoding = $PreviousConsoleOutputEncoding
}
exit $ExitCode
