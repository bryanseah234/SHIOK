[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$DataBundle,
    [switch]$SkipRemoteCheck
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$WebDir = Join-Path $RepoRoot "web"
$ConfigPath = Join-Path $WebDir "data-bundle.json"
$RootIgnorePath = Join-Path $RepoRoot ".vercelignore"
$WebIgnorePath = Join-Path $WebDir ".vercelignore"

if (-not $DataBundle -or $DataBundle.Contains("/") -or $DataBundle.Contains("\")) {
    throw "Invalid data bundle: $DataBundle"
}

$DataDir = Join-Path $WebDir "public\data\$DataBundle"
$ManifestPath = Join-Path $DataDir "manifest.json"
if (-not (Test-Path $ManifestPath)) {
    throw "Data bundle is missing manifest.json: $DataDir"
}

Push-Location $RepoRoot
try {
    uv run python run.py validate --input "web/public/data/$DataBundle"
    if ($LASTEXITCODE -ne 0) { throw "local bundle validation failed" }

    $LocalManifest = Get-Content $ManifestPath -Raw | ConvertFrom-Json
    if (-not $SkipRemoteCheck) {
        $RemoteUrl = "https://sgshiok.vercel.app/data/$DataBundle/manifest.json"
        try {
            $RemoteResponse = Invoke-WebRequest -Uri $RemoteUrl -UseBasicParsing -TimeoutSec 30
        }
        catch {
            throw "remote bundle is not reachable yet: $RemoteUrl"
        }
        $RemoteManifest = $RemoteResponse.Content | ConvertFrom-Json
        if (
            [int]$RemoteManifest.provenance.record_count -ne
            [int]$LocalManifest.provenance.record_count
        ) {
            throw "remote manifest record_count does not match local bundle"
        }
        if (
            [string]$RemoteManifest.generated_at -ne
            [string]$LocalManifest.generated_at
        ) {
            throw "remote manifest generated_at does not match local bundle"
        }
    }

    [System.IO.File]::WriteAllText(
        $ConfigPath,
        "{`n  `"bundle`": `"$DataBundle`"`n}",
        [System.Text.UTF8Encoding]::new($false)
    )
    [System.IO.File]::WriteAllText(
        $RootIgnorePath,
        @"
.env
.venv/
.pytest_cache/
.ruff_cache/
.mypy_cache/
.next/
__pycache__/
*.pyc

raw/
processed/
logs/
qa/
tmp/

web/.next/
web/.vercel/
web/node_modules/
web/public/data/generated_*/
!web/public/data/$DataBundle/
!web/public/data/$DataBundle/**
"@,
        [System.Text.UTF8Encoding]::new($false)
    )
    [System.IO.File]::WriteAllText(
        $WebIgnorePath,
        @"
.next/
.vercel/
node_modules/

public/data/generated_*/
!public/data/$DataBundle/
!public/data/$DataBundle/**
"@,
        [System.Text.UTF8Encoding]::new($false)
    )
    Write-Output "activated_bundle=$DataBundle"
    if ($SkipRemoteCheck) {
        Write-Warning "Remote production manifest check skipped."
    }
}
finally {
    Pop-Location
}
