#!/usr/bin/env bash
set -euo pipefail

# Syncs the working LetsVibe repo to the storage NVMe Docker directory,
# preserving the live database and .env, then rebuilds and restarts the container.

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOCKER_ROOT="/home/todd/storage/letsvibe-docker"
APP_DIR="${DOCKER_ROOT}/app"
DATA_DIR="${APP_DIR}/data"
BACKUP_DIR="/home/todd/letsvibe-backups"

mkdir -p "${APP_DIR}" "${DATA_DIR}" "${BACKUP_DIR}"

echo "==> Syncing code from ${REPO_DIR} -> ${APP_DIR} (preserving data/ and .env)..."
rsync -a --delete \
  --exclude='.git/' \
  --exclude='.venv/' \
  --exclude='data/' \
  --exclude='*.db' \
  --exclude='*.db-wal' \
  --exclude='*.db-shm' \
  --exclude='.env' \
  --exclude='.env.*' \
  --exclude='__pycache__/' \
  --exclude='.pytest_cache/' \
  --exclude='temp_repos/' \
  --exclude='reports/' \
  --exclude='deploy/cloudflare-offline-worker/.wrangler/' \
  "${REPO_DIR}/" "${APP_DIR}/"

if [[ ! -f "${APP_DIR}/.env" ]]; then
  if [[ -f "${REPO_DIR}/.env" ]]; then
    cp "${REPO_DIR}/.env" "${APP_DIR}/.env"
    chmod 600 "${APP_DIR}/.env"
    echo "==> Initialized ${APP_DIR}/.env from repo .env"
  else
    echo "WARNING: No .env found at ${APP_DIR}/.env. Copy .env.example to ${APP_DIR}/.env before opening signups."
  fi
fi

# On first-ever deploy only, seed the Docker DB from the repo DB if present.
if [[ ! -f "${DATA_DIR}/letsvibe.db" && -f "${REPO_DIR}/data/letsvibe.db" ]]; then
  echo "==> First-time setup: copying existing data/letsvibe.db to ${DATA_DIR}/letsvibe.db..."
  cp -a "${REPO_DIR}/data/." "${DATA_DIR}/"
fi

# Check if a non-Docker dev uvicorn is currently bound to port 8000.
if ss -tlnp 2>/dev/null | grep -q ':8000 ' && ! docker ps --format '{{.Names}}' | grep -qx 'letsvibe'; then
  echo ""
  echo "WARNING: Port 8000 is currently in use (probably your dev uvicorn)."
  echo "Stop dev uvicorn first so the Docker container can bind 127.0.0.1:8000 for cloudflared."
  echo ""
fi

echo "==> Building and starting letsvibe container..."
export HOST_UID="$(id -u)"
export HOST_GID="$(id -g)"
cd "${APP_DIR}"
docker compose up -d --build

echo ""
echo "==> Done! Container status:"
docker compose ps
echo "    App & DB (storage NVMe): ${APP_DIR}"
echo "    Backups  (primary NVMe): ${BACKUP_DIR}"
echo "    Tunnel   (cloudflared):  already proxies letsvibe.todd0042.dev -> 127.0.0.1:8000 (no changes needed)"
