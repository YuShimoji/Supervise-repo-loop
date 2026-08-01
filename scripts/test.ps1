[CmdletBinding()]
param(
    [Parameter()]
    [string]$Root,

    [Parameter()]
    [string]$PythonPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if (-not $Root) {
    $Root = Split-Path -Parent $PSScriptRoot
}

$resolvedRoot = (Resolve-Path -LiteralPath $Root).Path
$testRoot = Join-Path $resolvedRoot 'tests'
if (-not (Test-Path -LiteralPath $testRoot -PathType Container)) {
    throw "Test directory is missing: $testRoot"
}

function Test-PythonCandidate {
    param(
        [Parameter(Mandatory)]
        [string]$Executable,

        [Parameter()]
        [string[]]$PrefixArguments = @()
    )

    if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) {
        return $false
    }

    try {
        & $Executable @PrefixArguments -c `
            'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' `
            *> $null
        return $LASTEXITCODE -eq 0
    }
    catch {
        return $false
    }
}

function Resolve-ExistingPython {
    param(
        [Parameter()]
        [string]$RequestedPath
    )

    if ($RequestedPath) {
        $resolved = (Resolve-Path -LiteralPath $RequestedPath).Path
        if (-not (Test-PythonCandidate -Executable $resolved)) {
            throw "-PythonPath is not a usable Python 3.11+ executable: $resolved"
        }
        return [PSCustomObject]@{
            Executable = $resolved
            PrefixArguments = @()
            Source = 'explicit'
        }
    }

    $uv = Get-Command uv -CommandType Application -ErrorAction SilentlyContinue
    if ($uv) {
        $previousOffline = $env:UV_OFFLINE
        try {
            $env:UV_OFFLINE = '1'
            $uvResult = & $uv.Source python find '>=3.11' 2>$null
            if ($LASTEXITCODE -eq 0 -and $uvResult) {
                $candidate = ($uvResult | Select-Object -First 1).Trim()
                if (Test-PythonCandidate -Executable $candidate) {
                    return [PSCustomObject]@{
                        Executable = $candidate
                        PrefixArguments = @()
                        Source = 'uv-existing'
                    }
                }
            }
        }
        finally {
            $env:UV_OFFLINE = $previousOffline
        }
    }

    foreach ($commandName in @('python3', 'python')) {
        $command = Get-Command $commandName -CommandType Application -ErrorAction SilentlyContinue
        if ($command -and (Test-PythonCandidate -Executable $command.Source)) {
            return [PSCustomObject]@{
                Executable = $command.Source
                PrefixArguments = @()
                Source = "path-$commandName"
            }
        }
    }

    $launcher = Get-Command py -CommandType Application -ErrorAction SilentlyContinue
    if ($launcher -and (Test-PythonCandidate -Executable $launcher.Source -PrefixArguments @('-3'))) {
        return [PSCustomObject]@{
            Executable = $launcher.Source
            PrefixArguments = @('-3')
            Source = 'python-launcher'
        }
    }

    throw @'
No existing Python 3.11+ runtime was found. No installation was attempted.
Load the Codex workspace dependencies and rerun with -PythonPath set to the
reported Python executable, or make an existing uv-managed Python available.
'@
}

$python = Resolve-ExistingPython -RequestedPath $PythonPath
Write-Output "Using $($python.Source) Python: $($python.Executable)"

Push-Location $resolvedRoot
try {
    & $python.Executable @($python.PrefixArguments) -B -m unittest discover `
        -s $testRoot -v
    if ($LASTEXITCODE -ne 0) {
        throw "Test suite failed with exit code $LASTEXITCODE."
    }
}
finally {
    Pop-Location
}

