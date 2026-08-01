[CmdletBinding()]
param(
    [switch]$ConfirmCleanup
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

function Is-UnderRepoPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )
    $resolved = (Resolve-Path -LiteralPath $Path).Path
    return $resolved.StartsWith($RepoRoot, [System.StringComparison]::OrdinalIgnoreCase)
}

function Add-Candidate {
    param(
        [System.Collections.Generic.List[object]]$Candidates,
        [Parameter(Mandatory = $true)]
        [System.IO.FileSystemInfo]$Item,
        [Parameter(Mandatory = $true)]
        [string]$Reason
    )
    if (-not (Is-UnderRepoPath -Path $Item.FullName)) {
        throw "Refusing candidate outside repo: $($Item.FullName)"
    }
    $relative = $Item.FullName.Substring($RepoRoot.Length).TrimStart("\", "/")
    $Candidates.Add([pscustomobject]@{
        path = $Item.FullName
        relative = $relative
        reason = $Reason
        type = if ($Item.PSIsContainer) { "directory" } else { "file" }
        bytes = if ($Item.PSIsContainer) { $null } else { $Item.Length }
        last_write = $Item.LastWriteTime.ToString("o")
    }) | Out-Null
}

$Candidates = [System.Collections.Generic.List[object]]::new()

$logDir = Join-Path $RepoRoot "logs"
if (Test-Path $logDir) {
    Get-ChildItem -LiteralPath $logDir -File | ForEach-Object {
        Add-Candidate -Candidates $Candidates -Item $_ -Reason "runtime log"
    }
}

$tmpDir = Join-Path $RepoRoot "tmp"
if (Test-Path $tmpDir) {
    Get-ChildItem -LiteralPath $tmpDir -Force | Where-Object {
        $_.Name -like "browser-smoke-*" -or
        $_.Name -like "chrome-browser-qa*" -or
        $_.Name -like "vercel_source_*"
    } | ForEach-Object {
        Add-Candidate -Candidates $Candidates -Item $_ -Reason "temporary runtime/staged source"
    }
}

$summary = [pscustomobject]@{
    repo = $RepoRoot
    dry_run = -not $ConfirmCleanup
    candidates = $Candidates
    candidate_count = $Candidates.Count
}

if (-not $ConfirmCleanup) {
    $summary | ConvertTo-Json -Depth 6
    Write-Output "cleanup=dry_run"
    Write-Output "Rerun with -ConfirmCleanup to remove only the candidates listed above."
    return
}

foreach ($candidate in $Candidates) {
    $resolved = (Resolve-Path -LiteralPath $candidate.path).Path
    if (-not $resolved.StartsWith($RepoRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing delete outside repo: $resolved"
    }
    if ($candidate.type -eq "directory") {
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
    else {
        Remove-Item -LiteralPath $resolved -Force
    }
}

$summary | ConvertTo-Json -Depth 6
Write-Output "cleanup=ok"
