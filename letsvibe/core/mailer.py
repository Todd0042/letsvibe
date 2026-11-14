"""
Email delivery module for LetsVibe.
Handles appeal notifications to the site admin and approval/decline notices to users.
"""

import logging
import os
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger("letsvibe.mailer")

# Where appeal notifications go: set ADMIN_EMAIL in .env (git-ignored; see .env.example). No default.
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "").strip()
LOG_DIR = Path("data")


def _record_email_locally(subject: str, recipient: str, body: str) -> None:
    """Logs sent emails to data/sent_emails.log for local tracking and verification."""
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_file = LOG_DIR / "sent_emails.log"
        timestamp = datetime.now().isoformat()
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"\n{'='*70}\n")
            f.write(f"TIME: {timestamp}\nTO: {recipient}\nSUBJECT: {subject}\n\n{body}\n")
            f.write(f"{'='*70}\n")
    except Exception as e:
        logger.warning(f"Failed to record email to log file: {e}")


def _dispatch_email(recipient: str, subject: str, text_content: str, html_content: Optional[str] = None) -> bool:
    """Dispatches email via SMTP if configured, or falls back to local log recording."""
    smtp_host = os.environ.get("SMTP_HOST")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER")
    smtp_pass = os.environ.get("SMTP_PASSWORD")
    smtp_from = os.environ.get("SMTP_FROM", "").strip() or (smtp_user or "")

    if not smtp_host or not smtp_user:
        # Development only: with no SMTP server configured, keep a local copy so the flow can be checked.
        # Real emails are never written to disk.
        _record_email_locally(subject, recipient, text_content)
        logger.info(f"[SIMULATED EMAIL] Subject: {subject}")
        return True

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = smtp_from
        msg["To"] = recipient

        msg.attach(MIMEText(text_content, "plain"))
        if html_content:
            msg.attach(MIMEText(html_content, "html"))

        with smtplib.SMTP(smtp_host, smtp_port, timeout=10.0) as server:
            server.starttls()
            if smtp_pass:
                server.login(smtp_user, smtp_pass)
            server.send_message(msg)

        logger.info(f"Email sent via {smtp_host}: {subject}")
        return True
    except Exception as e:
        logger.error(f"Failed to deliver SMTP email ({subject}): {e}")
        return False


def send_appeal_to_admin(appeal: Dict[str, Any]) -> bool:
    """
    Sends an appeal notification to the site admin (ADMIN_EMAIL in .env); skipped if none is configured.
    Note: ONLY called for clean appeals (violent/profane messages are blocked from emailing).
    """
    repo = appeal.get("full_slug") or appeal.get("target", "Unknown Repo")
    submitter = appeal.get("submitted_by", "unknown")
    msg_text = appeal.get("message", "")
    appeal_id = appeal.get("id", "N/A")
    created_at = appeal.get("created_at", datetime.now().isoformat())

    subject = f"[LetsVibe Appeal #{appeal_id}] New Vibe-Coding Review Request for {repo}"

    body = (
        f"Hi,\n\n"
        f"A user has submitted an appeal for a repository that was initially rejected as non-vibe-coded.\n\n"
        f"Appeal Details:\n"
        f"-----------------------------------------\n"
        f"Appeal ID: #{appeal_id}\n"
        f"Repository: {repo}\n"
        f"Submitted by GitHub user: @{submitter}\n"
        f"Submitted At: {created_at}\n\n"
        f"User Explanation:\n"
        f"-----------------------------------------\n"
        f"{msg_text}\n\n"
        f"-----------------------------------------\n"
        f"To review, approve, or decline this appeal, visit your Admin Console:\n"
        f"http://localhost:8088/#tab-admin\n\n"
        f"— LetsVibe Autonomous Engine"
    )

    if not ADMIN_EMAIL:
        logger.info("ADMIN_EMAIL is not set; appeal notification skipped (the appeal is still in the admin inbox).")
        return False
    return _dispatch_email(ADMIN_EMAIL, subject, body)

