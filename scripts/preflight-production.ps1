[CmdletBinding()]
param(
    [string]$DataBundle = "",
    [switch]$SkipWebTests,
    [switch]$SkipNetworkPreflight
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$WebDir = Join-Path $RepoRoot "web"
$ConfigPath = Join-Path $WebDir "data-bundle.json"

if (-not $DataBundle) {
    $DataBundle = (Get-Content $ConfigPath -Raw | ConvertFrom-Json).bundle
}

if (-not $DataBundle -or $DataBundle.Contains("/") -or $DataBundle.Contains("\")) {
    throw "Invalid data bundle: $DataBundle"
}

$DataDir = Join-Path $WebDir "public\data\$DataBundle"
if (-not (Test-Path (Join-Path $DataDir "manifest.json"))) {
    throw "Data bundle is missing manifest.json: $DataDir"
}

Push-Location $RepoRoot
try {
    Write-Output "== S.H.I.O.K. production preflight =="
    Write-Output "repo=$RepoRoot"
    Write-Output "bundle=$DataBundle"
    Write-Output "data_dir=$DataDir"

    Write-Output ""
    Write-Output "== Git status =="
    git status --short --branch
    if ($LASTEXITCODE -ne 0) { throw "git status failed" }

    if (-not $SkipNetworkPreflight) {
        Write-Output ""
        Write-Output "== Network QA =="
        uv run python run.py network-qa --area island
        if ($LASTEXITCODE -ne 0) { throw "network QA failed" }

        Write-Output ""
        Write-Output "== Network preflight =="
        uv run python run.py network-preflight --area island --skip-geometry-inspection
        if ($LASTEXITCODE -ne 0) { throw "network preflight failed" }
    }

    Write-Output ""
    Write-Output "== Static data validation =="
    uv run python run.py validate --input "web/public/data/$DataBundle"
    if ($LASTEXITCODE -ne 0) { throw "static data validation failed" }

    if (-not $SkipWebTests) {
        Write-Output ""
        Write-Output "== Web tests =="
        npm --prefix web test
        if ($LASTEXITCODE -ne 0) { throw "web tests failed" }
    }

    Write-Output ""
    Write-Output "preflight=ok"
}
finally {
    Pop-Location
}
