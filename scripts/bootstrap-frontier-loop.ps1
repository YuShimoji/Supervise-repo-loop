#requires -Version 7.0

[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$StorageRoot,

    [string]$CoordinatorRoot = '',

    [string[]]$AdditionalShardRoot = @(),

    [ValidateSet('primary', 'standby', 'local-runner')]
    [string]$Role = 'standby',

    [string]$HostId = $([Environment]::MachineName.ToLowerInvariant()),

    [string]$CodexHome = $([IO.Path]::Combine(
        [Environment]::GetFolderPath([Environment+SpecialFolder]::UserProfile),
        '.codex'
    )),

    [string]$FrontierBoardProgramsRoot = $(if ($env:LOCALAPPDATA) { Join-Path $env:LOCALAPPDATA 'Programs' } else { '' }),

    [string]$FrontierBoardStartMenuProgramsRoot = $(if ($env:APPDATA) { Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs' } else { '' }),

    [string]$FrontierBoardDataRoot = $(if ($env:LOCALAPPDATA) { Join-Path $env:LOCALAPPDATA 'FrontierBoard' } else { '' }),

    [switch]$ForceProfile
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$entryRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$contractPath = Join-Path $entryRoot 'config\canonical-source.json'
if (-not (Test-Path -LiteralPath $contractPath -PathType Leaf)) {
    throw "canonical_source_contract_missing:$contractPath"
}
$contract = Get-Content -Raw -LiteralPath $contractPath | ConvertFrom-Json
if ([int]$contract.schemaVersion -ne 1) { throw 'canonical_source_schema_unsupported' }

$storageRootPath = [IO.Path]::GetFullPath($StorageRoot)
if (-not (Test-Path -LiteralPath $storageRootPath -PathType Container)) {
    throw "storage_root_missing:$storageRootPath"
}
$coordinatorRootPath = if ([string]::IsNullOrWhiteSpace($CoordinatorRoot)) {
    Join-Path $storageRootPath 'Coordinator'
}
else {
    [IO.Path]::GetFullPath($CoordinatorRoot)
}
$repositoryUrl = [string]$contract.coordinator.repositoryUrl
$branch = [string]$contract.coordinator.branch
$minimumRevision = [string]$contract.coordinator.minimumRevision

function Invoke-CheckedGit {
    param(
        [Parameter(Mandatory)][string]$WorkingDirectory,
        [Parameter(Mandatory)][string[]]$Arguments,
        [switch]$AllowFailure
    )
    $output = @(& git -C $WorkingDirectory @Arguments 2>&1)
    $exitCode = $LASTEXITCODE
    $rendered = [string]::Join("`n", @($output | ForEach-Object { [string]$_ })).Trim()
    if ($exitCode -ne 0 -and -not $AllowFailure) {
        throw "coordinator_git_failed:$($Arguments -join ' '):${exitCode}:$rendered"
    }
    [pscustomobject]@{ exitCode = $exitCode; text = $rendered }
}

$cloned = $false
if (-not (Test-Path -LiteralPath $coordinatorRootPath -PathType Container)) {
    $parent = Split-Path -Parent $coordinatorRootPath
    if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
        throw "coordinator_parent_missing:$parent"
    }
    $cloneOutput = @(& git clone --branch $branch --single-branch -- $repositoryUrl $coordinatorRootPath 2>&1)
    if ($LASTEXITCODE -ne 0) {
        throw "coordinator_clone_failed:$([string]::Join("`n", $cloneOutput))"
    }
    $cloned = $true
}

if (-not (Test-Path -LiteralPath (Join-Path $coordinatorRootPath '.git') -PathType Container)) {
    throw "coordinator_checkout_invalid:$coordinatorRootPath"
}
$originUrl = (Invoke-CheckedGit -WorkingDirectory $coordinatorRootPath -Arguments @('remote', 'get-url', 'origin')).text
if ($originUrl.TrimEnd('/') -ine $repositoryUrl.TrimEnd('/')) {
    throw "coordinator_origin_mismatch:$originUrl"
}
$currentBranch = (Invoke-CheckedGit -WorkingDirectory $coordinatorRootPath -Arguments @('branch', '--show-current')).text
if ($currentBranch -cne $branch) { throw "coordinator_branch_hold:$currentBranch" }
$upstream = (Invoke-CheckedGit -WorkingDirectory $coordinatorRootPath -Arguments @('rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{upstream}')).text
if ($upstream -cne "origin/$branch") { throw "coordinator_upstream_hold:$upstream" }
$dirty = (Invoke-CheckedGit -WorkingDirectory $coordinatorRootPath -Arguments @('status', '--porcelain=v1', '--untracked-files=all')).text
if (-not [string]::IsNullOrWhiteSpace($dirty)) { throw "coordinator_working_tree_dirty:$coordinatorRootPath" }

[void](Invoke-CheckedGit -WorkingDirectory $coordinatorRootPath -Arguments @('fetch', 'origin', '--prune'))
$remoteRef = "origin/$branch"
$minimumCheck = Invoke-CheckedGit -WorkingDirectory $coordinatorRootPath -Arguments @('merge-base', '--is-ancestor', $minimumRevision, $remoteRef) -AllowFailure
if ($minimumCheck.exitCode -ne 0) { throw "coordinator_minimum_revision_not_in_remote:$minimumRevision" }
$countsText = (Invoke-CheckedGit -WorkingDirectory $coordinatorRootPath -Arguments @('rev-list', '--left-right', '--count', "HEAD...$remoteRef")).text
$counts = @($countsText -split '\s+')
if ($counts.Count -ne 2) { throw "coordinator_ahead_behind_unreadable:$countsText" }
$localAhead = [int]$counts[0]
$remoteAhead = [int]$counts[1]
if ($localAhead -gt 0) { throw "coordinator_local_ahead_hold:$localAhead" }
if ($remoteAhead -gt 0) {
    [void](Invoke-CheckedGit -WorkingDirectory $coordinatorRootPath -Arguments @('pull', '--ff-only', 'origin', $branch))
}
$sourceRevision = (Invoke-CheckedGit -WorkingDirectory $coordinatorRootPath -Arguments @('rev-parse', 'HEAD')).text
$remoteRevision = (Invoke-CheckedGit -WorkingDirectory $coordinatorRootPath -Arguments @('rev-parse', $remoteRef)).text
if ($sourceRevision -cne $remoteRevision) { throw 'coordinator_sync_readback_mismatch' }
$minimumHeadCheck = Invoke-CheckedGit -WorkingDirectory $coordinatorRootPath -Arguments @('merge-base', '--is-ancestor', $minimumRevision, 'HEAD') -AllowFailure
if ($minimumHeadCheck.exitCode -ne 0) { throw "coordinator_minimum_revision_not_in_head:$minimumRevision" }

$setupPath = Join-Path $coordinatorRootPath 'scripts\setup-remote-coordinator.ps1'
if (-not (Test-Path -LiteralPath $setupPath -PathType Leaf)) { throw "canonical_setup_missing:$setupPath" }
$setupArguments = @{
    StorageRoot = $storageRootPath
    AdditionalShardRoot = @($AdditionalShardRoot)
    Role = $Role
    HostId = $HostId
    CodexHome = [IO.Path]::GetFullPath($CodexHome)
    FrontierBoardProgramsRoot = [IO.Path]::GetFullPath($FrontierBoardProgramsRoot)
    FrontierBoardStartMenuProgramsRoot = [IO.Path]::GetFullPath($FrontierBoardStartMenuProgramsRoot)
    FrontierBoardDataRoot = [IO.Path]::GetFullPath($FrontierBoardDataRoot)
}
if ($ForceProfile) { $setupArguments.ForceProfile = $true }
$setupOutput = @(& $setupPath @setupArguments)
$setupResult = ([string]::Join([Environment]::NewLine, $setupOutput)) | ConvertFrom-Json -Depth 30

[pscustomobject]@{
    classification = 'SIMPLIFIED_FRONTIER_LOOP_BOOTSTRAP'
    status = [string]$setupResult.status
    ready = [bool]$setupResult.ready
    clonedCoordinator = $cloned
    coordinatorRoot = $coordinatorRootPath
    coordinatorRevision = $sourceRevision
    minimumRevision = $minimumRevision
    frontierBoardRevision = [string]$setupResult.frontierBoard.sourceRevision
    installedCanary = [string]$setupResult.frontierBoard.installedCanary.status
    deployedCoreVersion = '10.2-frontier.1'
    legacyStateRestored = [bool]$setupResult.legacyStateRestored
    explicitProjectResumeRequired = $true
    canonicalSetup = $setupResult
} | ConvertTo-Json -Depth 30 -Compress
