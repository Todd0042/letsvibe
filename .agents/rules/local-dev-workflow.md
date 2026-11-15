---
trigger: always_on
description: Local development vs. live Docker deployment workflow for LetsVibe
---

# LetsVibe Local Dev → Live Docker Workflow

1. **Never test experimental changes against the live Docker container (`:8000`) or `./data/letsvibe.db`.**
2. **Test locally first on port `8001` with an isolated database:**
   ```bash
   cp data/letsvibe.db /tmp/letsvibe-dev.db
   LETSVIBE_DB_PATH=/tmp/letsvibe-dev.db PYTHONPATH=.:../letsvibe-engine \
     .venv/bin/uvicorn letsvibe.web.server:app --host 0.0.0.0 --port 8001 --reload
   ```
   - Local browser: `http://127.0.0.1:8001` (use `/dev-user-login` to switch between user and admin accounts locally).
   - LAN devices (phone/tablet): `http://<lan-ip>:8001` (Cloudflare Tunnel only routes `:8000`, so `:8001` stays private).
3. **Verify before deploying:**
   - Stop the `:8001` dev server.
   - Run `PYTHONPATH=.:../letsvibe-engine .venv/bin/pytest --ignore=tests/ui -q` and confirm all tests pass.
   - Commit changes.
4. **Deploy to live (`:8000`) with zero downtime:**
   - Sync repo to `/home/todd/storage/letsvibe-docker/app/` (always excluding `.git`, `.venv`, `data`, `.env`, `__pycache__`, `.pytest_cache`).
   - **UI-only edits (`letsvibe/web/templates/` or `letsvibe/web/static/`):** `rsync` alone applies them immediately via hot-mounts — **no container restart or swap needed**.
   - **Web/Python edits:** Run `/home/todd/storage/letsvibe-docker/app/deploy/blue-green/deploy.sh` to build the idle slot (`blue` `:8011` ↔ `green` `:8012`), verify `/api/health`, reload `letsvibe-proxy` (`:8000`), and stop the old slot.
   - **Worker/DB/Evaluator edits:** Only rebuild `letsvibe-worker` (`docker compose -f /home/todd/storage/letsvibe-docker/app/deploy/blue-green/docker-compose.yml up -d --build letsvibe-worker`) when no Ollama audit is actively running.
