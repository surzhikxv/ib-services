#!/usr/bin/env bash
set -euo pipefail

umask 077

BACKUP_ROOT="${BACKUP_ROOT:-/var/backups/kontur}"
RETENTION_DAYS="${RETENTION_DAYS:-30}"
POSTGRES_CONTAINER="${POSTGRES_CONTAINER:-kontur-postgres-1}"
POSTGRES_USER="${POSTGRES_USER:-kontur}"
POSTGRES_DB="${POSTGRES_DB:-kontur}"
METABASE_CONTAINER="${METABASE_CONTAINER:-kontur-metabase-1}"
N8N_CONTAINER="${N8N_CONTAINER:-kontur-n8n-1}"
METABASE_VOLUME="${METABASE_VOLUME:-/var/lib/docker/volumes/kontur_metabasedata/_data}"
N8N_VOLUME="${N8N_VOLUME:-/var/lib/docker/volumes/kontur_n8ndata/_data}"

mkdir -p /run/lock "$BACKUP_ROOT"
chmod 700 "$BACKUP_ROOT"
exec 9>/run/lock/kontur-backup.lock
if ! flock -n 9; then
    echo "Kontur backup is already running" >&2
    exit 0
fi

stamp="$(date -u +%Y-%m-%dT%H-%M-%SZ)"
target="$BACKUP_ROOT/$stamp"
mkdir -p "$target"
chmod 700 "$target"

stopped=()
restart_stopped() {
    local container
    for container in "${stopped[@]}"; do
        docker start "$container" >/dev/null || true
    done
    stopped=()
}
trap restart_stopped EXIT

docker exec "$POSTGRES_CONTAINER" pg_dump \
    -U "$POSTGRES_USER" \
    -d "$POSTGRES_DB" \
    --format=custom \
    --no-owner \
    --no-privileges > "$target/postgres.dump"
docker exec -i "$POSTGRES_CONTAINER" pg_restore --list < "$target/postgres.dump" >/dev/null

# Metabase H2 and n8n SQLite must be copied while their writers are stopped.
for container in "$METABASE_CONTAINER" "$N8N_CONTAINER"; do
    if [ "$(docker inspect -f '{{.State.Running}}' "$container")" = "true" ]; then
        docker stop -t 60 "$container" >/dev/null
        stopped+=("$container")
    fi
done

tar -C "$METABASE_VOLUME" -czf "$target/metabase-data.tar.gz" .
tar -C "$N8N_VOLUME" -czf "$target/n8n-data.tar.gz" .
tar -tzf "$target/metabase-data.tar.gz" >/dev/null
tar -tzf "$target/n8n-data.tar.gz" >/dev/null

restart_stopped
trap - EXIT

wait_for_http() {
    local url="$1"
    local expected="$2"
    local response
    local attempt
    for attempt in $(seq 1 60); do
        response="$(curl -fsS --max-time 3 "$url" 2>/dev/null || true)"
        if [[ "$response" == *"$expected"* ]]; then
            return 0
        fi
        sleep 2
    done
    echo "Service did not become healthy: $url" >&2
    return 1
}

wait_for_http "http://127.0.0.1:3000/api/health" '"status":"ok"'
wait_for_http "http://127.0.0.1:5678/healthz" 'ok'

sha256sum \
    "$target/postgres.dump" \
    "$target/metabase-data.tar.gz" \
    "$target/n8n-data.tar.gz" > "$target/SHA256SUMS"
(cd "$target" && sha256sum -c SHA256SUMS)
chmod 600 "$target"/*

# Delete only timestamp-named backup directories created by this script.
find "$BACKUP_ROOT" \
    -mindepth 1 \
    -maxdepth 1 \
    -type d \
    -name '20??-??-??T??-??-??Z' \
    -mtime "+$RETENTION_DAYS" \
    -exec rm -rf -- {} +

echo "Kontur backup completed: $target"
