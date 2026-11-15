#!/usr/bin/env python3
"""Publish a read-only snapshot of LetsVibe pages, leaderboard, and project reports to Cloudflare KV."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, List

BASE_URL = "http://127.0.0.1:8000"
WORKER_DIR = Path(__file__).resolve().parent
KV_NAMESPACE_ID = "71ab5e501e2b430dacbc2e006bf0d619"

STATIC_PAGES = [
    "/",
    "/leaderboard",
    "/how-it-works",
    "/faq",
    "/privacy",
    "/terms",
    "/grading-curve",
]


def _fetch_text(path: str) -> str:
    req = urllib.request.Request(f"{BASE_URL}{path}", headers={"User-Agent": "letsvibe-snapshot/1.0"})
    with urllib.request.urlopen(req, timeout=20.0) as resp:
        return resp.read().decode("utf-8")


def build_snapshot_entries() -> List[Dict[str, str]]:
    entries: List[Dict[str, str]] = []

    for page in STATIC_PAGES:
        try:
            entries.append({"key": f"page:{page}", "value": _fetch_text(page)})
        except Exception as exc:
            print(f"Warning: skipping static page {page}: {exc}", file=sys.stderr)

    lb_curve_raw = _fetch_text("/api/leaderboard?sort_by=overall&curve=true")
    lb_raw = _fetch_text("/api/leaderboard?sort_by=overall&curve=false")
    engines_raw = _fetch_text("/api/engines")

    entries.append({"key": "api:leaderboard:curve", "value": lb_curve_raw})
    entries.append({"key": "api:leaderboard:raw", "value": lb_raw})
    entries.append({"key": "api:engines", "value": engines_raw})

    lb_data = json.loads(lb_curve_raw)
    slugs = [item["full_slug"] for item in lb_data.get("items", []) if item.get("full_slug")]
    for slug in slugs:
        quoted = "/".join(urllib.parse.quote(part, safe="") for part in slug.split("/"))
        try:
            proj_json = _fetch_text(f"/api/project/{quoted}")
            entries.append({"key": f"api:project:{slug.lower()}", "value": proj_json})
        except Exception as exc:
            print(f"Warning: failed to snapshot project {slug}: {exc}", file=sys.stderr)

    return entries


def main() -> int:
    entries = build_snapshot_entries()
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as tmp:
        json.dump(entries, tmp)
        tmp_path = tmp.name

    try:
        cmd = [
            "npx",
            "--yes",
            "wrangler",
            "kv",
            "bulk",
            "put",
            tmp_path,
            "--namespace-id",
            KV_NAMESPACE_ID,
            "--remote",
        ]
        res = subprocess.run(cmd, cwd=str(WORKER_DIR), check=True, text=True)
        print(f"Published {len(entries)} snapshot keys to Cloudflare KV.")
        return res.returncode
    finally:
        Path(tmp_path).unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
