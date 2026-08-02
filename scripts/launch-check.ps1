[CmdletBinding()]
param(
    [string]$DataBundle = "generated_20260801_165500",
    [int]$Port = 3110,
    [switch]$SkipPythonTests,
    [switch]$SkipWebTests,
    [switch]$SkipBuild,
    [switch]$SkipBrowser
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$WebDir = Join-Path $RepoRoot "web"
$BundleDir = Join-Path $WebDir "public\data\$DataBundle"
$Timestamp = Get-Date -Format "yyyyMMdd_HHmmss"

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

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Label,
        [Parameter(Mandatory = $true)]
        [scriptblock]$Command
    )
    Write-Output ""
    Write-Output "== $Label =="
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "$Label failed with exit=$LASTEXITCODE"
    }
}

Push-Location $RepoRoot
$ServerProcess = $null
try {
    Write-Output "== S.H.I.O.K. local launch check =="
    Write-Output "repo=$RepoRoot"
    Write-Output "bundle=$DataBundle"
    Write-Output "deploy=false"
    $RequestedPort = $Port
    $Port = Find-AvailablePort -StartPort $Port
    if ($Port -ne $RequestedPort) {
        Write-Output "port_adjusted=$RequestedPort->$Port"
    }

    if (-not $SkipPythonTests) {
        Invoke-Checked -Label "Python tests" -Command { uv run python run.py test }
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
        $ReadinessOutput = uv run python run.py readiness --bundle-dir "web\public\data\$DataBundle"
        $ReadinessExit = $LASTEXITCODE
        $ReadinessOutput | Set-Content -Path "qa\readiness_launch_check_$Timestamp.json" -Encoding utf8
        $ReadinessOutput
        if ($ReadinessExit -ne 0) {
            exit $ReadinessExit
        }
    }

    if (-not $SkipBrowser) {
        Write-Output ""
        Write-Output "== Start local production server =="
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
            throw "Local production server did not become ready on port $Port"
        }

        Invoke-Checked -Label "Scored browser smoke" -Command {
            npm --prefix web run qa:browser -- --url "http://127.0.0.1:$Port/" --postals 560231,560234,570234 --out "..\qa\browser_smoke_launch_multi_$Timestamp.json"
        }
        Invoke-Checked -Label "Mayflower MRT-only browser smoke" -Command {
            npm --prefix web run qa:browser -- --url "http://127.0.0.1:$Port/" --postal 560231 --transit-mode mrt_lrt --must-include "Mayflower MRT Station" --out "..\qa\browser_smoke_mayflower_mrt_560231_$Timestamp.json" --debug-port ($Port + 99)
        }
        Invoke-Checked -Label "Route compare browser smoke" -Command {
            npm --prefix web run qa:browser -- --url "http://127.0.0.1:$Port/" --postal 560109 --route-mode both --must-include "shortest segments" --out "..\qa\browser_smoke_route_compare_560109_$Timestamp.json" --debug-port ($Port + 98)
        }
        Invoke-Checked -Label "No-transit browser smoke" -Command {
            npm --prefix web run qa:browser -- --url "http://127.0.0.1:$Port/" --postal 567754 --expected-state no_transit --out "..\qa\browser_smoke_no_transit_567754_$Timestamp.json" --debug-port ($Port + 100)
        }
        Invoke-Checked -Label "Not-yet-scored browser smoke" -Command {
            npm --prefix web run qa:browser -- --url "http://127.0.0.1:$Port/" --postal 000104 --expected-state not_yet_scored --out "..\qa\browser_smoke_not_yet_scored_000104_$Timestamp.json" --debug-port ($Port + 101)
        }
    }

    Invoke-Checked -Label "Release plan only" -Command {
        .\scripts\release-data-bundle.bat -DataBundle $DataBundle
    }

    Write-Output ""
    Write-Output "launch_check=ok"
    Write-Output "next_production_command=.\scripts\release-data-bundle.bat -DataBundle $DataBundle -ConfirmProduction"
}
finally {
    if ($ServerProcess) {
        Stop-ProcessTree -RootProcessId $ServerProcess.Id
        if ($ServerStartCutoff) {
            Stop-NewListenerOnPort -ListenerPort $Port -StartedAfter $ServerStartCutoff
        }
        Write-Output "server=stopped"
    }
    Pop-Location
}
