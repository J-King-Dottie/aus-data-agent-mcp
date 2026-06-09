Param(
    [string]$WslDistro = "Ubuntu",
    [string]$WslRepo = "/home/projects/abs-mcp",
    [int]$FrontendPort = 3000,
    [int]$BackendPort = 5000,
    [switch]$Install
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Stop-StaleProjectDevProcesses {
    $currentPid = $PID
    $patterns = @(
        "AusData",
        "abs-mcp",
        "dev-frontend-wsl.sh",
        "dev-backend-wsl.sh",
        "start-visible-dev.ps1",
        "fuser -k $FrontendPort",
        "fuser -k $BackendPort"
    )

    $processes = Get-CimInstance Win32_Process | Where-Object {
        if ($_.ProcessId -eq $currentPid) { return $false }
        if ($_.Name -notin @("powershell.exe", "cmd.exe", "wsl.exe")) { return $false }
        $commandLine = $_.CommandLine
        if (-not $commandLine) { return $false }

        foreach ($pattern in $patterns) {
            if ($commandLine -like "*$pattern*") { return $true }
        }

        return $false
    }

    foreach ($process in $processes) {
        try {
            Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
        }
        catch {
        }
    }
}

function Test-WslResponsive {
    param([int]$TimeoutMilliseconds = 30000)

    $stdoutPath = Join-Path $env:TEMP "abs-mcp-wsl-test-$PID.out"
    $stderrPath = Join-Path $env:TEMP "abs-mcp-wsl-test-$PID.err"

    $process = Start-Process `
        -FilePath "wsl.exe" `
        -ArgumentList @("-d", $WslDistro, "--", "bash", "-lc", "echo wsl-ok") `
        -NoNewWindow `
        -PassThru `
        -RedirectStandardOutput $stdoutPath `
        -RedirectStandardError $stderrPath

    if (-not $process.WaitForExit($TimeoutMilliseconds)) {
        try {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        }
        catch {
        }
        return $false
    }

    return $process.ExitCode -eq 0
}

Stop-StaleProjectDevProcesses
Start-Sleep -Seconds 1

if (-not (Test-WslResponsive)) {
    Get-Process -Name wsl -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 5

    if (-not (Test-WslResponsive -TimeoutMilliseconds 45000)) {
        throw "WSL did not respond after clearing stale dev processes."
    }
}

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$launcher = Join-Path $scriptRoot "start-visible-dev.ps1"
$arguments = @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", $launcher,
    "-WslDistro", $WslDistro,
    "-WslRepo", $WslRepo,
    "-FrontendPort", $FrontendPort,
    "-BackendPort", $BackendPort
)

if (-not $Install) {
    $arguments += "-SkipInstall"
}

& powershell.exe @arguments
