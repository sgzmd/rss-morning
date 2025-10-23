import types

import pytest

from rss_morning import emailing


def test_send_email_report_without_resend_logs_error(caplog):
    caplog.set_level("ERROR")
    emailing.resend = None

    emailing.send_email_report(payload=[], is_summary=False, to_address="user@example.com")

    assert "resend package is required" in caplog.text


def test_send_email_report_sends_when_configured(monkeypatch):
    calls = []

    class FakeEmails:
        @staticmethod
        def send(payload):
            calls.append(payload)
            return types.SimpleNamespace(id="123")

    fake_resend = types.SimpleNamespace(Emails=FakeEmails, api_key="")
    monkeypatch.setattr(emailing, "resend", fake_resend)
    monkeypatch.setenv("RESEND_API_KEY", "key")

    payload = [{"title": "Example", "summary": "", "text": "", "url": "https://example.com"}]

    emailing.send_email_report(
        payload=payload,
        is_summary=False,
        to_address="user@example.com",
        from_address="sender@example.com",
        subject="Subject",
    )

    assert calls
    assert calls[0]["to"] == ["user@example.com"]
