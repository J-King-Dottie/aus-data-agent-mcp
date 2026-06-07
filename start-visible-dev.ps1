Param(
    [string]$WslDistro = "Ubuntu",
    [string]$WslRepo = "/home/projects/abs-mcp",
    [int]$FrontendPort = 3000,
    [int]$BackendPort = 5000,
    [switch]$SkipInstall
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Convert-ToSingleQuotedBash {
    param([string]$Value)
    return "'" + $Value.Replace("'", "'\''") + "'"
}

function Convert-ToPowerShellSingleQuoted {
    param([string]$Value)
    return "'" + $Value.Replace("'", "''") + "'"
}

function New-VisibleDevTerminal {
    param(
        [string]$Title,
        [string]$BashCommand
    )

    $quotedBash = Convert-ToPowerShellSingleQuoted -Value $BashCommand
    $quotedDistro = Convert-ToPowerShellSingleQuoted -Value $WslDistro
    $windowCommand = @"
`$Host.UI.RawUI.WindowTitle = '$Title'
Write-Host '$Title'
wsl.exe -d $quotedDistro -- bash -lc $quotedBash
Write-Host ''
Write-Host '$Title exited. Press Enter to close this window.'
Read-Host
"@
    $encodedCommand = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($windowCommand))

    Start-Process `
        -FilePath "powershell.exe" `
        -ArgumentList @("-NoExit", "-ExecutionPolicy", "Bypass", "-EncodedCommand", $encodedCommand) `
        -WorkingDirectory $env:SystemRoot
}

function Clear-WslPort {
    param([int]$Port)

    $killCommand = "fuser -k $Port/tcp >/dev/null 2>&1 || true"
    wsl.exe -d $WslDistro -- bash -lc $killCommand | Out-Null
}

$repo = Convert-ToSingleQuotedBash -Value $WslRepo
$skipInstallValue = if ($SkipInstall) { "1" } else { "0" }

$frontendCommand = @"
set -euo pipefail
cd $repo
SKIP_INSTALL=$skipInstallValue FRONTEND_PORT=$FrontendPort ./scripts/dev-frontend-wsl.sh
"@

$backendCommand = @"
set -euo pipefail
cd $repo
SKIP_INSTALL=$skipInstallValue BACKEND_PORT=$BackendPort ./scripts/dev-backend-wsl.sh
"@

Clear-WslPort -Port $FrontendPort
Clear-WslPort -Port $BackendPort

New-VisibleDevTerminal -Title "AusData frontend dev" -BashCommand $frontendCommand
Start-Sleep -Milliseconds 700
New-VisibleDevTerminal -Title "AusData backend dev" -BashCommand $backendCommand

Write-Host "Opened visible dev terminals:"
Write-Host "  Frontend: http://127.0.0.1:$FrontendPort"
Write-Host "  Backend:  http://127.0.0.1:$BackendPort"
