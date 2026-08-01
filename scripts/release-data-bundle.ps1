[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$DataBundle,
    [switch]$ConfirmProduction,
    [switch]$SkipWebTests,
    [switch]$SkipGitPush,
    [switch]$PlanOnly,
    [int]$RemoteWaitSeconds = 900,
    [int]$RemotePollSeconds = 20,
    [string]$CommitMessage = ""
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$WebDir = Join-Path $RepoRoot "web"
$ConfigPath = Join-Path $WebDir "data-bundle.json"

if (-not $DataBundle -or $DataBundle.Contains("/") -or $DataBundle.Contains("\")) {
    throw "Invalid data bundle: $DataBundle"
}

if ($RemoteWaitSeconds -lt 0) {
    throw "RemoteWaitSeconds must be >= 0"
}

if ($RemotePollSeconds -lt 1) {
    throw "RemotePollSeconds must be >= 1"
}

if (-not $CommitMessage) {
    $CommitMessage = "data: activate $DataBundle"
}

$DataDir = Join-Path $WebDir "public\data\$DataBundle"
$ManifestPath = Join-Path $DataDir "manifest.json"
if (-not (Test-Path $ManifestPath)) {
    throw "Data bundle is missing manifest.json: $DataDir"
}

$Manifest = Get-Content $ManifestPath -Raw | ConvertFrom-Json
$RecordCount = $Manifest.provenance.record_count
$GeneratedAt = $Manifest.generated_at

Write-Output "== S.H.I.O.K. data bundle release =="
Write-Output "repo=$RepoRoot"
Write-Output "bundle=$DataBundle"
Write-Output "record_count=$RecordCount"
Write-Output "generated_at=$GeneratedAt"

if ($PlanOnly) {
    Write-Output ""
    Write-Output "plan_only=true"
    Write-Output "commands:"
    Write-Output ".\scripts\release-data-bundle.bat -DataBundle $DataBundle -ConfirmProduction"
    Write-Output ""
    Write-Output "This will validate locally, direct-deploy the ignored data bundle, wait for the production manifest, activate web/data-bundle.json, preflight, commit, and push."
    return
}

if (-not $ConfirmProduction) {
    throw "Refusing production release without -ConfirmProduction. Use -PlanOnly to print the release plan."
}

Push-Location $RepoRoot
try {
    Write-Output ""
    Write-Output "== Pre-release git status =="
    git status --short --branch
    if ($LASTEXITCODE -ne 0) { throw "git status failed" }

    git diff --quiet -- "web/data-bundle.json"
    if ($LASTEXITCODE -ne 0) {
        throw "web/data-bundle.json has uncommitted changes before activation"
    }

    Write-Output ""
    Write-Output "== Local bundle validation =="
    uv run python run.py validate --input "web/public/data/$DataBundle"
    if ($LASTEXITCODE -ne 0) { throw "local bundle validation failed" }

    Write-Output ""
    Write-Output "== Direct production deploy =="
    $DeployArgs = @{ DataBundle = $DataBundle }
    if ($SkipWebTests) { $DeployArgs.SkipWebTests = $true }
    & (Join-Path $PSScriptRoot "deploy-production.ps1") @DeployArgs
    if ($LASTEXITCODE -ne 0) { throw "production deploy failed" }

    Write-Output ""
    Write-Output "== Activate after remote manifest is reachable =="
    $Deadline = (Get-Date).AddSeconds($RemoteWaitSeconds)
    while ($true) {
        try {
            & (Join-Path $PSScriptRoot "activate-data-bundle.ps1") -DataBundle $DataBundle
            if ($LASTEXITCODE -ne 0) { throw "activation failed" }
            break
        }
        catch {
            if ((Get-Date) -ge $Deadline) {
                throw
            }
            Write-Warning "Remote manifest not ready yet. Waiting $RemotePollSeconds seconds before retry."
            Start-Sleep -Seconds $RemotePollSeconds
        }
    }

    Write-Output ""
    Write-Output "== Production preflight =="
    & (Join-Path $PSScriptRoot "preflight-production.ps1") -DataBundle $DataBundle -SkipNetworkPreflight
    if ($LASTEXITCODE -ne 0) { throw "production preflight failed" }

    Write-Output ""
    Write-Output "== Commit activation =="
    git diff --quiet -- "web/data-bundle.json"
    if ($LASTEXITCODE -eq 0) {
        Write-Output "activation_commit=skipped_no_change"
    }
    else {
        git add "web/data-bundle.json"
        if ($LASTEXITCODE -ne 0) { throw "git add failed" }
        git commit -m $CommitMessage
        if ($LASTEXITCODE -ne 0) { throw "git commit failed" }
    }

    if (-not $SkipGitPush) {
        Write-Output ""
        Write-Output "== Push main =="
        git push origin main
        if ($LASTEXITCODE -ne 0) { throw "git push failed" }
    }
    else {
        Write-Warning "Git push skipped."
    }

    Write-Output ""
    Write-Output "release=ok"
    Write-Output "production_url=https://sgshiok.vercel.app/"
}
finally {
    Pop-Location
}
