[CmdletBinding()]
param(
    [string]$DataBundle = "",
    [switch]$SkipWebTests
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
    $env:SHIOK_DATA_BUNDLE = $DataBundle
    $env:NEXT_PUBLIC_DATA_BASE = "/data/$DataBundle/"

    & (Join-Path $PSScriptRoot "ensure-web-deps.ps1")
    if ($LASTEXITCODE -ne 0) { throw "web dependency install failed" }

    if (-not $SkipWebTests) {
        npm --prefix web test
        if ($LASTEXITCODE -ne 0) { throw "web tests failed" }
    }

    uv run python run.py publish --input "web/public/data/$DataBundle" --deploy --confirm-production
    if ($LASTEXITCODE -ne 0) { throw "production deploy failed" }
}
finally {
    Remove-Item Env:\SHIOK_DATA_BUNDLE -ErrorAction SilentlyContinue
    Remove-Item Env:\NEXT_PUBLIC_DATA_BASE -ErrorAction SilentlyContinue
    Pop-Location
}
