"""
notifier.py — notification scaffolding.

All channels are disabled by default. Enable via environment variables:
  LIST_NOTIFY_EMAIL=true  (+ SMTP settings)
  LIST_NOTIFY_SLACK=true  (+ LIST_SLACK_WEBHOOK)
  LIST_NOTIFY_TEAMS=true  (+ LIST_TEAMS_WEBHOOK)

This module is imported but does nothing until configured.
"""
from __future__ import annotations
import json
import logging
import smtplib
from email.mime.text import MIMEText
from typing import Optional

import requests as _requests

from config import (
    NOTIFY_EMAIL_ENABLED, NOTIFY_SLACK_ENABLED, NOTIFY_TEAMS_ENABLED,
    SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM,
    SLACK_WEBHOOK_URL, TEAMS_WEBHOOK_URL,
)

log = logging.getLogger(__name__)


def _send_email(subject: str, body: str, to: Optional[str] = None) -> None:
    if not NOTIFY_EMAIL_ENABLED or not SMTP_HOST:
        return
    try:
        msg = MIMEText(body, "plain")
        msg["Subject"] = subject
        msg["From"]    = SMTP_FROM
        msg["To"]      = to or SMTP_FROM
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
            s.starttls()
            if SMTP_USER:
                s.login(SMTP_USER, SMTP_PASSWORD)
            s.send_message(msg)
        log.info("Email notification sent: %s", subject)
    except Exception as exc:
        log.warning("Email notification failed: %s", exc)


def _send_slack(text: str) -> None:
    if not NOTIFY_SLACK_ENABLED or not SLACK_WEBHOOK_URL:
        return
    try:
        _requests.post(SLACK_WEBHOOK_URL, json={"text": text}, timeout=5)
        log.info("Slack notification sent")
    except Exception as exc:
        log.warning("Slack notification failed: %s", exc)


def _send_teams(title: str, text: str) -> None:
    if not NOTIFY_TEAMS_ENABLED or not TEAMS_WEBHOOK_URL:
        return
    try:
        payload = {
            "@type": "MessageCard",
            "@context": "https://schema.org/extensions",
            "summary": title,
            "title": title,
            "text": text,
        }
        _requests.post(TEAMS_WEBHOOK_URL, json=payload, timeout=5)
        log.info("Teams notification sent")
    except Exception as exc:
        log.warning("Teams notification failed: %s", exc)


# ── Public helpers ────────────────────────────────────────────────────────────

def notify_case_created(case_number: str, title: str, severity: str) -> None:
    msg = f"[LIST] New case {case_number}: {title} (severity: {severity})"
    _send_email(f"New case: {case_number}", msg)
    _send_slack(msg)
    _send_teams(f"New case: {case_number}", msg)


def notify_bias_complete(case_number: str, gap_count: int) -> None:
    msg = f"[LIST] BIAS analysis complete for {case_number} — {gap_count} gap(s) found"
    _send_email(f"BIAS complete: {case_number}", msg)
    _send_slack(msg)
    _send_teams(f"BIAS complete: {case_number}", msg)


def notify_case_promoted(chain_key: str, case_number: str) -> None:
    msg = f"[LIST] Alert chain {chain_key} promoted to case {case_number}"
    _send_email(f"Case promoted: {case_number}", msg)
    _send_slack(msg)
    _send_teams(f"Case promoted: {case_number}", msg)
