# Root Installer Wrapper for Windows
# Delegates to installers/install_windows.ps1

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir

$installerPath = Join-Path $scriptDir "installers\install_windows.ps1"
if (Test-Path $installerPath) {
    & powershell -ExecutionPolicy Bypass -File $installerPath
} else {
    Write-Host "❌ Error: installers\install_windows.ps1 not found." -ForegroundColor Red
    exit 1
}
