#!/usr/bin/env bash

set -euo pipefail

# ================================================================
# DSTS Lane D — One Command Demo Launcher
# ================================================================

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

cd "$ROOT_DIR"


# ------------------------------------------------
# Configuration
# ------------------------------------------------

API_HOST="127.0.0.1"
API_PORT="8000"

PROMETHEUS_PORT="9090"
GRAFANA_PORT="3000"


# ------------------------------------------------
# Colors
# ------------------------------------------------

GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'


# ------------------------------------------------
# Helper functions
# ------------------------------------------------

info() {
    echo -e "${CYAN}[DSTS]${NC} $1"
}


success() {
    echo -e "${GREEN}[ OK ]${NC} $1"
}


warning() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}


error() {
    echo -e "${RED}[ERR ]${NC} $1"
}


# ------------------------------------------------
# Cleanup
# ------------------------------------------------

cleanup() {

    echo
    info "Stopping DSTS services..."

    if [[ -n "${UVICORN_PID:-}" ]]; then

        kill "$UVICORN_PID" 2>/dev/null || true

    fi

    echo

    # Do not destroy containers.
    # Just stop the monitoring stack.
    if command -v docker >/dev/null 2>&1; then

        docker compose \
            -f "$ROOT_DIR/docker-compose.yml" \
            stop prometheus grafana \
            >/dev/null 2>&1 || true

    fi

    success "DSTS demo stopped."

}


trap cleanup EXIT INT TERM


# ------------------------------------------------
# Banner
# ------------------------------------------------

clear 2>/dev/null || true

echo
echo "============================================================"
echo "        DSTS — DISTRIBUTED TRACKING & SECURITY"
echo "                    LANE D DEMO"
echo "============================================================"
echo


# ------------------------------------------------
# Check Python
# ------------------------------------------------

if ! command -v python >/dev/null 2>&1; then

    error "Python was not found."

    exit 1

fi


success "Python detected."


# ------------------------------------------------
# Check Docker
# ------------------------------------------------

if ! command -v docker >/dev/null 2>&1; then

    error "Docker was not found."

    exit 1

fi


if ! docker info >/dev/null 2>&1; then

    error "Docker is not running."

    echo "Please start Docker and run this script again."

    exit 1

fi


success "Docker is running."


# ================================================================
# 1. START PROMETHEUS + GRAFANA
# ================================================================

echo
info "Starting Prometheus + Grafana..."

docker compose \
    -f "$ROOT_DIR/docker-compose.yml" \
    up -d prometheus grafana


success "Prometheus started on http://localhost:${PROMETHEUS_PORT}"

success "Grafana started on http://localhost:${GRAFANA_PORT}"


# ================================================================
# 2. START FASTAPI
# ================================================================

echo
info "Starting DSTS FastAPI server..."


python -m uvicorn \
    dsts.api.server:app \
    --host "$API_HOST" \
    --port "$API_PORT" \
    > /tmp/dsts_api.log 2>&1 &


UVICORN_PID=$!


# ------------------------------------------------
# Wait for API
# ------------------------------------------------

info "Waiting for dashboard server..."


API_READY=false


for i in {1..30}; do

    if curl -fsS \
        "http://${API_HOST}:${API_PORT}/api/health" \
        >/dev/null 2>&1; then

        API_READY=true

        break

    fi


    # Check whether uvicorn crashed.
    if ! kill -0 "$UVICORN_PID" 2>/dev/null; then

        error "FastAPI server stopped unexpectedly."

        echo
        cat /tmp/dsts_api.log

        exit 1

    fi


    sleep 1

done


if [[ "$API_READY" != true ]]; then

    error "FastAPI did not become ready."

    echo
    cat /tmp/dsts_api.log

    exit 1

fi


success "FastAPI ready."


# ================================================================
# 3. OPEN DASHBOARD
# ================================================================

DASHBOARD_URL="http://localhost:${API_PORT}/"


echo
success "DSTS dashboard is ready."
echo
echo "------------------------------------------------------------"
echo " Dashboard : ${DASHBOARD_URL}"
echo " Grafana   : http://localhost:${GRAFANA_PORT}"
echo " Prometheus: http://localhost:${PROMETHEUS_PORT}"
echo " Metrics   : http://localhost:${API_PORT}/metrics"
echo " API Docs  : http://localhost:${API_PORT}/docs"
echo "------------------------------------------------------------"
echo


open_browser() {

    local url="$1"


    # Linux / WSL
    if command -v xdg-open >/dev/null 2>&1; then

        xdg-open "$url" >/dev/null 2>&1 &

        return 0

    fi


    # macOS
    if command -v open >/dev/null 2>&1; then

        open "$url" >/dev/null 2>&1 &

        return 0

    fi


    # Windows Git Bash
    if command -v cmd.exe >/dev/null 2>&1; then

        cmd.exe /c start "" "$url" \
            >/dev/null 2>&1 &

        return 0

    fi


    # Windows WSL
    if command -v wslview >/dev/null 2>&1; then

        wslview "$url" >/dev/null 2>&1 &

        return 0

    fi


    return 1
}


if open_browser "$DASHBOARD_URL"; then

    success "Opening DSTS dashboard in your browser."

else

    warning "Could not automatically open a browser."

    echo "Open this manually:"
    echo "  ${DASHBOARD_URL}"

fi


# ================================================================
# 4. RUN SCRIPTED B1 → B5 WALK
# ================================================================

if [[ "${1:-}" != "--no-demo" ]]; then

    echo
    info "Running scripted B1 → B5 demonstration..."
    echo


    if python -m scripts.terminal_walk; then

        success "B1 → B5 demonstration completed."

    else

        warning "B1 → B5 terminal demonstration returned an error."

    fi

fi


# ================================================================
# 5. KEEP SERVER ALIVE
# ================================================================

echo
echo "============================================================"
echo " DSTS IS RUNNING"
echo "============================================================"
echo
echo " Dashboard : ${DASHBOARD_URL}"
echo " Grafana   : http://localhost:${GRAFANA_PORT}"
echo " Prometheus: http://localhost:${PROMETHEUS_PORT}"
echo
echo " Press Ctrl+C to stop the entire DSTS demo."
echo


wait "$UVICORN_PID"