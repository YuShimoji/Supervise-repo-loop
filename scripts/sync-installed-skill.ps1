[CmdletBinding(SupportsShouldProcess, ConfirmImpact = 'Medium')]
param(
    [Parameter()]
    [string]$Destination,

    [Parameter()]
    [string]$PythonPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$sourceRoot = (Resolve-Path -LiteralPath (Split-Path -Parent $PSScriptRoot)).Path
if (-not $Destination) {
    $userProfile = [Environment]::GetFolderPath('UserProfile')
    if (-not $userProfile) {
        throw 'Cannot resolve the current user profile for the installed skill path.'
    }
    $Destination = Join-Path $userProfile '.codex\skills\supervise-repo-loop'
}

$destinationFull = [System.IO.Path]::GetFullPath($Destination)
if ([System.IO.Path]::GetFileName($destinationFull.TrimEnd('\', '/')) -ne 'supervise-repo-loop') {
    throw "Destination must end with the exact directory name supervise-repo-loop: $destinationFull"
}
if ($destinationFull.TrimEnd('\', '/') -eq $sourceRoot.TrimEnd('\', '/')) {
    throw 'Source and destination must be different directories.'
}
if (Test-Path -LiteralPath $destinationFull) {
    $destinationItem = Get-Item -LiteralPath $destinationFull -Force
    if (-not $destinationItem.PSIsContainer) {
        throw "Destination is not a directory: $destinationFull"
    }
    if ($destinationItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
        throw "Destination is a reparse point; refusing to modify it: $destinationFull"
    }
}

$testScript = Join-Path $sourceRoot 'scripts\test.ps1'
Write-Output 'Running source tests before any copy...'
& $testScript -Root $sourceRoot -PythonPath $PythonPath

$allowlist = @(
    'SKILL.md',
    'AGENTS.md',
    'README.md',
    'agents',
    'docs',
    'references',
    'schemas',
    'scripts',
    'tests'
)

$manifest = [System.Collections.Generic.List[object]]::new()
foreach ($entry in $allowlist) {
    $sourceEntry = Join-Path $sourceRoot $entry
    if (-not (Test-Path -LiteralPath $sourceEntry)) {
        throw "Allowlisted source entry is missing: $sourceEntry"
    }

    $item = Get-Item -LiteralPath $sourceEntry -Force
    $files = if ($item.PSIsContainer) {
        Get-ChildItem -LiteralPath $sourceEntry -Recurse -File -Force |
            Where-Object {
                $_.FullName -notmatch '[\\/]__pycache__[\\/]' -and
                $_.Extension -notin @('.pyc', '.pyo')
            }
    }
    else {
        @($item)
    }

    foreach ($file in $files) {
        if ($file.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
            throw "Reparse points are not allowed in the static manifest: $($file.FullName)"
        }

        $relative = $file.FullName.Substring($sourceRoot.Length).TrimStart('\', '/')
        $portable = $relative.Replace('\', '/')
        if ($portable -match '(^|/)(state|\.serena)(/|$)') {
            throw "Forbidden runtime/local path entered the manifest: $relative"
        }

        $manifest.Add([PSCustomObject]@{
            Relative = $relative
            Source = $file.FullName
            Destination = Join-Path $destinationFull $relative
        })
    }
}

if ($manifest.Count -eq 0) {
    throw 'The static installation manifest is empty.'
}

Write-Output "Static manifest: $($manifest.Count) files"
Write-Output "Destination: $destinationFull"

foreach ($entry in $manifest) {
    if ($PSCmdlet.ShouldProcess($entry.Destination, "Copy static source $($entry.Relative)")) {
        $parent = Split-Path -Parent $entry.Destination
        if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
            New-Item -ItemType Directory -Path $parent -Force | Out-Null
        }
        Copy-Item -LiteralPath $entry.Source -Destination $entry.Destination -Force
    }
}

if ($WhatIfPreference) {
    Write-Output 'WhatIf preview complete; no files were copied and post-copy checks were skipped.'
    return
}

$mismatches = [System.Collections.Generic.List[string]]::new()
foreach ($entry in $manifest) {
    if (-not (Test-Path -LiteralPath $entry.Destination -PathType Leaf)) {
        $mismatches.Add("missing: $($entry.Relative)")
        continue
    }

    $sourceHash = (Get-FileHash -LiteralPath $entry.Source -Algorithm SHA256).Hash
    $destinationHash = (Get-FileHash -LiteralPath $entry.Destination -Algorithm SHA256).Hash
    if ($sourceHash -ne $destinationHash) {
        $mismatches.Add("hash mismatch: $($entry.Relative)")
    }
}

if ($mismatches.Count -gt 0) {
    throw "Static installation verification failed:`n$($mismatches -join "`n")"
}

Write-Output "SHA-256 readback passed for $($manifest.Count) files."
Write-Output 'Running the complete test suite against the installed copy...'
& $testScript -Root $destinationFull -PythonPath $PythonPath
Write-Output 'Static skill installation completed. Live state and .serena were not synchronized.'

