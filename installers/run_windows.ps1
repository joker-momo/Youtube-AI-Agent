# Runner script for YouTube AI Agent on Windows
# Starts Docker if needed, verifies environment, and launches all containers.

$ErrorActionPreference = "Stop"

# Helper constants for colored outputs
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
Write-Host "          YouTube AI Agent Launcher                  " -ForegroundColor Blue -FontWeight Bold
Write-Host "=====================================================" -ForegroundColor Blue -FontWeight Bold
Write-Host ""

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoDir = Split-Path -Parent $scriptDir
Set-Location $repoDir

# 1. Check Docker Daemon
Write-Host "Checking Docker status..." -ForegroundColor Cyan
& docker info >$null 2>&1
if ($LASTEXITCODE -ne 0) {
    Show-Warning "Docker is not running. Launching Docker Desktop on Windows..."
    $dockerPath = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
    if (Test-Path $dockerPath) {
        Start-Process $dockerPath
    } else {
        Start-Process "Docker Desktop" -ErrorAction SilentlyContinue
    }
    
    Write-Host "Waiting for Docker to start..." -ForegroundColor Blue
    $count = 0
    $ready = $false
    while (-not $ready -and $count -lt 60) {
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
        Show-Error "Docker took too long to start. Please start Docker Desktop manually and run this script again."
        exit 1
    }
}
Show-Success "Docker is active and running!`n"

# 2. Check for .env file
if (-not (Test-Path ".env")) {
    Show-Warning ".env file not found. Copying from .env.example..."
    Copy-Item .env.example .env
}

# 3. Start Containers with optional GPU configurations
$composeArgs = @("-f", "docker-compose.yml")

Write-Host "Checking for GPU hardware..." -ForegroundColor Cyan
$nvidiaExists = Get-Command nvidia-smi -ErrorAction SilentlyContinue
if ($nvidiaExists) {
    Show-Success "Detected NVIDIA GPU! Configuring Docker for GPU acceleration..."
    $composeArgs += @("-f", "docker-compose.nvidia.yml")
} else {
    Write-Host "ℹ️ No NVIDIA GPU detected. Running in standard CPU mode." -ForegroundColor Gray
}

Write-Host "Starting YouTube AI Agent services..." -ForegroundColor Cyan
& docker compose $composeArgs up -d app worker browser-worker browser-runtime

# 4. Check Health
Write-Host ""
Show-Success "Services started successfully!"
Write-Host "=====================================================" -ForegroundColor Cyan -FontWeight Bold
Write-Host "  - Dashboard URL:      http://localhost:8000" -ForegroundColor Green -FontWeight Bold
Write-Host "  - VNC Browser URL:    http://localhost:7900 (Manual ChatGPT/Claude Logins)" -ForegroundColor Green -FontWeight Bold
Write-Host "=====================================================" -ForegroundColor Cyan -FontWeight Bold
Write-Host ""
Write-Host "To view realtime logs, run:"
Write-Host "  docker compose logs -f" -ForegroundColor Yellow
Write-Host ""
Write-Host "To stop all services, run:"
Write-Host "  docker compose down" -ForegroundColor Yellow
Write-Host "=====================================================" -ForegroundColor Cyan -FontWeight Bold
