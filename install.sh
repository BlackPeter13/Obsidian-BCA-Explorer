#!/usr/bin/env bash
#
# obsidian-BCA-explorer — installer.
#
# Phase 0 (online, needs root): Docker Engine bootstrap — skipped when
# Docker is already present. Set SKIP_DOCKER=1 to fail instead of installing.
# Phases 1+ are fully OFFLINE (vendored images/feeds, no pulls):
#   1. checks Docker Engine 24+ with Compose v2
#   2. loads base images from images/*.tar (no pulls)
#   3. verifies the vendored NuGet feed + runtime .debs are present
#   4. creates .env from the example (edit it for your daemon)
#   5. keeps existing ./data (chain cache, stats DB) — restart/reinstall
#      only ever UPDATES, never resyncs from scratch
#   6. builds all images offline and starts the stack, then verifies
#
# All builds happen in Linux containers (never on the host OS).

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# git-bash/MSYS pwd looks like /c/Users/... which native Windows
# binaries (docker.exe) cannot resolve — rewrite to C:/Users/...
# form. No-op on real Linux.
if [[ "$ROOT" =~ ^/[a-zA-Z]/ ]]; then
    DRIVE="$(echo "${ROOT:1:1}" | tr '[:lower:]' '[:upper:]')"
    ROOT="$DRIVE:${ROOT:2}"
fi
cd "$ROOT"

COMPOSE="$ROOT/docker-compose.yml"
ENV_FILE="$ROOT/.env"
ENV_EXAMPLE="$ROOT/.env.example"
API="http://localhost:24444/v1/cryptos/bca"

log()  { printf '[install] %s\n' "$*"; }
die()  { printf '[install] ERROR: %s\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

# Phase 0 (needs internet + root): Docker Engine bootstrap. Skipped
# automatically when Docker is already present. Everything after this
# is fully offline.
ensure_docker() {
    if have docker && docker compose version >/dev/null 2>&1; then
        log "docker OK: $(docker --version | head -c 60) (bootstrap skipped)"
        return 0
    fi
    [ "${SKIP_DOCKER:-0}" = "1" ] && die "docker not present and SKIP_DOCKER=1."
    [ "$(uname -s)" = "Linux" ] || die "no docker found and host is not Linux — install Docker Desktop / Engine manually, then re-run."
    have apt-get || die "no docker and no apt-get — install Docker Engine 24+ with Compose v2 manually, then re-run."
    SUDO=""
    [ "$(id -u)" -ne 0 ] && SUDO="sudo"
    have sudo || [ "$(id -u)" -eq 0 ] || die "need root (or sudo) to install docker."
    if ! have curl; then $SUDO apt-get update; $SUDO apt-get install -y ca-certificates curl gnupg; fi
    log "adding Docker official apt repo (Debian/Ubuntu)..."
    $SUDO install -m 0755 -d /etc/apt/keyrings
    $SUDO curl -fsSL https://download.docker.com/linux/$(. /etc/os-release && echo "$ID")/gpg \
        -o /etc/apt/keyrings/docker.asc
    $SUDO chmod a+r /etc/apt/keyrings/docker.asc
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/$(. /etc/os-release && echo "$ID") \
$(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
        $SUDO tee /etc/apt/sources.list.d/docker.list > /dev/null
    $SUDO apt-get update
    $SUDO apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
    if have systemctl; then
        $SUDO systemctl enable --now docker
    else
        $SUDO service docker start 2>/dev/null || die "could not start docker daemon — start it manually, then re-run."
    fi
    have docker && docker compose version >/dev/null 2>&1 \
        || die "docker install finished but 'docker compose version' still fails."
    log "docker installed: $(docker --version | head -c 60)"
    if [ "$(id -u)" -ne 0 ]; then
        $SUDO usermod -aG docker "$(id -un)" 2>/dev/null || true
        log "added $(id -un) to the docker group — log out/in if docker says 'permission denied'."
    fi
}

declare -A IMAGES=(
    ["mcr.microsoft.com/dotnet/sdk:8.0-bookworm-slim"]="images/dotnet-sdk-8.0-bookworm-slim.tar"
    ["mcr.microsoft.com/dotnet/aspnet:8.0-bookworm-slim"]="images/dotnet-aspnet-8.0-bookworm-slim.tar"
    ["python:3.11-slim-bookworm"]="images/python-3.11-slim-bookworm.tar"
    ["nginx:alpine"]="images/nginx-alpine.tar"
)

check_host() {
    case "$(uname -m)" in
        x86_64|amd64) log "arch OK: $(uname -m) (native amd64 images)" ;;
        *) log "WARNING: $(uname -m) is not amd64 — images run emulated (slow) or not at all." ;;
    esac
}


