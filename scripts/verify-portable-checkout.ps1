[CmdletBinding()]
param(
    [Parameter()]
    [string]$ExpectedRemote = 'https://github.com/YuShimoji/Supervise-repo-loop.git',

    [Parameter()]
    [string]$InstalledSkillPath,

    [Parameter()]
    [switch]$RequireInstalledParity,

    [Parameter()]
    [switch]$RequireClean,

    [Parameter()]
    [switch]$VerifyRemoteTip,

    [Parameter()]
    [switch]$SkipTests
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$sourceRoot = (Resolve-Path -LiteralPath (Split-Path -Parent $PSScriptRoot)).Path

function Invoke-GitText {
    param(
        [Parameter(Mandatory)]
        [string[]]$Arguments
    )

    $output = & git -C $sourceRoot @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "git $($Arguments -join ' ') failed: $($output -join [Environment]::NewLine)"
    }
    return ($output -join "`n").Trim()
}

function Normalize-GitRemote {
    param(
        [Parameter(Mandatory)]
        [string]$Remote
    )

    $value = $Remote.Trim().Replace('\', '/')
    if ($value -match '^[^@]+@(?<host>[^:]+):(?<path>.+)$') {
        $value = "$($Matches.host)/$($Matches.path)"
    }
    elseif ($value -match '^[a-zA-Z][a-zA-Z0-9+.-]*://') {
        $uri = [Uri]$value
        $value = "$($uri.Host)/$($uri.AbsolutePath.TrimStart('/'))"
    }
    return $value.TrimEnd('/').Replace('.git', '').ToLowerInvariant()
}

function Get-StaticManifest {
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
            throw "Allowlisted static source is missing: $sourceEntry"
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
                throw "Reparse points are not portable static source: $($file.FullName)"
            }
            $relative = $file.FullName.Substring($sourceRoot.Length).TrimStart('\', '/')
            $portable = $relative.Replace('\', '/')
            if ($portable -match '(^|/)(state|\.serena|\.playwright-mcp)(/|$)') {
                throw "Host-local path entered the static manifest: $relative"
            }
            $manifest.Add([PSCustomObject]@{
                Relative = $relative
                Source = $file.FullName
            })
        }
    }
    return $manifest
}

