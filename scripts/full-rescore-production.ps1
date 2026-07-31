[CmdletBinding()]
param(
    [switch]$ConfirmFullBatch,
    [switch]$Deploy,
    [int]$Workers = 4,
    [int]$ChunkSize = 500,
    [string]$Stamp = "",
    [string]$ResumeRunRoot = "",
    [string]$PostalUniverse = "processed\postal_universe_candidate_full_registered_geocoded.parquet",
    [string]$Network = "processed\network_island.parquet"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")

if (-not $ConfirmFullBatch) {
    throw "Full rescore requires -ConfirmFullBatch after review. This avoids accidental long batch runs."
}
if ($Workers -lt 1 -or $Workers -gt 8) {
    throw "Workers must be between 1 and 8."
}
if ($ChunkSize -lt 1) {
    throw "ChunkSize must be positive."
}
$UniversePath = Join-Path $RepoRoot $PostalUniverse
$NetworkPath = Join-Path $RepoRoot $Network
$ResumeMode = [bool]$ResumeRunRoot

if ($ResumeMode) {
    $ResolvedResumeRunRoot = if ([System.IO.Path]::IsPathRooted($ResumeRunRoot)) {
        Resolve-Path $ResumeRunRoot
    }
    else {
        Resolve-Path (Join-Path $RepoRoot $ResumeRunRoot)
    }
    $RunRoot = $ResolvedResumeRunRoot.Path
    $RunName = Split-Path $RunRoot -Leaf
    if ($RunName -notmatch '^full_rescore_([0-9]{8}_[0-9]{6})$') {
        throw "ResumeRunRoot must point to a full_rescore_yyyyMMdd_HHmmss directory: $RunRoot"
    }
    $Stamp = $Matches[1]
}
else {
    if (-not $Stamp) {
        $Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    }
    if ($Stamp -notmatch '^[0-9]{8}_[0-9]{6}$') {
        throw "Stamp must match yyyyMMdd_HHmmss."
    }
    $RunRoot = Join-Path $RepoRoot "processed\score_batches\full_rescore_$Stamp"
}

$PartitionDir = Join-Path $RunRoot "partitions"
$LogDir = Join-Path $RunRoot "logs"
$CombinedDir = Join-Path $RunRoot "combined"
$CombinedChunksDir = Join-Path $CombinedDir "chunks"
$BundleName = "generated_$Stamp"
$ExportDir = Join-Path $RepoRoot "web\public\data\$BundleName"
$BundleConfig = Join-Path $RepoRoot "web\data-bundle.json"

if (-not (Test-Path $UniversePath)) { throw "Postal universe not found: $UniversePath" }
if (-not (Test-Path $NetworkPath)) { throw "Network not found: $NetworkPath" }
if ((Test-Path $RunRoot) -and -not $ResumeMode) { throw "Run directory already exists: $RunRoot" }
if ($ResumeMode -and -not (Test-Path $RunRoot)) { throw "Run directory not found: $RunRoot" }
if (Test-Path $ExportDir) { throw "Export bundle already exists: $ExportDir" }

New-Item -ItemType Directory -Force -Path $PartitionDir, $LogDir, $CombinedChunksDir | Out-Null

Push-Location $RepoRoot
try {
    if (-not $ResumeMode) {
        $partitionScript = @"
from pathlib import Path
import sys
import pandas as pd

universe = Path(sys.argv[1])
out_dir = Path(sys.argv[2])
workers = int(sys.argv[3])
rows = pd.read_parquet(universe)
rows["postal_code"] = rows["postal_code"].astype(str).str.zfill(6)
rows = rows.sort_values("postal_code", kind="stable").reset_index(drop=True)
for index in range(workers):
    part = rows.iloc[index::workers].copy()
    part_path = out_dir / f"part{index + 1:02d}_of{workers:02d}.parquet"
    part.to_parquet(part_path, index=False)
    print(f"{part_path} rows={len(part)} ready={(part['status'] == 'READY_TO_SCORE').sum()}")
"@
        $partitionScript | uv run python - $UniversePath $PartitionDir $Workers
        if ($LASTEXITCODE -ne 0) { throw "partitioning failed" }
    }
    else {
        Write-Output "resuming_run_root=$RunRoot"
    }

    $processes = @()
    for ($index = 1; $index -le $Workers; $index++) {
        $partName = "part{0:D2}_of{1:D2}.parquet" -f $index, $Workers
        $partPath = Join-Path $PartitionDir $partName
        if (-not (Test-Path $partPath)) { throw "Missing partition for resume/run: $partPath" }
        $partOut = Join-Path $RunRoot ("part{0:D2}_of{1:D2}" -f $index, $Workers)
        $logPrefix = if ($ResumeMode) { "resume_part{0:D2}" -f $index } else { "part{0:D2}" -f $index }
        $stdout = Join-Path $LogDir "$logPrefix.out.log"
        $stderr = Join-Path $LogDir "$logPrefix.err.log"
        $args = @(
            "run", "python", "run.py", "score-batch",
            "--postal-universe", $partPath,
            "--network", $NetworkPath,
            "--output-dir", $partOut,
            "--full-batch",
            "--confirm-full-batch",
            "--chunk-size", "$ChunkSize"
        )
        $processes += Start-Process -FilePath "uv" -ArgumentList $args -WorkingDirectory $RepoRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput $stdout -RedirectStandardError $stderr
    }

    foreach ($process in $processes) {
        $process.WaitForExit()
    }
    $failed = $processes | Where-Object { $_.ExitCode -ne 0 }
    if ($failed) {
        throw "One or more score-batch workers failed. Check logs: $LogDir"
    }

    for ($index = 1; $index -le $Workers; $index++) {
        $partOut = Join-Path $RunRoot ("part{0:D2}_of{1:D2}" -f $index, $Workers)
        $chunkDir = Join-Path $partOut "chunks"
        if (-not (Test-Path $chunkDir)) { throw "Missing chunk directory: $chunkDir" }
        Get-ChildItem $chunkDir -Filter "chunk_*.json" | Sort-Object Name | ForEach-Object {
            $targetName = "chunk_part{0:D2}_{1}" -f $index, $_.Name.Substring(6)
            Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $CombinedChunksDir $targetName)
        }
    }

    $manifestScript = @"
from pathlib import Path
import json
import sys

combined = Path(sys.argv[1])
chunks = sorted((combined / "chunks").glob("chunk_*.json"))
records = 0
states = {}
for path in chunks:
    payload = json.loads(path.read_text(encoding="utf-8"))
    records += len(payload)
    for item in payload:
        state = str(item.get("state"))
        states[state] = states.get(state, 0) + 1
(combined / "combined_manifest.json").write_text(
    json.dumps({"ok": True, "chunk_count": len(chunks), "records": records, "state_counts": dict(sorted(states.items()))}, indent=2, sort_keys=True),
    encoding="utf-8",
)
print(json.dumps({"chunk_count": len(chunks), "records": records, "state_counts": dict(sorted(states.items()))}, indent=2, sort_keys=True))
"@
    $manifestScript | uv run python - $CombinedDir
    if ($LASTEXITCODE -ne 0) { throw "combined manifest failed" }

    uv run python run.py export --records-dir $CombinedDir --output $ExportDir --full-batch --confirm-full-batch --postal-universe $UniversePath --network $NetworkPath
    if ($LASTEXITCODE -ne 0) { throw "export failed" }

    uv run python run.py validate --input $ExportDir
    if ($LASTEXITCODE -ne 0) { throw "validate failed" }

    [System.IO.File]::WriteAllText(
        $BundleConfig,
        "{`n  `"bundle`": `"$BundleName`"`n}",
        [System.Text.UTF8Encoding]::new($false)
    )

    if ($Deploy) {
        & (Join-Path $PSScriptRoot "deploy-production.ps1") -DataBundle $BundleName
        if ($LASTEXITCODE -ne 0) { throw "deploy failed" }
    }

    Write-Output "bundle=$BundleName"
    Write-Output "run_root=$RunRoot"
}
finally {
    Pop-Location
}
