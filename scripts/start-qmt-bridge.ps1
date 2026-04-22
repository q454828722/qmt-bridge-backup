param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [int]$Port = 18888,
    [int]$StartupTimeoutSeconds = 25,
    [int]$MaxLogBytes = 5242880
)

$ErrorActionPreference = "Stop"

Set-Location $ProjectRoot

$LogsDir = Join-Path $ProjectRoot "logs"
$PidFile = Join-Path $ProjectRoot "qmt-bridge.pid"
$ServerExe = Join-Path $ProjectRoot ".venv\Scripts\qmt-server.exe"
$StdoutLog = Join-Path $LogsDir "windows-startup-server.out.log"
$StderrLog = Join-Path $LogsDir "windows-startup-server.err.log"

if (-not (Test-Path $LogsDir)) {
    New-Item -ItemType Directory -Path $LogsDir | Out-Null
}

function Test-ProcessAlive {
    param([int]$ProcessId)
    return [bool](Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)
}

function Get-ProcessCommandLine {
    param([int]$ProcessId)
    $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction SilentlyContinue
    if ($null -eq $processInfo) {
        return ""
    }
    return [string]$processInfo.CommandLine
}

function Test-QmtServerProcess {
    param([int]$ProcessId)
    if (-not (Test-ProcessAlive -ProcessId $ProcessId)) {
        return $false
    }
    $commandLine = Get-ProcessCommandLine -ProcessId $ProcessId
    return $commandLine -like "*qmt-server.exe*"
}

function Get-QmtServerListenerPid {
    param([int]$ListenPort)
    $connections = Get-NetTCPConnection -LocalPort $ListenPort -State Listen -ErrorAction SilentlyContinue
    foreach ($conn in $connections) {
        $process = Get-Process -Id $conn.OwningProcess -ErrorAction SilentlyContinue
        if ($null -eq $process -or $process.ProcessName -eq "wslrelay") {
            continue
        }
        if (Test-QmtServerProcess -ProcessId $conn.OwningProcess) {
            return [int]$conn.OwningProcess
        }
    }
    return $null
}

function Rotate-Log {
    param([string]$Path)
    if (-not (Test-Path $Path)) {
        return
    }
    $item = Get-Item $Path -ErrorAction SilentlyContinue
    if ($null -eq $item -or $item.Length -lt $MaxLogBytes) {
        return
    }
    $archivePath = "$Path.1"
    Remove-Item $archivePath -Force -ErrorAction SilentlyContinue
    Move-Item $Path $archivePath -Force
}

function Wait-QmtServerListener {
    param(
        [int]$ExpectedProcessId,
        [int]$ListenPort,
        [int]$TimeoutSeconds
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 500
        if (-not (Test-QmtServerProcess -ProcessId $ExpectedProcessId)) {
            throw "qmt-server exited during startup. Check $StderrLog"
        }
        $listenerPid = Get-QmtServerListenerPid -ListenPort $ListenPort
        if ($listenerPid) {
            $listenerPid | Set-Content -Encoding ascii -NoNewline $PidFile
            return $listenerPid
        }
    }
    return $null
}

if (-not (Test-Path $ServerExe)) {
    throw "qmt-server.exe not found: $ServerExe"
}

$listenerPid = Get-QmtServerListenerPid -ListenPort $Port
if ($listenerPid) {
    $listenerPid | Set-Content -Encoding ascii -NoNewline $PidFile
    Write-Output "QMT Bridge already listening on port $Port (PID $listenerPid)."
    exit 0
}

if (Test-Path $PidFile) {
    $oldPidText = (Get-Content $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    $oldPid = 0
    if ([int]::TryParse($oldPidText, [ref]$oldPid) -and (Test-QmtServerProcess -ProcessId $oldPid)) {
        Write-Output "QMT Bridge process already running (PID $oldPid), waiting for port $Port."
        $listenerPid = Wait-QmtServerListener -ExpectedProcessId $oldPid -ListenPort $Port -TimeoutSeconds $StartupTimeoutSeconds
        if ($listenerPid) {
            Write-Output "QMT Bridge started listening on port $Port (PID $listenerPid)."
            exit 0
        }
        throw "qmt-server process $oldPid is running but port $Port is not listening within $StartupTimeoutSeconds seconds."
    }
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
}

Rotate-Log -Path $StdoutLog
Rotate-Log -Path $StderrLog

$process = Start-Process `
    -FilePath $ServerExe `
    -WorkingDirectory $ProjectRoot `
    -RedirectStandardOutput $StdoutLog `
    -RedirectStandardError $StderrLog `
    -WindowStyle Hidden `
    -PassThru

$process.Id | Set-Content -Encoding ascii -NoNewline $PidFile

$listenerPid = Wait-QmtServerListener -ExpectedProcessId $process.Id -ListenPort $Port -TimeoutSeconds $StartupTimeoutSeconds
if ($listenerPid) {
    Write-Output "QMT Bridge started on port $Port (PID $listenerPid)."
    exit 0
}

throw "qmt-server did not start listening on port $Port within $StartupTimeoutSeconds seconds. Check $StderrLog"
