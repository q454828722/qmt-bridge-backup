param(
    [string]$OldRoot = "D:\qmt-bridge"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $OldRoot)) {
    Write-Output "NOT_FOUND:$OldRoot"
    exit 0
}

try {
    Remove-Item -LiteralPath $OldRoot -Recurse -Force
    Write-Output "REMOVED:$OldRoot"
    exit 0
} catch {
    Write-Output "REMOVE_FAILED:$OldRoot"
}

$pythonExe = Join-Path $OldRoot ".venv\Scripts\python.exe"
if (Test-Path -LiteralPath $pythonExe) {
    try {
        [System.IO.File]::Delete($pythonExe)
        Write-Output "REMOVED_FILE:$pythonExe"
    } catch {
        Write-Output "DELETE_FAILED:$pythonExe"
    }
}

try {
    Remove-Item -LiteralPath $OldRoot -Recurse -Force
    Write-Output "REMOVED:$OldRoot"
    exit 0
} catch {
    Write-Output "FINAL_FAILED:$OldRoot"
}

Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;

public static class MoveFileExHelper {
    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    public static extern bool MoveFileEx(string existingFileName, string newFileName, int flags);
}
"@

$delayUntilReboot = 0x4
$paths = @(
    (Join-Path $OldRoot ".venv\Scripts\python.exe")
    (Join-Path $OldRoot ".venv\Scripts")
    (Join-Path $OldRoot ".venv")
    $OldRoot
)

$allQueued = $true
foreach ($path in $paths) {
    if (-not (Test-Path -LiteralPath $path)) {
        continue
    }
    $queued = [MoveFileExHelper]::MoveFileEx($path, $null, $delayUntilReboot)
    if ($queued) {
        Write-Output "PENDING_DELETE_ON_REBOOT:$path"
    } else {
        Write-Output "PENDING_DELETE_FAILED:$path"
        $allQueued = $false
    }
}

if ($allQueued) {
    exit 0
}

exit 1
