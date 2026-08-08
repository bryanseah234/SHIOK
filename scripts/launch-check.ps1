[CmdletBinding()]
param(
    [string]$DataBundle = "generated_20260801_165500",
    [int]$Port = 3110,
    [switch]$SkipPythonTests,
    [switch]$SkipWebTests,
    [switch]$SkipBuild,
    [switch]$SkipBrowser,
    [switch]$WaiveOneMapValidation,
    [string]$OwnerApprovalNote = ""
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$WebDir = Join-Path $RepoRoot "web"
$BundleDir = Join-Path $WebDir "public\data\$DataBundle"
$Timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$LogDir = Join-Path $RepoRoot "qa\launch_check_$Timestamp"
$CurrentStep = $null

if (-not (Test-Path (Join-Path $BundleDir "manifest.json"))) {
    throw "Bundle manifest not found: $BundleDir"
}
if ($Port -lt 1) {
    throw "Invalid port: $Port"
}

function Test-PortInUse {
    param([int]$CandidatePort)
    $listener = Get-NetTCPConnection -LocalPort $CandidatePort -State Listen -ErrorAction SilentlyContinue
    return $null -ne $listener
}

function Find-AvailablePort {
    param([int]$StartPort)
    for ($candidate = $StartPort; $candidate -lt ($StartPort + 100); $candidate++) {
        if (-not (Test-PortInUse -CandidatePort $candidate)) {
            return $candidate
        }
    }
    throw "No available local port found from $StartPort to $($StartPort + 99)"
}

function Get-ChildProcessIds {
    param([int]$ParentId)
    $children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$ParentId" -ErrorAction SilentlyContinue)
    $ids = @()
    foreach ($child in $children) {
        $ids += [int]$child.ProcessId
        $ids += Get-ChildProcessIds -ParentId ([int]$child.ProcessId)
    }
    return $ids
}

function Stop-ProcessTree {
    param([int]$RootProcessId)
    $ids = @(Get-ChildProcessIds -ParentId $RootProcessId) + @($RootProcessId)
    foreach ($id in ($ids | Select-Object -Unique)) {
        Stop-Process -Id $id -Force -ErrorAction SilentlyContinue
    }
}

function Stop-NewListenerOnPort {
    param(
        [int]$ListenerPort,
        [datetime]$StartedAfter
    )
    $listeners = @(Get-NetTCPConnection -LocalPort $ListenerPort -State Listen -ErrorAction SilentlyContinue)
    foreach ($listener in $listeners) {
        $process = Get-Process -Id $listener.OwningProcess -ErrorAction SilentlyContinue
        if ($process -and $process.StartTime -ge $StartedAfter) {
            Stop-ProcessTree -RootProcessId ([int]$process.Id)
            Write-Output "server_listener_stopped=$($process.Id)"
        }
    }
}

function Get-StepLogPath {
    param([string]$Label)
    $safeLabel = ($Label.ToLowerInvariant() -replace '[^a-z0-9]+', '_').Trim('_')
    return Join-Path $LogDir "$Timestamp`_$safeLabel.log"
}

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Label,
        [Parameter(Mandatory = $true)]
        [scriptblock]$Command
    )
    $script:CurrentStep = $Label
    $logPath = Get-StepLogPath -Label $Label
    Write-Output ""
    Write-Output "step_start=$Label log=$logPath"
    try {
        & $Command *>&1 | Tee-Object -FilePath $logPath
    }
    catch {
        Write-Output "step_failed=$Label log=$logPath"
        throw
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Output "step_failed=$Label exit=$LASTEXITCODE log=$logPath"
        throw "$Label failed with exit=$LASTEXITCODE"
    }
    Write-Output "step_ok=$Label log=$logPath"
}

