#requires -Version 7.0

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$root = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$contractPath = Join-Path $root 'config\canonical-source.json'
$skillPath = Join-Path $root 'SKILL.md'
$corePath = Join-Path $root 'core\supervise-repo-loop.md'
$bootstrapPath = Join-Path $root 'scripts\bootstrap-frontier-loop.ps1'
foreach ($path in @($contractPath, $skillPath, $corePath, $bootstrapPath)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "required_file_missing:$path" }
}

$contract = Get-Content -Raw -LiteralPath $contractPath | ConvertFrom-Json
if ([int]$contract.schemaVersion -ne 1) { throw 'contract_schema_mismatch' }
if ([string]$contract.coordinator.repositoryUrl -cne 'https://github.com/YuShimoji/project-reflection-coordinator.git') { throw 'coordinator_remote_mismatch' }
if ([string]$contract.coordinator.branch -cne 'main') { throw 'coordinator_branch_mismatch' }
if ([string]$contract.coordinator.minimumRevision -notmatch '\A[0-9a-f]{40}\z') { throw 'coordinator_revision_invalid' }
if ([string]$contract.validatedContract.frontierBoardRevision -notmatch '\A[0-9a-f]{40}\z') { throw 'frontierboard_revision_invalid' }

$skill = Get-Content -Raw -LiteralPath $skillPath
$core = Get-Content -Raw -LiteralPath $corePath
$bootstrap = Get-Content -Raw -LiteralPath $bootstrapPath
foreach ($legacyToken in @('protocol-v2.md', 'scheduler-v3.md', 'supervise_repo_loop.py', 'SUPERVISOR_WORK_ORDER_REQUESTED')) {
    if ($skill.Contains($legacyToken) -or $core.Contains($legacyToken) -or $bootstrap.Contains($legacyToken)) {
        throw "legacy_runtime_token_present:$legacyToken"
    }
}
foreach ($requiredToken in @('Coordinator', 'Worker(PROBE)', 'FrontierBoard')) {
    if (-not $core.Contains($requiredToken)) { throw "simple_core_token_missing:$requiredToken" }
}
foreach ($requiredToken in @("'fetch', 'origin', '--prune'", "'pull', '--ff-only'", 'merge-base', 'setup-remote-coordinator.ps1', 'explicitProjectResumeRequired')) {
    if (-not $bootstrap.Contains($requiredToken)) { throw "bootstrap_guard_missing:$requiredToken" }
}
foreach ($forbiddenToken in @('reset --hard', 'git reset', 'git clean', 'git stash', 'git rebase', '--force')) {
    if ($bootstrap.Contains($forbiddenToken)) { throw "destructive_bootstrap_token_present:$forbiddenToken" }
}

$tokens = $null
$errors = $null
[void][Management.Automation.Language.Parser]::ParseFile($bootstrapPath, [ref]$tokens, [ref]$errors)
if (@($errors).Count -ne 0) { throw "bootstrap_parser_failed:$($errors[0].Message)" }

$trackedLegacyPaths = @(git -C $root ls-files -- 'references/**' 'schemas/**' 'scripts/supervise_repo_loop.py' 'docs/scheduler-v3.md')
if ($trackedLegacyPaths.Count -ne 0) { throw "legacy_runtime_paths_still_tracked:$($trackedLegacyPaths -join ',')" }

[pscustomobject]@{
    status = 'PASS'
    topology = 'Coordinator -> Worker(PROBE) -> Coordinator -> FrontierBoard'
    coordinatorMinimumRevision = [string]$contract.coordinator.minimumRevision
    frontierBoardRevision = [string]$contract.validatedContract.frontierBoardRevision
    oldV2ActiveFiles = 0
    destructiveGitOperations = $false
    explicitProjectResumeRequired = $true
} | ConvertTo-Json -Compress
