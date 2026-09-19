#!/usr/bin/env bash
#
# obsidian-BCA-explorer — main entrypoint.
#
# Usage: ./obsidian.sh [install|up|down|restart|status|logs|help]
#   install   build + start + verify            (default when no command given)
#   up        start the stack (rebuilds images)
#   down      stop the stack (index data in the volume is kept)
#   restart   restart the explorer service
#   status    container state + live chain status via the HTTP API
#   logs      follow explorer logs
#
# Needs: Docker Engine 24+ with Compose v2, network access to MCR
# (dotnet SDK/runtime images) + NuGet, and the BCA daemon host in .env.
#
# Testing hook: OBSIDIAN_TEST_OS / OBSIDIAN_TEST_ARCH override uname.

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
SERVICE="nbxplorer-bca"
API="http://localhost:24444/v1/cryptos/bca"

log()  { printf '[obsidian] %s\n' "$*"; }
die()  { printf '[obsidian] ERROR: %s\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

ensure_docker() {
    have docker && docker compose version >/dev/null 2>&1 \
        || die "need Docker Engine 24+ with Compose v2."
    log "docker OK: $(docker --version | head -c 60)"
}

ensure_env() {
    [ -f "$ENV_FILE" ] || { cp "$ENV_EXAMPLE" "$ENV_FILE"; log ".env created from example — edit it for your daemon."; }
    log ".env present."
}

wait_ready() {
    log "waiting for API (up to 300s, first sync takes a while)..."
    for _ in $(seq 1 60); do
        if curl -sf "$API/status" >/dev/null 2>&1; then
            log "API answering."
            return 0
        fi
        sleep 5
    done
    die "API did not answer. Check: docker compose -f $COMPOSE logs --tail 50 $SERVICE"
}

cmd_install() {
    ensure_docker
    ensure_env
    log "building images..."
    docker compose -f "$COMPOSE" build
    log "starting stack..."
    docker compose -f "$COMPOSE" up -d
    wait_ready
    cmd_status
    log "done. API :24444 · monitor :8080"
}

cmd_up() {
    ensure_docker
    docker compose -f "$COMPOSE" up -d --build
    wait_ready
}

cmd_down()    { docker compose -f "$COMPOSE" down; }
cmd_restart() { docker compose -f "$COMPOSE" restart "$SERVICE"; wait_ready; }

cmd_status() {
    docker compose -f "$COMPOSE" ps
    if curl -sf "$API/status" 2>/dev/null | head -c 600; then
        echo
    else
        log "API not answering (still starting or stopped)."
    fi
}

cmd_logs() { docker compose -f "$COMPOSE" logs -f --tail 100 "$SERVICE"; }

usage() {
    sed -n '2,/^$/p' "$0" | sed 's/^# \?//'
}

case "${1:-install}" in
    install)  cmd_install ;;
    up)       cmd_up ;;
    down)     cmd_down ;;
    restart)  cmd_restart ;;
    status)   cmd_status ;;
    logs)     cmd_logs ;;
    help|-h|--help) usage ;;
    *) die "unknown command '$1'. See: ./obsidian.sh help" ;;
esac
