$ErrorActionPreference = "Stop"

# ======================================
# DSTS LANE D STARTUP SCRIPT
# ======================================

# DSTS project location
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

# Python virtual environment is inside the project folder
$Python = Join-Path $ProjectRoot "venv\Scripts\python.exe"

# Docker CLI
$DockerPath = "C:\Program Files\Docker\Docker\resources\bin"

Write-Host ""
Write-Host "======================================" -ForegroundColor Cyan
Write-Host "        DSTS LANE D STARTUP" -ForegroundColor Cyan
Write-Host "======================================" -ForegroundColor Cyan
Write-Host ""

# ======================================
# 1. CHECK DOCKER
# ======================================

Write-Host "[1] Checking Docker..." -ForegroundColor Yellow

if (Test-Path "$DockerPath\docker.exe") {
    $env:Path += ";$DockerPath"
}

try {
    docker --version
}
catch {
    Write-Host "Docker is not available." -ForegroundColor Red
    Write-Host "Please start Docker Desktop and run this script again." -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

# ======================================
# 2. START GRAFANA + PROMETHEUS
# ======================================

Write-Host ""
Write-Host "[2] Starting Grafana + Prometheus..." -ForegroundColor Yellow

$MonitoringPath = Join-Path $ProjectRoot "monitoring"

if (-not (Test-Path $MonitoringPath)) {
    Write-Host "Monitoring folder not found:" -ForegroundColor Red
    Write-Host $MonitoringPath -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

Push-Location $MonitoringPath

docker compose up -d

Pop-Location

Write-Host "Monitoring services are running." -ForegroundColor Green

# ======================================
# 3. CHECK PYTHON ENVIRONMENT
# ======================================

Write-Host ""
Write-Host "[3] Checking Python environment..." -ForegroundColor Yellow

if (-not (Test-Path $Python)) {
    Write-Host "Python virtual environment not found." -ForegroundColor Red
    Write-Host "Expected location:" -ForegroundColor Red
    Write-Host $Python -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

& $Python --version

# ======================================
# 4. START FASTAPI
# ======================================

Write-Host ""
Write-Host "[4] Starting DSTS server..." -ForegroundColor Yellow
Write-Host ""

Write-Host "Dashboard : http://localhost:8000" -ForegroundColor Green
Write-Host "Grafana   : http://localhost:3000" -ForegroundColor Green
Write-Host "Prometheus: http://localhost:9090" -ForegroundColor Green

Write-Host ""
Write-Host "Opening DSTS dashboard..." -ForegroundColor Cyan

Start-Process "http://localhost:8000"

Write-Host ""
Write-Host "======================================" -ForegroundColor Green
Write-Host "        DSTS IS RUNNING" -ForegroundColor Green
Write-Host "======================================" -ForegroundColor Green
Write-Host ""
Write-Host "Dashboard : http://localhost:8000"
Write-Host "Grafana   : http://localhost:3000"
Write-Host "Prometheus: http://localhost:9090"
Write-Host ""
Write-Host "Keep this window open while using DSTS." -ForegroundColor Yellow
Write-Host "Press CTRL+C to stop the FastAPI server."
Write-Host ""

# Start FastAPI using the project's virtual environment
& $Python -m uvicorn dsts.api.server:app --host 127.0.0.1 --port 8000