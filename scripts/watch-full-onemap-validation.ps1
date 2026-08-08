param(
    [string]$DataBundle = "generated_20260805_prefer_scored_routed",
    [string]$RunId = "20260808_full_scored",
    [int]$SampleSize = 200000,
    [int]$BatchSize = 1000,
    [double]$DelaySec = 2.0,
    [int]$PollSeconds = 300,
    [int]$StaleMinutes = 20,
    [int]$MaxRestarts = 25,
    [int]$MaxLoops = 0
)

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $RepoRoot

$RunDir = Join-Path $RepoRoot "qa\onemap_full_validation_$RunId"
$StatusPath = Join-Path $RunDir "status.json"
$CacheDir = Join-Path $RepoRoot "raw\validation\onemap_walk_od"
$WatchLog = Join-Path $RunDir "watchdog.log"
$WatchStatusPath = Join-Path $RunDir "watchdog_status.json"

New-Item -ItemType Directory -Force -Path $RunDir | Out-Null

function Write-WatchLog {
    param([string]$Message)
    $line = "[$((Get-Date).ToString("o"))] $Message"
    Add-Content -Path $WatchLog -Value $line
}

function Get-RunnerProcesses {
    Get-CimInstance Win32_Process |
        Where-Object {
            $_.CommandLine -like "*full-onemap-validation.ps1*" -and
            $_.CommandLine -like "*$RunId*" -and
            $_.CommandLine -notlike "*watch-full-onemap-validation.ps1*"
        }
}

function Get-ChildProcessIds {
    param([int]$RootProcessId)
    $children = Get-CimInstance Win32_Process | Where-Object { $_.ParentProcessId -eq $RootProcessId }
    foreach ($child in $children) {
        Get-ChildProcessIds -RootProcessId ([int]$child.ProcessId)
        [int]$child.ProcessId
    }
}

function Stop-RunnerTree {
    $runnerProcesses = @(Get-RunnerProcesses)
    foreach ($runner in $runnerProcesses) {
        $ids = @((Get-ChildProcessIds -RootProcessId ([int]$runner.ProcessId))) + [int]$runner.ProcessId
        foreach ($id in ($ids | Select-Object -Unique)) {
            Stop-Process -Id $id -Force -ErrorAction SilentlyContinue
        }
    }
}

function Start-Runner {
    $scriptPath = Join-Path $RepoRoot "scripts\full-onemap-validation.ps1"
    $args = @(
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        $scriptPath,
        "-DataBundle",
        $DataBundle,
        "-SampleSize",
        "$SampleSize",
        "-BatchSize",
        "$BatchSize",
        "-DelaySec",
        "$DelaySec",
        "-RunId",
        $RunId
    )
    $process = Start-Process -FilePath "powershell.exe" -ArgumentList $args -PassThru -WindowStyle Hidden
    Write-WatchLog "started runner pid=$($process.Id)"
    return $process.Id
}

function Read-Status {
    if (-not (Test-Path $StatusPath)) {
        return $null
    }
    try {
        return Get-Content $StatusPath -Raw | ConvertFrom-Json
    }
    catch {
        Write-WatchLog "status read failed: $($_.Exception.Message)"
        return $null
    }
}

function Latest-CacheWrite {
    $latest = Get-ChildItem $CacheDir -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if ($null -eq $latest) {
        return $null
    }
    return $latest.LastWriteTime
}

function Latest-CollectProgress {
    $latest = Get-ChildItem $RunDir -Filter "collect_progress_batch_*.json" -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if ($null -eq $latest) {
        return $null
    }
    try {
        $payload = Get-Content $latest.FullName -Raw | ConvertFrom-Json
        return [ordered]@{
            path = $latest.FullName
            last_write_time = $latest.LastWriteTime.ToString("o")
            batch_http_requests = $payload.http_requests
            batch_queued_requests = $payload.queued_requests
            batch_pending_remaining = $payload.pending_remaining
            current_postal = $payload.current_postal
            last_progress_at = $payload.last_progress_at
            errors_count = @($payload.errors).Count
        }
    }
    catch {
        Write-WatchLog "collect progress read failed: $($_.Exception.Message)"
        return $null
    }
}

$restartCount = 0
$loop = 0
Write-WatchLog "watchdog started run_id=$RunId poll_seconds=$PollSeconds stale_minutes=$StaleMinutes"

while ($true) {
    $loop += 1
    $status = Read-Status
    $phase = if ($null -eq $status) { "missing_status" } else { [string]$status.phase }
    $runnerProcesses = @(Get-RunnerProcesses)
    $latestCacheWrite = Latest-CacheWrite
    $collectProgress = Latest-CollectProgress
    $cacheAgeMinutes = $null
    if ($null -ne $latestCacheWrite) {
        $cacheAgeMinutes = ((Get-Date) - $latestCacheWrite).TotalMinutes
    }

    $watchStatus = [ordered]@{
        ok = $true
        generated_at = (Get-Date).ToUniversalTime().ToString("o")
        run_id = $RunId
        phase = $phase
        runner_pids = @($runnerProcesses | ForEach-Object { [int]$_.ProcessId })
        restart_count = $restartCount
        latest_cache_write = if ($null -eq $latestCacheWrite) { $null } else { $latestCacheWrite.ToString("o") }
        cache_age_minutes = if ($null -eq $cacheAgeMinutes) { $null } else { [math]::Round($cacheAgeMinutes, 2) }
        collect_progress = $collectProgress
        status_path = $StatusPath
        watch_log = $WatchLog
    }
    $watchStatus | ConvertTo-Json -Depth 6 | Set-Content -Path $WatchStatusPath -Encoding UTF8

    if ($phase -eq "complete") {
        Write-WatchLog "complete; exiting watchdog"
        exit 0
    }

    $stale = $false
    if ($runnerProcesses.Count -eq 0) {
        $stale = $true
        Write-WatchLog "runner missing phase=$phase"
    }
    elseif ($null -ne $cacheAgeMinutes -and $cacheAgeMinutes -ge $StaleMinutes -and $phase -eq "collecting") {
        $stale = $true
        Write-WatchLog "runner stale phase=$phase cache_age_minutes=$([math]::Round($cacheAgeMinutes, 2))"
    }

    if ($stale) {
        if ($restartCount -ge $MaxRestarts) {
            Write-WatchLog "max restarts reached; exiting watchdog"
            exit 2
        }
        Stop-RunnerTree
        Start-Sleep -Seconds 5
        [void](Start-Runner)
        $restartCount += 1
    }

    if ($MaxLoops -gt 0 -and $loop -ge $MaxLoops) {
        Write-WatchLog "max loops reached; exiting watchdog"
        exit 0
    }

    Start-Sleep -Seconds $PollSeconds
}
