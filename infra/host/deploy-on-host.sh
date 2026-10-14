#!/usr/bin/env bash
# ===========================================================================
# One-shot deploy ON an existing EC2 host.
#
# Assumes: Docker + the Compose plugin are installed and `.env` is filled in
# from infra/host/env.example. Builds the image from this checkout, starts the
# stack, then runs the one-time schema + seed steps.
#
#   cd <repo>
#   ./infra/host/deploy-on-host.sh
#   # if your user is not in the docker group:  sudo ./infra/host/deploy-on-host.sh
# ===========================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

log() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }

[ -f .env ] || { echo "Missing .env - cp infra/host/env.example .env and fill it in." >&2; exit 1; }
command -v docker >/dev/null || { echo "Docker not installed - see docs/deploy-existing-host.md." >&2; exit 1; }

COMPOSE=(docker compose -f docker-compose.host.yml --env-file .env)

log "Building and starting the stack"
"${COMPOSE[@]}" up -d --build

log "Waiting for the API to be ready"
ready=""
for i in $(seq 1 36); do
  if "${COMPOSE[@]}" exec -T api python -c "from apps.api.db import init_db; init_db()" 2>/dev/null; then
    ready="yes"; break
  fi
  echo "  ...still starting (${i}/36)"; sleep 5
done
[ -n "$ready" ] || { echo "API did not become ready; run: ${COMPOSE[*]} logs api" >&2; exit 1; }

log "Seeding tenant, KB articles, policies, and sample tickets (idempotent)"
"${COMPOSE[@]}" exec -T api python -m apps.api.seed

log "Stack status"
"${COMPOSE[@]}" ps

log "Health check"
curl -fsS http://localhost/health && echo || true

cat <<EOF

$(log "Deployed")
  App URL   : http://$(curl -s http://169.254.169.254/latest/meta-data/public-ipv4 2>/dev/null || echo '<host-ip>')/health
  Logs      : ${COMPOSE[*]} logs -f api
  Redeploy  : git pull && ./infra/host/deploy-on-host.sh
EOF
