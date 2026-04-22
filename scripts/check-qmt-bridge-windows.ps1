param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [int]$Port = 18888,
    [string]$TaskName = "QMT Bridge Server",
    [int]$TimeoutSeconds = 5,
    [switch]$SkipApiHealth
)

$ErrorActionPreference = "SilentlyContinue"
$overall = 0

function Set-Result {
    param([int]$Code)
    if ($Code -gt $script:overall) {
        $script:overall = $Code
    }
}

function Write-Status {
    param(
        [string]$Label,
        [string]$Message
    )
    Write-Output ("{0,-5} {1}" -f $Label, $Message)
}

function Get-ProcessCommandLine {
    param([int]$ProcessId)
    $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction SilentlyContinue
    if ($null -eq $processInfo) {
        return ""
    }
    return [string]$processInfo.CommandLine
}

Write-Output "Windows QMT Bridge check"
Write-Output ("project_root: {0}" -f $ProjectRoot)
Write-Output ("port: {0}" -f $Port)

Write-Output ""
Write-Output "[scheduled task]"
$taskOk = $false
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
$info = Get-ScheduledTaskInfo -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -eq $task) {
    Write-Output ("task: {0} missing" -f $TaskName)
    Write-Status "WARN" "scheduled task is not installed"
    Set-Result 1
} else {
    Write-Output ("task: {0} state={1}" -f $TaskName, $task.State)
    if ($null -ne $info) {
        Write-Output ("task_last_run: {0}" -f $info.LastRunTime)
        Write-Output ("task_last_result: {0}" -f $info.LastTaskResult)
        $taskOk = ($info.LastTaskResult -eq 0)
    }
    $action = $task.Actions | Select-Object -First 1
    if ($null -ne $action) {
        Write-Output ("task_action: {0} {1}" -f $action.Execute, $action.Arguments)
        Write-Output ("task_workdir: {0}" -f $action.WorkingDirectory)
    }
    if ($taskOk) {
        Write-Status "PASS" "scheduled task metadata is healthy"
    } else {
        Write-Status "WARN" "scheduled task exists, but last result is not 0"
        Set-Result 1
    }
}

Write-Output ""
Write-Output "[listener]"
$serverFound = $false
$listeners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($null -eq $listeners) {
    Write-Output ("listener: none on port {0}" -f $Port)
} else {
    foreach ($conn in $listeners) {
        $proc = Get-Process -Id $conn.OwningProcess -ErrorAction SilentlyContinue
        $name = "unknown"
        $path = ""
        if ($null -ne $proc) {
            $name = $proc.ProcessName
            $path = $proc.Path
        }
        if ($name -eq "wslrelay") {
            Write-Output ("listener: {0}:{1} pid={2} process=wslrelay role=wsl-relay" -f $conn.LocalAddress, $conn.LocalPort, $conn.OwningProcess)
            continue
        }

        $commandLine = Get-ProcessCommandLine -ProcessId $conn.OwningProcess
        $isQmt = ($commandLine -like "*qmt-server.exe*")
        if ($isQmt) {
            $serverFound = $true
        }
        Write-Output ("listener: {0}:{1} pid={2} process={3} qmt_server={4}" -f $conn.LocalAddress, $conn.LocalPort, $conn.OwningProcess, $name, $isQmt)
        if ($path) {
            Write-Output ("process_path: {0}" -f $path)
        }
    }
}

if ($serverFound) {
    Write-Status "PASS" "Windows qmt-server listener is present"
} else {
    Write-Status "FAIL" "Windows qmt-server listener was not found"
    Set-Result 2
}

if (-not $SkipApiHealth) {
    Write-Output ""
    Write-Output "[api health]"
    $url = "http://127.0.0.1:$Port/api/meta/health"
    try {
        $response = Invoke-RestMethod -Uri $url -TimeoutSec $TimeoutSeconds
        Write-Output ("url: {0}" -f $url)
        Write-Output ("response_status: {0}" -f $response.status)
        if ($response.status -eq "ok") {
            Write-Status "PASS" "Windows localhost API health is ok"
        } else {
            Write-Status "FAIL" "Windows localhost API health returned an unexpected status"
            Set-Result 2
        }
    } catch {
        Write-Output ("url: {0}" -f $url)
        Write-Output ("error: {0}" -f $_.Exception.Message)
        Write-Status "FAIL" "Windows localhost API health check failed"
        Set-Result 2
    }
}

Write-Output ""
Write-Output "[summary]"
switch ($overall) {
    0 { Write-Status "PASS" "Windows qmt-server environment is healthy" }
    1 { Write-Status "WARN" "Windows qmt-server is usable, but cleanup is recommended" }
    default { Write-Status "FAIL" "Windows qmt-server environment is not healthy" }
}

exit $overall