ensure_images() {
    for ref in "${!IMAGES[@]}"; do
        tar="${IMAGES[$ref]}"
        if docker image inspect "$ref" >/dev/null 2>&1; then
            log "image present: $ref"
        else
            [ -f "$ROOT/$tar" ] || die "image $ref missing and $tar not found — cannot install offline."
            log "loading $ref from $tar ..."
            docker load -i "$ROOT/$tar"
        fi
    done
}

ensure_vendor() {
    count=$(ls "$ROOT/vendor/nuget/"*.nupkg 2>/dev/null | wc -l)
    [ "$count" -gt 100 ] || die "vendor/nuget incomplete ($count packages) — cannot restore offline."
    log "vendored NuGet feed OK ($count packages)"
    dcount=$(ls "$ROOT/vendor/debs-runtime/"*.deb 2>/dev/null | wc -l)
    [ "$dcount" -ge 1 ] || die "vendor/debs-runtime missing curl .debs."
    log "vendored runtime .debs OK ($dcount packages)"
}

ensure_env() {
    if [ -f "$ENV_FILE" ]; then
        log ".env present — keeping it. Delete it to re-enter node details."
        return 0
    fi
    echo
    echo "=== BCA node connection (each install points at its own node) ==="
    if [ -t 0 ]; then
        read -rp "Node IP or hostname: " _host
        [ -n "$_host" ] || die "node address cannot be empty."
        read -rp "RPC port [7332]: " _rpcp
        _rpcp="${_rpcp:-7332}"
        read -rp "P2P port [7333]: " _p2pp
        _p2pp="${_p2pp:-7333}"
        read -rp "RPC user: " _user
        read -rsp "RPC password: " _pass
        echo
    else
        log "non-interactive shell — writing .env from example; edit it before starting."
        cp "$ENV_EXAMPLE" "$ENV_FILE"
        die ".env needs real node details — edit $ENV_FILE and re-run."
    fi
    [ -n "$_user" ] && [ -n "$_pass" ] || die "RPC user/password cannot be empty."
    {
        echo "# Generated by install.sh — edit or delete to re-enter."
        echo "BCA_RPC_URL=http://${_host}:${_rpcp}/"
        echo "BCA_RPC_USER=${_user}"
        echo "BCA_RPC_PASS=${_pass}"
        echo "BCA_NODE_ENDPOINT=${_host}:${_p2pp}"
    } > "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    log ".env written for node ${_host} (RPC :${_rpcp}, P2P :${_p2pp})."
}

ensure_data() {
    mkdir -p "$ROOT/data/nbxplorer" "$ROOT/data/stats"
    if [ -n "$(ls -A "$ROOT/data/nbxplorer" 2>/dev/null)" ]; then
        log "existing chain data found in ./data — will resume, not resync."
    else
        log "fresh ./data — first sync starts from the node (one time)."
    fi
}

wait_ready() {
    log "waiting for API (up to 10 min; first sync takes a while)..."
    for _ in $(seq 1 120); do
        if curl -sf "$API/status" >/dev/null 2>&1; then
            log "API answering."
            return 0
        fi
        sleep 5
    done
    die "API did not answer. Check: docker compose -f $COMPOSE logs --tail 50 nbxplorer-bca"
}

main() {
    check_host
    ensure_docker
    ensure_images
    ensure_vendor
    ensure_env
    ensure_data
    log "building images (offline: vendored feed + debs only)..."
    docker compose -f "$COMPOSE" build
    log "starting stack..."
    docker compose -f "$COMPOSE" up -d
    wait_ready
    docker compose -f "$COMPOSE" ps
    curl -sf "$API/status" | head -c 400
    echo
    log "done. API :24444 · monitor :8080"
    log "synced data persists in ./data across restarts and reinstalls."
}

main "$@"
