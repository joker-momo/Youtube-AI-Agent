# Root Runner Wrapper for Windows
# Delegates to installers/run_windows.ps1

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir

$runnerPath = Join-Path $scriptDir "installers\run_windows.ps1"
if (Test-Path $runnerPath) {
    & powershell -ExecutionPolicy Bypass -File $runnerPath
} else {
    Write-Host "❌ Error: installers\run_windows.ps1 not found." -ForegroundColor Red
    exit 1
}
