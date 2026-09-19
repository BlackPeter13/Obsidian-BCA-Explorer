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
    # Remove stale apt sources pointing to missing local paths (e.g. BitcoinAtomCore offline repos).
    find /etc/apt/sources.list.d/ -name '*.list' -exec grep -l 'file:' {} \; 2>/dev/null | while read f; do
        rm -f "$f"
        warn "Removed stale local apt source: $f"
    done
    [ -f /etc/apt/sources.list ] && grep -v 'file:' /etc/apt/sources.list > /tmp/sources.clean 2>/dev/null && mv /tmp/sources.clean /etc/apt/sources.list || true
    apt-get update -qq 2>&1 | grep -v 'Failed to fetch\|Some index' || true
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

# --- Phase 1b: Fix project files for zip downloads (no vendored blobs) --
fix_project_files() {
    # If Dockerfile still has the old COPY vendor/nuget/ line, patch it.
    if [ -f "$ROOT/Dockerfile" ] && grep -q 'COPY vendor/nuget/' "$ROOT/Dockerfile" 2>/dev/null; then
        warn "Patching Dockerfile for zip download compatibility..."
        # Remove old COPY vendor/nuget/ line, replace with mkdir + dotnet restore
        sed -i '/COPY vendor\/nuget\//d' "$ROOT/Dockerfile"
        sed -i 's/# Vendored feed must arrive BEFORE restore (offline by construction)./# Vendored feed is optional — NuGet.config falls back to nuget.org./' "$ROOT/Dockerfile"
        # Insert mkdir before dotnet restore line
        sed -i 's|RUN dotnet restore NBXplorer/NBXplorer.csproj --nologo|RUN mkdir -p ./vendor/nuget \&\& dotnet restore NBXplorer/NBXplorer.csproj --nologo|' "$ROOT/Dockerfile"
        log "Dockerfile patched."
    fi

    # Also fix the runtime Dockerfile section (vendor/debs-runtime).
    if [ -f "$ROOT/Dockerfile" ] && grep -q "COPY vendor/debs-runtime" "$ROOT/Dockerfile" 2>/dev/null; then
        warn "Patching Dockerfile runtime section..."
        sed -i "/COPY vendor\/debs-runtime\/\*\.deb/d" "$ROOT/Dockerfile"
        sed -i "/# dpkg (not apt): two-phase/d" "$ROOT/Dockerfile"
        sed -i "/RUN dpkg --unpack/d" "$ROOT/Dockerfile"
        sed -i "/dpkg --configure -a/d" "$ROOT/Dockerfile"
        sed -i "/rm -rf \/tmp\/debs-runtime/d" "$ROOT/Dockerfile"
        sed -i "/# curl only for the HEALTHCHECK, from vendored/d" "$ROOT/Dockerfile"
        sed -i "s|RUN useradd -m -d /datadir -s /usr/sbin/nologin nbxplorer \&\& mkdir -p /datadir|RUN apt-get update -qq \&\& apt-get install -y -qq curl \&\& useradd -m -d /datadir -s /usr/sbin/nologin nbxplorer \&\& mkdir -p /datadir|" "$ROOT/Dockerfile"
        log "Dockerfile runtime section patched."
    fi

    # Fix .dockerignore to exclude vendor/ completely.
    if [ -f "$ROOT/.dockerignore" ] && grep -q 'vendor/\*' "$ROOT/.dockerignore" 2>/dev/null; then
        warn "Patching .dockerignore..."
        sed -i '/vendor\/\*/d; /!vendor\/nuget/d; /!vendor\/debs-runtime/d' "$ROOT/.dockerignore"
        echo "vendor/" >> "$ROOT/.dockerignore"
        log ".dockerignore patched."
    fi

    # Fix NuGet.config to add nuget.org fallback.
    if [ -f "$ROOT/NuGet.config" ] && grep -q '<clear />' "$ROOT/NuGet.config" 2>/dev/null; then
        warn "Patching NuGet.config for nuget.org fallback..."
        cat > "$ROOT/NuGet.config" << 'NUGETEOF'
<?xml version="1.0" encoding="utf-8"?>
<configuration>
  <packageSources>
    <add key="obsidian-local" value="vendor/nuget" />
    <add key="nuget.org" value="https://api.nuget.org/v3/index.json" />
  </packageSources>
</configuration>
NUGETEOF
        log "NuGet.config patched."
    fi
}

# --- Phase 2: Base images (docker compose build handles pulling) ------
load_images() {
    log "Base images will be pulled by docker compose build as needed."
}

# --- Phase 3: Verify vendored files -----------------------------------------
ensure_vendor() {
    local nupkgs=$(ls "$ROOT/vendor/nuget/"*.nupkg 2>/dev/null | wc -l)
    local debs=$(ls "$ROOT/vendor/debs-runtime/"*.deb 2>/dev/null | wc -l)
    if [ "$nupkgs" -gt 100 ]; then
        log "Vendored NuGet feed OK ($nupkgs packages)."
    else
        warn "Vendored NuGet feed missing ($nupkgs packages) — build may need network."
    fi
    if [ "$debs" -ge 1 ]; then
        log "Vendored runtime .debs OK ($debs packages)."
    else
        warn "Vendored .debs missing — base image will pull from Docker Hub."
    fi
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
    fix_project_files
    load_images
    ensure_vendor
    ensure_env
    ensure_data
    build_and_start
}

main "$@"