$actualRoot = Invoke-GitText -Arguments @('rev-parse', '--show-toplevel')
$actualRoot = [System.IO.Path]::GetFullPath($actualRoot)
if ($actualRoot.TrimEnd('\', '/') -ne $sourceRoot.TrimEnd('\', '/')) {
    throw "Script root is not the exact Git root: script=$sourceRoot git=$actualRoot"
}

$origin = Invoke-GitText -Arguments @('remote', 'get-url', 'origin')
if ((Normalize-GitRemote -Remote $origin) -ne
    (Normalize-GitRemote -Remote $ExpectedRemote)) {
    throw "origin does not match the expected repository: $origin"
}

$branch = Invoke-GitText -Arguments @('branch', '--show-current')
if (-not $branch) {
    throw 'Detached HEAD is not a portable post-work reflection target.'
}

$upstream = Invoke-GitText -Arguments @(
    'rev-parse',
    '--abbrev-ref',
    '--symbolic-full-name',
    '@{upstream}'
)
$expectedUpstream = "origin/$branch"
if ($upstream -ne $expectedUpstream) {
    throw "Current branch must track its same-named origin branch: expected=$expectedUpstream actual=$upstream"
}

$trackedHostLocal = Invoke-GitText -Arguments @(
    'ls-files',
    '--',
    'state',
    'state/**',
    '.serena',
    '.serena/**',
    '.playwright-mcp',
    '.playwright-mcp/**'
)
if ($trackedHostLocal) {
    throw "Host-local files are tracked:`n$trackedHostLocal"
}

$workingTree = Invoke-GitText -Arguments @('status', '--porcelain=v1', '--untracked-files=all')
if ($RequireClean -and $workingTree) {
    throw "Working tree is not clean:`n$workingTree"
}

$automationManifestPath = Join-Path $sourceRoot 'references\automation-portability.v1.json'
$automationManifest = Get-Content -LiteralPath $automationManifestPath -Raw |
    ConvertFrom-Json
if ($automationManifest.schema_version -ne 1) {
    throw 'Unsupported automation portability manifest schema.'
}
$roles = @($automationManifest.profiles | ForEach-Object { $_.role })
foreach ($requiredRole in @('coordinator_recovery_lease', 'post_work_reflection')) {
    if ($requiredRole -notin $roles) {
        throw "Automation portability profile is missing: $requiredRole"
    }
}

if (-not $SkipTests) {
    & (Join-Path $sourceRoot 'scripts\test.ps1') -Root $sourceRoot
}

$manifest = @(Get-StaticManifest)
if (-not $InstalledSkillPath) {
    $userProfile = [Environment]::GetFolderPath('UserProfile')
    if (-not $userProfile) {
        throw 'Cannot resolve the current user profile for the installed skill path.'
    }
    $InstalledSkillPath = Join-Path $userProfile '.codex\skills\supervise-repo-loop'
}
$installedFull = [System.IO.Path]::GetFullPath($InstalledSkillPath)
$installedParity = 'missing_action_required'
$installedMismatches = [System.Collections.Generic.List[string]]::new()
if (Test-Path -LiteralPath $installedFull -PathType Container) {
    $installedItem = Get-Item -LiteralPath $installedFull -Force
    if ($installedItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
        throw "Installed skill is a reparse point: $installedFull"
    }
    foreach ($entry in $manifest) {
        $destination = Join-Path $installedFull $entry.Relative
        if (-not (Test-Path -LiteralPath $destination -PathType Leaf)) {
            $installedMismatches.Add("missing: $($entry.Relative)")
            continue
        }
        $sourceHash = (Get-FileHash -LiteralPath $entry.Source -Algorithm SHA256).Hash
        $installedHash = (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash
        if ($sourceHash -ne $installedHash) {
            $installedMismatches.Add("hash mismatch: $($entry.Relative)")
        }
    }
    $installedParity = if ($installedMismatches.Count -eq 0) {
        'exact'
    }
    else {
        'mismatch_action_required'
    }
}
if ($RequireInstalledParity -and $installedParity -ne 'exact') {
    throw "Installed static parity is required but not exact: $($installedMismatches -join ', ')"
}

$remoteTipState = 'not_checked'
$head = Invoke-GitText -Arguments @('rev-parse', 'HEAD')
if ($VerifyRemoteTip) {
    $remoteLine = Invoke-GitText -Arguments @(
        'ls-remote',
        '--exit-code',
        'origin',
        "refs/heads/$branch"
    )
    $remoteSha = ($remoteLine -split '\s+')[0]
    if ($remoteSha -ne $head) {
        throw "Remote branch does not match local HEAD: local=$head remote=$remoteSha"
    }
    $counts = Invoke-GitText -Arguments @(
        'rev-list',
        '--left-right',
        '--count',
        "$upstream...HEAD"
    )
    if (($counts -replace '\s+', ' ').Trim() -ne '0 0') {
        throw "Local and upstream are not at parity: $counts"
    }
    $remoteTipState = 'exact'
}

$result = [ordered]@{
    schema_version = 1
    repository = [ordered]@{
        root = $sourceRoot
        origin = $origin
        branch = $branch
        upstream = $upstream
        head = $head
        working_tree = if ($workingTree) { 'dirty' } else { 'clean' }
        remote_tip = $remoteTipState
    }
    static_source = [ordered]@{
        file_count = $manifest.Count
        tracked_host_local_paths = 0
        automation_profiles = $roles
    }
    installed_skill = [ordered]@{
        path = $installedFull
        parity = $installedParity
        mismatch_count = $installedMismatches.Count
    }
    live_scheduler = [ordered]@{
        state_transfer = 'forbidden'
        task_id_transfer = 'forbidden'
        recovery_heartbeat = 'recreate_paused_on_receiving_host'
        activation_gate = 'coordinator-plan.watchdog_should_be_armed=true'
    }
    post_work_reflection = [ordered]@{
        repository_prerequisites = 'ready'
        host_gate_and_helper = 'configure_per_host'
    }
    readiness = 'PORTABLE_STATIC_CHECKPOINT'
}

$result | ConvertTo-Json -Depth 6
