[CmdletBinding()]
param(
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$WebDir = Join-Path $RepoRoot "web"
$PackageLock = Join-Path $WebDir "package-lock.json"
$RequiredBins = @(
    (Join-Path $WebDir "node_modules\.bin\vitest.cmd"),
    (Join-Path $WebDir "node_modules\.bin\next.cmd")
)

if (-not (Test-Path $PackageLock)) {
    throw "web/package-lock.json is required for deterministic npm ci"
}

$Missing = @()
foreach ($Path in $RequiredBins) {
    if (-not (Test-Path $Path)) {
        $Missing += $Path
    }
}

if ($Force -or $Missing.Count -gt 0) {
    Write-Output "web_dependencies=installing"
    if ($Missing.Count -gt 0) {
        Write-Output "missing=$($Missing -join ',')"
    }
    npm --prefix web ci
    if ($LASTEXITCODE -ne 0) {
        throw "npm ci failed with exit=$LASTEXITCODE"
    }
}
else {
    Write-Output "web_dependencies=present"
}
