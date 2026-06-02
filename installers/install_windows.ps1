# Windows Installer Script for YouTube AI Agent
# Assumes a fresh machine, installs Git, Docker Desktop, and boots the project.

$ErrorActionPreference = "Stop"

# Helper constants for colored outputs
function Show-Step ($msg) {
    Write-Host "`n👉 [Step] $msg..." -ForegroundColor Cyan -FontWeight Bold
}

function Show-Success ($msg) {
    Write-Host "✅ $msg" -ForegroundColor Green
}

function Show-Warning ($msg) {
    Write-Host "⚠️  $msg" -ForegroundColor Yellow
}

function Show-Error ($msg) {
    Write-Host "❌ $msg" -ForegroundColor Red
}

Write-Host "=====================================================" -ForegroundColor Blue -FontWeight Bold
Write-Host "      YouTube AI Agent - Windows Setup Wizard        " -ForegroundColor Blue -FontWeight Bold
Write-Host "=====================================================" -ForegroundColor Blue -FontWeight Bold
Write-Host ""

# Check for Administrator privileges
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Show-Warning "This script is not running as Administrator."
    Show-Warning "If package installations fail, please restart PowerShell as Administrator and run again."
    Write-Host ""
}

# 1. Check/Install Git
Show-Step "Checking for Git"
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Show-Warning "Git is not installed. Attempting to install via winget..."
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        winget install --id Git.Git -e --source winget --accept-package-agreements --accept-source-agreements
        Show-Success "Git installed successfully! Please restart the shell if git command is not recognized."
    } else {
        Show-Error "winget is not found. Please install Git manually from https://git-scm.com/download/win and rerun this script."
        exit 1
    }
} else {
    Show-Success "Git is already installed at: $((Get-Command git).Source)"
}

# 2. Check/Install Docker Desktop
Show-Step "Checking for Docker Desktop"
$dockerPath = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
if (-not (Get-Command docker -ErrorAction SilentlyContinue) -and -not (Test-Path $dockerPath)) {
    Show-Warning "Docker Desktop is not installed. Attempting to install via winget..."
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Show-Warning "Installing Docker Desktop (this may take several minutes)..."
        winget install --id Docker.DockerDesktop -e --source winget --accept-package-agreements --accept-source-agreements
        Show-Success "Docker Desktop installed successfully!"
        Show-Warning "Note: Windows may require a system sign-out/restart to apply Docker user groups."
    } else {
        Show-Error "winget is not found. Please install Docker Desktop manually from https://www.docker.com/products/docker-desktop/ and rerun."
        exit 1
    }
} else {
    Show-Success "Docker CLI is already installed/available."
}

# 3. Starting Docker Desktop
Show-Step "Starting Docker Desktop application"
& docker info >$null 2>&1
if ($LASTEXITCODE -ne 0) {
    Show-Warning "Docker daemon is not running. Launching Docker Desktop..."
    if (Test-Path $dockerPath) {
        Start-Process $dockerPath
    } else {
        Show-Warning "Could not find Docker Desktop executable at typical path. Attempting to start 'Docker Desktop' by name..."
        Start-Process "Docker Desktop" -ErrorAction SilentlyContinue
    }
    
    Write-Host "Waiting for Docker daemon to become ready (this may take up to 2 minutes)..." -ForegroundColor Blue
    Write-Host "Please click 'Yes' on any User Account Control (UAC) prompts if they appear." -ForegroundColor Yellow
    
    $count = 0
    $ready = $false
    while (-not $ready -and $count -lt 120) {
        Write-Host -NoNewline "."
        Start-Sleep -Seconds 3
        $count += 3
        & docker info >$null 2>&1
        if ($LASTEXITCODE -eq 0) {
            $ready = $true
        }
    }
    Write-Host ""
    if (-not $ready) {
        Show-Error "Docker took too long to start. Please open 'Docker Desktop' manually from your Start Menu, then run this installer again."
        exit 1
    }
    Show-Success "Docker daemon is active and running!"
} else {
    Show-Success "Docker daemon is already active!"
}

# 4. Environment configuration (.env)
Show-Step "Configuring environment variables"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoDir = Split-Path -Parent $scriptDir
Set-Location $repoDir

if (-not (Test-Path ".env")) {
    Show-Warning "Bootstrapping .env from .env.example..."
    Copy-Item .env.example .env
    Show-Success ".env file created! Open it later to fill in your API keys."
} else {
    Show-Success ".env file already exists."
}

# 5. Building Multi-Container Stack
Show-Step "Building Docker containers"
Write-Host "Running 'docker compose build'... (this compiles TTS models and prepares the browser runtime)" -ForegroundColor Blue
docker compose build
Show-Success "Docker containers built successfully!"

# 6. Booting the stack
Show-Step "Launching YouTube AI Agent"
docker compose up -d app worker browser-worker browser-runtime
Show-Success "All services started successfully in the background!"

Write-Host "=====================================================" -ForegroundColor Green -FontWeight Bold
Write-Host "      ✨ Setup Completed Successfully! ✨            " -ForegroundColor Green -FontWeight Bold
Write-Host "=====================================================" -ForegroundColor Green -FontWeight Bold
Write-Host ""
Write-Host "⚠️  CRITICAL ACTION REQUIRED ON NEW MACHINE:" -ForegroundColor Yellow -FontWeight Bold
Write-Host "You must configure the .env file in the repository root before running jobs."
Write-Host "Open the .env file and fill in your keys:"
Write-Host "  - TELEGRAM_BOT_TOKEN & TELEGRAM_CHAT_ID (For status/progress alerts)"
Write-Host "  - PEXELS_API_KEY & PIXABAY_API_KEY (For stock video/image downloads)"
Write-Host ""
Write-Host "You can now access the following endpoints in your browser:"
Write-Host "  - Web Dashboard:    http://localhost:8000" -ForegroundColor Cyan
Write-Host "  - Browser Runtime (VNC): http://localhost:7900 (For manual ChatGPT/Gemini logins)" -ForegroundColor Cyan
Write-Host ""
Write-Host "To view running logs, execute:"
Write-Host "  docker compose logs -f" -ForegroundColor Yellow
Write-Host ""
Write-Host "To stop the agent, run:"
Write-Host "  docker compose down" -ForegroundColor Yellow
Write-Host ""
Write-Host "To start it again anytime, use our runner script:"
Write-Host "  powershell -ExecutionPolicy Bypass -File .\run.ps1" -ForegroundColor Yellow
Write-Host "=====================================================" -ForegroundColor Green -FontWeight Bold
