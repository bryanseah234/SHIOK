param(
    [string]$DataBundle = "generated_20260805_prefer_scored_routed",
    [int]$SampleSize = 200000,
    [int]$BatchSize = 1000,
    [double]$DelaySec = 2.0,
    [int]$MaxBatches = 0,
    [string]$RunId = "",
    [switch]$IncludeResults
)

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $RepoRoot

if (-not $RunId) {
    $RunId = Get-Date -Format "yyyyMMdd_HHmmss"
}

$BundleDir = Join-Path $RepoRoot "web\public\data\$DataBundle"
$RunDir = Join-Path $RepoRoot "qa\onemap_full_validation_$RunId"
$SamplePath = Join-Path $RunDir "full_scored_sample.json"
$StatusPath = Join-Path $RunDir "status.json"
$LatestReportPath = Join-Path $RunDir "latest_cached_report.json"
$CacheDir = Join-Path $RepoRoot "raw\validation\onemap_walk_od"

New-Item -ItemType Directory -Force -Path $RunDir | Out-Null

function Write-Status {
    param(
        [string]$Phase,
        [int]$BatchIndex = 0,
        [object]$CollectReport = $null,
        [object]$EvalReport = $null,
        [string]$Message = ""
    )

    $status = [ordered]@{
        ok = $true
        generated_at = (Get-Date).ToUniversalTime().ToString("o")
        phase = $Phase
        data_bundle = $DataBundle
        run_id = $RunId
        run_dir = $RunDir
        sample_path = $SamplePath
        cache_dir = $CacheDir
        batch_size = $BatchSize
        delay_sec = $DelaySec
        batch_index = $BatchIndex
        message = $Message
    }
    if ($CollectReport -ne $null) {
        $status.collect = $CollectReport
    }
    if ($EvalReport -ne $null) {
        $status.evaluate = [ordered]@{
            sample_size = $EvalReport.sample_size
            cached_results = $EvalReport.cached_results
            missing_cache_results = $EvalReport.missing_cache_results
            invalid_cache_results = $EvalReport.invalid_cache_results
            gate_passed = $EvalReport.gate_passed
            median_abs_pct_delta = $EvalReport.median_abs_pct_delta
            p95_abs_pct_delta = $EvalReport.p95_abs_pct_delta
        }
    }
    $status | ConvertTo-Json -Depth 8 | Set-Content -Path $StatusPath -Encoding UTF8
}

function Invoke-Logged {
    param(
        [string]$Name,
        [string[]]$Command,
        [string]$LogPath,
        [switch]$DiscardStdout
    )

    $header = "[$((Get-Date).ToString("o"))] START $Name`n$($Command -join " ")"
    Set-Content -Path $LogPath -Value $header -Encoding UTF8
    $exe = $Command[0]
    $cmdArgs = @()
    if ($Command.Count -gt 1) {
        $cmdArgs = $Command[1..($Command.Count - 1)]
    }
    if ($DiscardStdout) {
        & $exe @cmdArgs 1>$null 2>>$LogPath
    }
    else {
        & $exe @cmdArgs *>&1 | Tee-Object -FilePath $LogPath -Append | Out-Null
    }
    $exit = $LASTEXITCODE
    Add-Content -Path $LogPath -Value "[$((Get-Date).ToString("o"))] EXIT $Name code=$exit"
    Write-Output $exit
}

if (-not (Test-Path $BundleDir)) {
    Write-Status -Phase "failed" -Message "Bundle directory not found: $BundleDir"
    throw "Bundle directory not found: $BundleDir"
}

Write-Status -Phase "planning"
if (-not (Test-Path $SamplePath)) {
    $planLog = Join-Path $RunDir "plan.log"
    $planCmd = @(
        "uv", "run", "python", "run.py", "onemap-validation", "plan",
        "--bundle-dir", $BundleDir,
        "--sample-size", "$SampleSize",
        "--onemap-delay-sec", "$DelaySec",
        "--output", $SamplePath
    )
    $planExit = Invoke-Logged -Name "plan-full-sample" -Command $planCmd -LogPath $planLog -DiscardStdout
    if ($planExit -ne 0) {
        Write-Status -Phase "failed" -Message "Full OneMap sample planning failed; see $planLog"
        exit $planExit
    }
}

$batchIndex = 0
while ($true) {
    if ($MaxBatches -gt 0 -and $batchIndex -ge $MaxBatches) {
        Write-Status -Phase "paused" -BatchIndex $batchIndex -Message "Reached MaxBatches=$MaxBatches"
        exit 0
    }

    $batchIndex += 1
    Write-Status -Phase "collecting" -BatchIndex $batchIndex
    $collectPath = Join-Path $RunDir ("collect_batch_{0:D5}.json" -f $batchIndex)
    $collectLog = Join-Path $RunDir ("collect_batch_{0:D5}.log" -f $batchIndex)
    $collectCmd = @(
        "uv", "run", "python", "run.py", "onemap-validation", "collect",
        "--sample", $SamplePath,
        "--cache-dir", $CacheDir,
        "--output", $collectPath,
        "--delay-sec", "$DelaySec",
        "--limit", "$BatchSize",
        "--confirm-onemap-collection",
        "--cache-errors"
    )
    [void](Invoke-Logged -Name "collect-batch-$batchIndex" -Command $collectCmd -LogPath $collectLog)
    $collectReport = Get-Content $collectPath -Raw | ConvertFrom-Json

    Write-Status -Phase "evaluating" -BatchIndex $batchIndex -CollectReport $collectReport
    $evalPath = Join-Path $RunDir ("cached_report_batch_{0:D5}.json" -f $batchIndex)
    $evalLog = Join-Path $RunDir ("evaluate_batch_{0:D5}.log" -f $batchIndex)
    $evalCmd = @(
        "uv", "run", "python", "run.py", "onemap-validation", "evaluate",
        "--sample", $SamplePath,
        "--cache-dir", $CacheDir,
        "--output", $evalPath
    )
    if ($IncludeResults) {
        $evalCmd += "--include-results"
    }
    [void](Invoke-Logged -Name "evaluate-batch-$batchIndex" -Command $evalCmd -LogPath $evalLog)
    Copy-Item -Force -Path $evalPath -Destination $LatestReportPath
    $evalReport = Get-Content $evalPath -Raw | ConvertFrom-Json

    Write-Status -Phase "running" -BatchIndex $batchIndex -CollectReport $collectReport -EvalReport $evalReport
    if ([int]$evalReport.missing_cache_results -le 0) {
        Write-Status -Phase "complete" -BatchIndex $batchIndex -CollectReport $collectReport -EvalReport $evalReport
        exit 0
    }

    if ([int]$collectReport.queued_requests -le 0 -or [int]$collectReport.http_requests -le 0) {
        Write-Status -Phase "blocked" -BatchIndex $batchIndex -CollectReport $collectReport -EvalReport $evalReport -Message "No queued/http requests but evaluation still has missing cache rows."
        exit 2
    }
}
