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
   - Run `pytest` and confirm all tests pass.
   - Commit/push changes, then run `deploy-to-docker` to rebuild and restart the live `letsvibe` container on `:8000`.
