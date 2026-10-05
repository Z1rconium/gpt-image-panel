import logging

from backend.app.core import redaction, secrets


def test_redaction_survives_and_reports_secret_lookup_failure(monkeypatch, caplog):
    def broken():
        raise RuntimeError("registry value hunter2 is unreadable")

    monkeypatch.setattr(secrets, "active_secret_values", broken)
    monkeypatch.setattr(redaction, "_secret_lookup_failure_logged", False)

    with caplog.at_level(logging.WARNING, logger=redaction.logger.name):
        first = redaction.redact_sensitive_text("key=abc explicit-secret", secret_values=("explicit-secret",))
        redaction.redact_sensitive_text("second call")

    assert "explicit-secret" not in first
    warnings = [record for record in caplog.records if "unavailable for redaction" in record.getMessage()]
    assert len(warnings) == 1
    assert "RuntimeError" in warnings[0].getMessage()
    assert "hunter2" not in warnings[0].getMessage()
