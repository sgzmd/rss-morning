"""Email delivery via Resend."""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

from .renderers import build_email_html, build_email_text

logger = logging.getLogger(__name__)

try:  # pragma: no cover - dependency optional unless email requested
    import resend
except ImportError:  # pragma: no cover
    resend = None


def send_email_report(
    payload: Any,
    is_summary: bool,
    to_address: str,
    from_address: Optional[str] = None,
    subject: Optional[str] = None,
) -> None:
    """Send the prepared report via Resend."""
    if resend is None:
        logger.error("resend package is required for email functionality, but it's not installed.")
        return

    api_key = os.environ.get("RESEND_API_KEY")
    if not api_key:
        logger.error("RESEND_API_KEY environment variable is not set; skipping email delivery.")
        return

    sender = from_address or os.environ.get("RESEND_FROM_EMAIL")
    if not sender:
        logger.error("Sender email is not configured. Set --email-from or RESEND_FROM_EMAIL.")
        return

    html_content = build_email_html(payload, is_summary)
    if not html_content:
        logger.warning("Email content is empty; skipping email delivery.")
        return

    email_subject = subject or "RSS Morning Briefing"
    text_content = build_email_text(payload, is_summary)

    resend.api_key = api_key
    try:
        response = resend.Emails.send(
            {
                "from": sender,
                "to": [to_address],
                "subject": email_subject,
                "html": html_content,
                "text": text_content,
            }
        )
        logger.info("Sent email to %s via Resend (id %s)", to_address, getattr(response, "id", "unknown"))
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to send email via Resend: %s", exc)
