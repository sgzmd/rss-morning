import types

import pytest


from rss_morning import emailing


def test_send_email_report_without_resend_logs_error(caplog, monkeypatch):
    caplog.set_level("ERROR")
    monkeypatch.setattr(emailing, "resend", None)

    emailing.send_email_report(
        payload=[], is_summary=False, to_address="user@example.com"
    )

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

    payload = [
        {"title": "Example", "summary": "", "text": "", "url": "https://example.com"}
    ]

    emailing.send_email_report(
        payload=payload,
        is_summary=False,
        to_address="user@example.com",
        from_address="sender@example.com",
        subject="Subject",
    )

    assert calls
    assert calls[0]["to"] == ["user@example.com"]


def test_send_email_report_requires_key_and_sender(monkeypatch, caplog):
    fake_resend = types.SimpleNamespace(
        Emails=types.SimpleNamespace(send=lambda _: None)
    )
    monkeypatch.setattr(emailing, "resend", fake_resend)
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    emailing.send_email_report([], False, "user@example.com", "sender@example.com")
    assert "RESEND_API_KEY" in caplog.text

    caplog.clear()
    monkeypatch.setenv("RESEND_API_KEY", "key")
    monkeypatch.delenv("RESEND_FROM_EMAIL", raising=False)
    emailing.send_email_report([], False, "user@example.com")
    assert "Sender email is not configured" in caplog.text


@pytest.mark.parametrize(
    "payload,expected_fallback",
    [("raw", "raw"), (42, "42"), ([], None), ({}, None)],
)
def test_send_email_report_builds_fallback_and_defaults(
    monkeypatch, payload, expected_fallback
):
    calls = []
    sent = []
    fake_resend = types.SimpleNamespace(
        Emails=types.SimpleNamespace(
            send=lambda message: sent.append(message) or types.SimpleNamespace()
        ),
        api_key="",
    )
    monkeypatch.setattr(emailing, "resend", fake_resend)
    monkeypatch.setenv("RESEND_API_KEY", "key")
    monkeypatch.setenv("RESEND_FROM_EMAIL", "env-sender@example.com")
    monkeypatch.setattr(
        emailing,
        "build_email_html",
        lambda value, is_summary, fallback: calls.append(
            ("html", value, is_summary, fallback)
        )
        or "html",
    )
    monkeypatch.setattr(
        emailing,
        "build_email_text",
        lambda value, is_summary, fallback: calls.append(
            ("text", value, is_summary, fallback)
        )
        or "text",
    )

    emailing.send_email_report(payload, False, "to@example.com")

    assert calls[0][3] == expected_fallback
    assert sent[0]["from"] == "env-sender@example.com"
    assert sent[0]["subject"] == "RSS Morning Briefing"


def test_send_email_report_skips_empty_content(monkeypatch, caplog):
    monkeypatch.setattr(
        emailing,
        "resend",
        types.SimpleNamespace(Emails=types.SimpleNamespace(send=lambda _: None)),
    )
    monkeypatch.setenv("RESEND_API_KEY", "key")
    monkeypatch.setattr(emailing, "build_email_html", lambda *_args, **_kwargs: "")

    emailing.send_email_report([], False, "to@example.com", "from@example.com")

    assert "Email content is empty" in caplog.text


def test_send_email_report_logs_delivery_failure(monkeypatch, caplog):
    def fail(_message):
        raise RuntimeError("delivery failed")

    monkeypatch.setattr(
        emailing,
        "resend",
        types.SimpleNamespace(Emails=types.SimpleNamespace(send=fail), api_key=""),
    )
    monkeypatch.setenv("RESEND_API_KEY", "key")
    monkeypatch.setattr(emailing, "build_email_html", lambda *_args, **_kwargs: "html")
    monkeypatch.setattr(emailing, "build_email_text", lambda *_args, **_kwargs: "text")

    emailing.send_email_report([], False, "to@example.com", "from@example.com")

    assert "Failed to send email via Resend: delivery failed" in caplog.text
