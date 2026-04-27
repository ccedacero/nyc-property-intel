"""Email alerting via Resend. No-op if RESEND_API_KEY is unset.

Used by scripts/sync_all.py to surface sync failures and drift warnings.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

ALERT_FROM = os.environ.get("ALERT_FROM_EMAIL", "alerts@nycpropertyintel.com")
ALERT_TO = os.environ.get("ALERT_EMAIL_TO", "cristian.cedacero@gmail.com")


def send_alert(subject: str, body_plain: str, body_html: str | None = None) -> bool:
    """Send an alert email. Returns True on success, False on misconfig or API error.

    Silently no-ops if RESEND_API_KEY is unset — never blocks a sync.
    """
    api_key = os.environ.get("RESEND_API_KEY")
    if not api_key:
        logger.info("RESEND_API_KEY unset — skipping alert (would have sent: %r)", subject)
        return False

    try:
        import resend
    except ImportError:
        logger.warning("resend package not installed — install with `uv add resend`")
        return False

    resend.api_key = api_key
    payload = {
        "from": ALERT_FROM,
        "to": [ALERT_TO],
        "subject": subject,
        "text": body_plain,
    }
    if body_html:
        payload["html"] = body_html
    try:
        resp = resend.Emails.send(payload)
        logger.info("alert sent: id=%s subject=%r", resp.get("id"), subject)
        return True
    except Exception as e:
        logger.exception("alert send failed: %s", e)
        return False
