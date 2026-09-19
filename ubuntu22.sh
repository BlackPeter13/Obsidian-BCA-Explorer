#!/usr/bin/env bash
#
# ubuntu22.sh — obsidian-BCA-explorer installer for Ubuntu 22.04.
#
# One script installs Docker Engine + Compose, loads vendored base
# images, configures the project, builds all Docker images, and starts
# the complete stack (NBXplorer + nginx + bca-stats).
#
# Usage:
#   curl -sL <repo>/ubuntu22.sh | bash          # fresh install
#   SKIP_DOCKER=1 bash ubuntu22.sh              # Docker already present
#
set -euo pipefail

# --- Colors & helpers -------------------------------------------------------
R='\033[0;31m'; G='\033[0;32m'; Y='\033[1;33m'; NC='\033[0m'
log()  { printf "${G}[install]${NC} %s\n" "$*"; }
warn() { printf "${Y}[install]${NC} %s\n" "$*"; }
die()  { printf "${R}[install] ERROR:${NC} %s\n" "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="$ROOT/docker-compose.yml"
ENV_FILE="$ROOT/.env"
ENV_EXAMPLE="$ROOT/.env.example"

# --- Phase 1: Docker Engine --------------------------------------------------
ensure_docker() {
    if have docker && docker compose version >/dev/null 2>&1; then
        log "Docker Engine already present ($(docker --version | cut -d' ' -f1)). Bootstrap skipped."
        return 0
    fi
    [ "${SKIP_DOCKER:-0}" = "1" ] && die "Docker not found and SKIP_DOCKER=1."
    [ "$(uname -s)" = "Linux" ] || die "Host is $(uname -s) — this installer requires Ubuntu 22.04 Linux."
    [ "$(uname -m)" = "x86_64" ] || die "Only amd64/x86_64 is supported by vendored images."
    have apt-get || die "No apt-get — cannot install Docker. Install Docker manually and re-run."
    [ "$(id -u)" -eq 0 ] || die "Need root (sudo) to install Docker Engine."

    log "Installing Docker Engine on Ubuntu 22.04..."
    apt-get update -qq
    apt-get install -y -qq ca-certificates curl gnupg

    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg

    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
        https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
        tee /etc/apt/sources.list.d/docker.list > /dev/null

    apt-get update -qq
    apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin

    if have systemctl; then
        systemctl enable --now docker 2>/dev/null || true
    else
        service docker start 2>/dev/null || true
    fi

    have docker && docker compose version >/dev/null 2>&1 \
        || die "Docker install completed but 'docker compose version' still fails."
    log "Docker Engine installed: $(docker --version | cut -d' ' -f1)"
    log "Docker Compose v2: $(docker compose version 2>/dev/null | head -c 40)"
}

# --- Phase 2: Load vendored base images --------------------------------------
load_images() {
    declare -A IMAGES=(
        ["mcr.microsoft.com/dotnet/sdk:8.0-bookworm-slim"]="images/dotnet-sdk-8.0-bookworm-slim.tar"
        ["mcr.microsoft.com/dotnet/aspnet:8.0-bookworm-slim"]="images/dotnet-aspnet-8.0-bookworm-slim.tar"
        ["python:3.11-slim-bookworm"]="images/python-3.11-slim-bookworm.tar"
        ["nginx:alpine"]="images/nginx-alpine.tar"
    )

    log "Loading vendored base images..."
    for ref in "${!IMAGES[@]}"; do
        tar="${IMAGES[$ref]}"
        if docker image inspect "$ref" >/dev/null 2>&1; then
            log "  ✓ already present: $ref"
        else
            [ -f "$ROOT/$tar" ] || die "Missing image $ref and $tar not found — cannot install offline."
            log "  → loading $ref ..."
            docker load -i "$ROOT/$tar" 2>&1 | tail -1
        fi
    done
    log "All base images loaded."
}

# --- Phase 3: Verify vendored files -----------------------------------------
ensure_vendor() {
    local nupkgs=$(ls "$ROOT/vendor/nuget/"*.nupkg 2>/dev/null | wc -l)
    local debs=$(ls "$ROOT/vendor/debs-runtime/"*.deb 2>/dev/null | wc -l)
    [ "$nupkgs" -gt 100 ] || die "vendor/nuget incomplete ($nupkgs packages) — cannot build offline."
    [ "$debs" -ge 1 ]       || die "vendor/debs-runtime missing .deb files."
    log "Vendored NuGet feed OK ($nupkgs packages), runtime .debs OK ($debs)."
}

# --- Phase 4: Configure .env -------------------------------------------------
ensure_env() {
    if [ -f "$ENV_FILE" ]; then
        log ".env present — keeping existing configuration."
        return 0
    fi

    echo
    warn "=== BCA node configuration ==="
    warn "This explorer connects to your BCA daemon via RPC."
    warn "Enter the details below (each install points at its own node)."
    echo

    if [ -t 0 ]; then
        read -rp "  Node IP or hostname [127.0.0.1]: " _host
        _host="${_host:-127.0.0.1}"
        read -rp "  RPC port [7332]: " _rpcp
        _rpcp="${_rpcp:-7332}"
        read -rp "  P2P port [7333]: " _p2pp
        _p2pp="${_p2pp:-7333}"
        read -rp "  RPC user: " _user
        read -rsp "  RPC password: " _pass
        echo
    else
        warn "Non-interactive shell — writing .env from example."
        cp "$ENV_EXAMPLE" "$ENV_FILE"
        warn "Edit .env with your node details before starting."
        return 0
    fi

    [ -n "$_user" ] && [ -n "$_pass" ] || die "RPC user and password cannot be empty."

    cat > "$ENV_FILE" <<EOF
# Generated by ubuntu22.sh — edit or delete to re-enter.
# Never commit .env (it holds RPC credentials).
BCA_RPC_URL=http://${_host}:${_rpcp}/
BCA_RPC_USER=${_user}
BCA_RPC_PASS=${_pass}
BCA_NODE_ENDPOINT=${_host}:${_p2pp}
EOF
    chmod 600 "$ENV_FILE"
    log ".env written for node ${_host} (RPC :${_rpcp}, P2P :${_p2pp})."
}

# --- Phase 5: Data directory -------------------------------------------------
ensure_data() {
    mkdir -p "$ROOT/data/nbxplorer" "$ROOT/data/stats"
    if [ -n "$(ls -A "$ROOT/data/nbxplorer" 2>/dev/null)" ]; then
        log "Existing chain data found in ./data — will resume, not resync."
    else
        log "Fresh ./data — first sync starts from the node (one time)."
    fi
}

# --- Phase 6: Build & start --------------------------------------------------
build_and_start() {
    log "Building all Docker images..."
    docker compose -f "$COMPOSE_FILE" build 2>&1 | tail -20

    log "Starting the stack..."
    docker compose -f "$COMPOSE_FILE" up -d 2>&1

    # Wait for the API to answer
    log "Waiting for NBXplorer API (first sync takes a while)..."
    for _ in $(seq 1 120); do
        if curl -sf http://localhost:24444/v1/cryptos/bca/status >/dev/null 2>&1; then
            log "NBXplorer API is answering!"
            break
        fi
        sleep 5
    done

    # Wait for stats service
    for _ in $(seq 1 12); do
        if curl -sf http://localhost:8090/health >/dev/null 2>&1; then
            log "bca-stats is healthy."
            break
        fi
        sleep 5
    done

    log "Stack status:"
    docker compose -f "$COMPOSE_FILE" ps

    log ""
    log "=== DONE ==="
    log "Monitor page : http://localhost:8080"
    log "API endpoint : http://localhost:24444/v1/cryptos/bca/status"
    log "Stats API    : http://localhost:8090"
    log ""
    log "Synced data persists in ./data across restarts and reinstalls."
}

# --- Main --------------------------------------------------------------------
main() {
    log "obsidian-BCA-explorer installer for Ubuntu 22.04"
    log "Root: $ROOT"
    log ""
    ensure_docker
    load_images
    ensure_vendor
    ensure_env
    ensure_data
    build_and_start
}

main "$@"
