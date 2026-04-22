param(
    [string]$TaskName = "QMT Bridge Server",
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [int]$Port = 18888,
    [int]$StartupDelaySeconds = 30
)

$ErrorActionPreference = "Stop"

$StartScript = Join-Path $ProjectRoot "scripts\start-qmt-bridge.ps1"
if (-not (Test-Path $StartScript)) {
    throw "Start script not found: $StartScript"
}

$PowerShell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$Action = New-ScheduledTaskAction `
    -Execute $PowerShell `
    -Argument "-NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$StartScript`" -ProjectRoot `"$ProjectRoot`" -Port $Port" `
    -WorkingDirectory $ProjectRoot
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
if ($StartupDelaySeconds -gt 0) {
    $Trigger.Delay = "PT${StartupDelaySeconds}S"
}
$Principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 0) `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Principal $Principal `
    -Settings $Settings `
    -Description "Start QMT Bridge from $ProjectRoot when the user logs on." `
    -Force | Out-Null

Write-Output "Installed scheduled task: $TaskName"
Write-Output "Action: $PowerShell -NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$StartScript`" -ProjectRoot `"$ProjectRoot`" -Port $Port"
Write-Output "WorkingDirectory: $ProjectRoot"
Write-Output "StartupDelaySeconds: $StartupDelaySeconds"