function Invoke-ExternalStep {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Label,
        [Parameter(Mandatory = $true)]
        [string]$FilePath,
        [Parameter(Mandatory = $true)]
        [string[]]$ArgumentList,
        [int]$TimeoutSec = 180
    )
    $script:CurrentStep = $Label
    $logPath = Get-StepLogPath -Label $Label
    $errPath = "$logPath.err"
    Write-Output ""
    Write-Output "step_start=$Label timeout_sec=$TimeoutSec log=$logPath"
    $quotedArgs = @(
        foreach ($arg in $ArgumentList) {
            if ($arg -match '\s') {
                '"' + ($arg -replace '"', '\"') + '"'
            }
            else {
                $arg
            }
        }
    )
    $process = Start-Process `
        -FilePath $FilePath `
        -ArgumentList $quotedArgs `
        -WorkingDirectory $RepoRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $logPath `
        -RedirectStandardError $errPath `
        -PassThru
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while (-not $process.HasExited -and (Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 500
        $process.Refresh()
    }
    if (-not $process.HasExited) {
        Stop-ProcessTree -RootProcessId ([int]$process.Id)
        if (Test-Path $errPath) {
            Add-Content -Path $logPath -Value "`n--- stderr ---"
            Get-Content $errPath | Add-Content -Path $logPath
            Remove-Item $errPath -Force -ErrorAction SilentlyContinue
        }
        Write-Output "step_failed=$Label reason=timeout timeout_sec=$TimeoutSec log=$logPath"
        throw "$Label timed out after $TimeoutSec seconds"
    }
    if (Test-Path $errPath) {
        Add-Content -Path $logPath -Value "`n--- stderr ---"
        Get-Content $errPath | Add-Content -Path $logPath
        Remove-Item $errPath -Force -ErrorAction SilentlyContinue
    }
    if ($process.ExitCode -ne 0) {
        Write-Output "step_failed=$Label exit=$($process.ExitCode) log=$logPath"
        throw "$Label failed with exit=$($process.ExitCode)"
    }
    Write-Output "step_ok=$Label log=$logPath"
}

