"""Email delivery via Resend."""

from __future__ import annotations

import importlib
import logging
import os
from dataclasses import dataclass
from typing import Any, Optional

from .renderers import build_email_html, build_email_text

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EmailDeliveryResult:
    """Content-free outcome returned to orchestration metrics."""

    attempted: bool
    sent: bool
    failed: bool


try:  # pragma: no cover - dependency optional unless email requested
    resend: Any = importlib.import_module("resend")
except ImportError:  # pragma: no cover
    resend = None


def send_email_report(
    payload: Any,
    is_summary: bool,
    to_address: str,
    from_address: Optional[str] = None,
    subject: Optional[str] = None,
) -> EmailDeliveryResult:
    """Send the prepared report via Resend."""
    if resend is None:
        logger.error(
            "resend package is required for email functionality, but it's not installed."
        )
        return EmailDeliveryResult(attempted=False, sent=False, failed=True)

    api_key = os.environ.get("RESEND_API_KEY")
    if not api_key:
        logger.error(
            "RESEND_API_KEY environment variable is not set; skipping email delivery."
        )
        return EmailDeliveryResult(attempted=False, sent=False, failed=True)

    sender = from_address or os.environ.get("RESEND_FROM_EMAIL")
    if not sender:
        logger.error(
            "Sender email is not configured. Set --email-from or RESEND_FROM_EMAIL."
        )
        return EmailDeliveryResult(attempted=False, sent=False, failed=True)

    fallback_text: Optional[str]
    if isinstance(payload, str):
        fallback_text = payload
    elif isinstance(payload, (list, dict)):
        fallback_text = None
    else:
        fallback_text = str(payload)

    html_content = build_email_html(payload, is_summary, fallback=fallback_text)
    if not html_content:
        logger.warning("Email content is empty; skipping email delivery.")
        return EmailDeliveryResult(attempted=False, sent=False, failed=True)

    email_subject = subject or "RSS Morning Briefing"
    text_content = build_email_text(payload, is_summary, fallback=fallback_text)

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
        logger.info(
            "Sent email to %s via Resend (id %s)",
            to_address,
            getattr(response, "id", "unknown"),
        )
        return EmailDeliveryResult(attempted=True, sent=True, failed=False)
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to send email via Resend: %s", exc)
        return EmailDeliveryResult(attempted=True, sent=False, failed=True)
