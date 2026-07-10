#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [ "$(id -u)" -ne 0 ]; then
    echo "Run as root: sudo ./ops/deploy.sh" >&2
    exit 2
fi
if [ ! -f .env ]; then
    echo "Missing $ROOT/.env" >&2
    exit 2
fi
if [ ! -s raw/bothelp_raw.json ]; then
    echo "Missing or empty $ROOT/raw/bothelp_raw.json" >&2
    exit 2
fi
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    echo "Tracked files are dirty; deploy only a committed tree" >&2
    exit 2
fi

version="${1:-$(git rev-parse --short=12 HEAD)}"
image="kontur-app:${version}"
stamp="$(date -u +%Y-%m-%dT%H-%M-%SZ)"
rollback_image="kontur-app:rollback-${stamp}"

set_env_value() {
    local key="$1"
    local value="$2"
    python - "$ROOT/.env" "$key" "$value" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
key = sys.argv[2]
value = sys.argv[3]
prefix = key + "="
lines = path.read_text(encoding="utf-8").splitlines()
for index, line in enumerate(lines):
    if line.startswith(prefix):
        lines[index] = prefix + value
        break
else:
    lines.append(prefix + value)
path.write_text("\n".join(lines) + "\n", encoding="utf-8")
path.chmod(0o600)
PY
}

wait_healthy() {
    local container="$1"
    local attempts="${2:-60}"
    local status
    local i
    for i in $(seq 1 "$attempts"); do
        status="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container" 2>/dev/null || true)"
        if [ "$status" = "healthy" ] || [ "$status" = "running" ]; then
            return 0
        fi
        if [ "$status" = "unhealthy" ] || [ "$status" = "exited" ] || [ "$status" = "dead" ]; then
            docker logs --tail 80 "$container" >&2 || true
            return 1
        fi
        sleep 2
    done
    docker logs --tail 80 "$container" >&2 || true
    return 1
}

old_app_id="$(docker inspect -f '{{.Image}}' kontur-app-1)"
old_compose_bot=false
if docker inspect kontur-bot-1 >/dev/null 2>&1; then
    old_compose_bot=true
fi
systemd_bot_active=false
if systemctl is-active --quiet kontur-bot.service; then
    systemd_bot_active=true
fi

echo "Creating verified backup before deploy..."
systemctl start kontur-backup.service

docker tag "$old_app_id" "$rollback_image"
echo "Building $image..."
if ! docker build --pull=false -t "$image" .; then
    echo "Clean build unavailable; using the verified-image overlay fallback..." >&2
    docker build \
        --pull=false \
        --build-arg "BASE_IMAGE=$rollback_image" \
        -f ops/Dockerfile.app-overlay \
        -t "$image" \
        .
fi

set_env_value KONTUR_IMAGE "$image"
set_env_value COMPOSE_PROJECT_NAME kontur
docker compose config --quiet

rolled_back=false
rollback() {
    local exit_code=$?
    if [ "$rolled_back" = true ]; then
        exit "$exit_code"
    fi
    rolled_back=true
    echo "Deploy failed; rolling back to $rollback_image" >&2
    set_env_value KONTUR_IMAGE "$rollback_image"
    docker compose up -d --no-deps app || true
    if [ "$old_compose_bot" = true ]; then
        docker compose up -d --no-deps bot || true
    else
        docker compose rm -sf bot >/dev/null 2>&1 || true
        if [ "$systemd_bot_active" = true ]; then
            systemctl enable kontur-bot.service >/dev/null 2>&1 || true
            systemctl start kontur-bot.service || true
        fi
    fi
    exit "$exit_code"
}
trap rollback ERR INT TERM

if [ "$systemd_bot_active" = true ]; then
    systemctl stop kontur-bot.service
fi

docker compose up -d --no-deps app bot
wait_healthy kontur-app-1
wait_healthy kontur-bot-1

curl -fsS http://127.0.0.1:8000/health | grep -q '"status":"ok"'
curl -fsS http://127.0.0.1:8081/health | grep -q 'ok'

if [ "$systemd_bot_active" = true ]; then
    systemctl disable kontur-bot.service >/dev/null
fi

trap - ERR INT TERM
echo "Deploy complete: $image"
echo "Rollback image retained locally: $rollback_image"