Push-Location $RepoRoot
$ServerProcess = $null
try {
    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
    Write-Output "== S.H.I.O.K. local launch check =="
    Write-Output "repo=$RepoRoot"
    Write-Output "bundle=$DataBundle"
    Write-Output "deploy=false"
    Write-Output "log_dir=$LogDir"
    if ($WaiveOneMapValidation) {
        Write-Output "onemap_validation_waiver=owner_approved"
    }
    $RequestedPort = $Port
    $Port = Find-AvailablePort -StartPort $Port
    if ($Port -ne $RequestedPort) {
        Write-Output "port_adjusted=$RequestedPort->$Port"
    }

    if (-not $SkipPythonTests) {
        Invoke-Checked -Label "Python tests" -Command { uv run python run.py test }
    }

    if (-not ($SkipWebTests -and $SkipBuild -and $SkipBrowser)) {
        Invoke-Checked -Label "Web dependencies" -Command {
            .\scripts\ensure-web-deps.ps1
        }
    }

    if (-not $SkipWebTests) {
        Invoke-Checked -Label "Web tests" -Command { npm --prefix web test }
    }

    if (-not $SkipBuild) {
        Invoke-Checked -Label "Fresh-bundle web build" -Command {
            $env:SHIOK_DATA_BUNDLE = $DataBundle
            $env:NEXT_PUBLIC_DATA_BASE = "/data/$DataBundle/"
            try {
                npm --prefix web run build
            }
            finally {
                Remove-Item Env:\SHIOK_DATA_BUNDLE -ErrorAction SilentlyContinue
                Remove-Item Env:\NEXT_PUBLIC_DATA_BASE -ErrorAction SilentlyContinue
            }
        }
    }

    Invoke-Checked -Label "Pending-bundle readiness" -Command {
        $ReadinessCommandLabel = "uv run python run.py readiness"
        Write-Output "command=$ReadinessCommandLabel"
        $ReadinessArgs = @("run", "python", "run.py", "readiness", "--bundle-dir", "web\public\data\$DataBundle")
        if ($WaiveOneMapValidation) {
            $ReadinessArgs += "--waive-onemap-validation"
            if ($OwnerApprovalNote) {
                $ReadinessArgs += "--owner-approval-note"
                $ReadinessArgs += $OwnerApprovalNote
            }
        }
        $ReadinessOutput = uv @ReadinessArgs
        $ReadinessExit = $LASTEXITCODE
        $ReadinessOutput | Set-Content -Path "qa\readiness_launch_check_$Timestamp.json" -Encoding utf8
        $ReadinessOutput
        if ($ReadinessExit -ne 0) {
            throw "readiness command failed with exit=$ReadinessExit"
        }
        $ReadinessJson = $ReadinessOutput | ConvertFrom-Json
        if ($ReadinessJson.release_gate_status -eq "blocked") {
            Write-Output "release_gate_status=blocked"
            Write-Output "release_gate_passed=$($ReadinessJson.release_gate_passed)"
            throw "release gate blocked"
        }
    }

    if (-not $SkipBrowser) {
        Write-Output ""
        Write-Output "step_start=Start local production server"
        $ServerStartCutoff = (Get-Date).AddSeconds(-2)
        $ServerProcess = Start-Process -FilePath npm.cmd -ArgumentList @("--prefix", "web", "run", "start", "--", "-p", "$Port") -WindowStyle Hidden -PassThru
        Write-Output "server_pid=$($ServerProcess.Id)"
        $Deadline = (Get-Date).AddSeconds(45)
        do {
            try {
                $Response = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$Port/" -TimeoutSec 2
                if ($Response.StatusCode -eq 200) {
                    Write-Output "server=ready"
                    break
                }
            }
            catch {
                Start-Sleep -Milliseconds 500
            }
        } while ((Get-Date) -lt $Deadline)
        if ((Get-Date) -ge $Deadline) {
            Write-Output "step_failed=Start local production server"
            throw "Local production server did not become ready on port $Port"
        }
        Write-Output "step_ok=Start local production server"

        Invoke-ExternalStep -Label "Scored browser smoke" -FilePath "npm.cmd" -TimeoutSec 180 -ArgumentList @("--prefix", "web", "run", "qa:browser", "--", "--url", "http://127.0.0.1:$Port/", "--postals", "560231,560234,570234", "--out", "..\qa\browser_smoke_launch_multi_$Timestamp.json")
        Invoke-ExternalStep -Label "Mayflower MRT-only browser smoke" -FilePath "npm.cmd" -TimeoutSec 180 -ArgumentList @("--prefix", "web", "run", "qa:browser", "--", "--url", "http://127.0.0.1:$Port/", "--postal", "560231", "--transit-mode", "mrt_lrt", "--must-include", "Mayflower MRT Station", "--out", "..\qa\browser_smoke_mayflower_mrt_560231_$Timestamp.json", "--debug-port", "$($Port + 99)")
        Invoke-ExternalStep -Label "Route compare browser smoke" -FilePath "npm.cmd" -TimeoutSec 180 -ArgumentList @("--prefix", "web", "run", "qa:browser", "--", "--url", "http://127.0.0.1:$Port/", "--postal", "560109", "--route-mode", "both", "--must-include", "shortest segments", "--out", "..\qa\browser_smoke_route_compare_560109_$Timestamp.json", "--debug-port", "$($Port + 98)")
        Invoke-ExternalStep -Label "No-transit browser smoke" -FilePath "npm.cmd" -TimeoutSec 180 -ArgumentList @("--prefix", "web", "run", "qa:browser", "--", "--url", "http://127.0.0.1:$Port/", "--postal", "567754", "--expected-state", "no_transit", "--out", "..\qa\browser_smoke_no_transit_567754_$Timestamp.json", "--debug-port", "$($Port + 100)")
        Invoke-ExternalStep -Label "Not-yet-scored browser smoke" -FilePath "npm.cmd" -TimeoutSec 180 -ArgumentList @("--prefix", "web", "run", "qa:browser", "--", "--url", "http://127.0.0.1:$Port/", "--postal", "000104", "--expected-state", "not_yet_scored", "--out", "..\qa\browser_smoke_not_yet_scored_000104_$Timestamp.json", "--debug-port", "$($Port + 101)")
    }

    Invoke-Checked -Label "Release plan only" -Command {
        .\scripts\release-data-bundle.bat -DataBundle $DataBundle
    }

    Write-Output ""
    Write-Output "launch_check=ok"
    Write-Output "next_production_command=.\scripts\release-data-bundle.bat -DataBundle $DataBundle -ConfirmProduction"
}
catch {
    Write-Output ""
    Write-Output "launch_check=failed"
    if ($CurrentStep) {
        Write-Output "failing_step=$CurrentStep"
    }
    Write-Output "error=$($_.Exception.Message)"
    throw
}
finally {
    if ($ServerProcess) {
        Stop-ProcessTree -RootProcessId $ServerProcess.Id
        if ($ServerStartCutoff) {
            Stop-NewListenerOnPort -ListenerPort $Port -StartedAfter $ServerStartCutoff
        }
        Write-Output "server=stopped"
    }
    if (Test-PortInUse -CandidatePort $Port) {
        Stop-NewListenerOnPort -ListenerPort $Port -StartedAfter ([datetime]::MinValue)
    }
    if (Test-PortInUse -CandidatePort $Port) {
        Write-Output "port_listener_remaining=$Port"
    }
    Pop-Location
}
