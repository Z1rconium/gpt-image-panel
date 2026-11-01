"""Regression coverage for the shared URL-masking and allowlist primitives.

The Wave A refactor unified the URL-redaction and host/origin/IP allowlist
implementations behind single primitives. These assertions lock the masking
output and the accept/reject behavior so the shared code paths cannot drift
apart again.
"""

import pytest

from backend.app.api import host_validation
from backend.app.core import overall_config, redaction, validators
from backend.app.core import settings as config


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("", ""),
        ("not a url", "***"),
        ("https://example.com", "https://example.com"),
        ("https://example.com/", "https://example.com/"),
        (
            "https://user:pass@example.com:8443/deep/path?q=1#frag",
            "https://***@example.com:8443/***?***#***",
        ),
        ("http://[::1]:8080/x", "http://[::1]:8080/***"),
    ],
)
def test_redact_url_masks_credentials_path_and_query(url, expected):
    assert redaction.redact_url(url) == expected


def test_log_scrub_and_display_mask_share_one_primitive():
    # The log scrub keeps the path (for diagnosis) while masking credentials and
    # query; the display mask hides the path too. Both are the same primitive.
    raw = "https://user:pass@example.com/deep?q=1"
    assert redaction.redact_sensitive_text(raw) == "https://[REDACTED]@example.com/deep?[REDACTED]"
    assert (
        redaction.mask_url(
            raw,
            token="[REDACTED]",
            invalid="[REDACTED_URL]",
            require_scheme=False,
        )
        == "https://[REDACTED]@example.com/deep?[REDACTED]"
    )


def test_split_allowlist_separator_policy():
    assert validators.split_allowlist(" a , b ; c ") == ["a", "b", "c"]
    # The legacy comma-only policy keeps a semicolon inside a single entry.
    assert validators.split_allowlist("a;b", separators=",") == ["a;b"]


def test_overall_config_split_list_delegates_to_shared_primitive():
    assert overall_config._split_list(" a ; b , c ") == validators.split_allowlist(" a ; b , c ")


def test_allowlist_validators_reject_and_accept():
    overall_config._validate_ip_list("10.0.0.1, 192.168.0.0/24")
    with pytest.raises(ValueError):
        overall_config._validate_ip_list("not-an-ip")

    overall_config._validate_host_or_origin_list("example.com, https://api.test")
    with pytest.raises(ValueError):
        overall_config._validate_host_or_origin_list("https://api.test/path")


def test_configured_allowed_hosts_expands_origins_and_ports(monkeypatch):
    monkeypatch.setattr(config, "ALLOWED_HOSTS", "example.com, https://api.test", raising=False)
    monkeypatch.setattr(config, "PUBLIC_ORIGIN", "", raising=False)

    hosts = host_validation._configured_allowed_hosts()
    assert "example.com" in hosts
    assert "api.test" in hosts
    assert "api.test:443" in hosts


def test_host_allowed_rejects_unknown_hosts(monkeypatch):
    monkeypatch.setattr(config, "ALLOWED_HOSTS", "example.com", raising=False)
    monkeypatch.setattr(config, "PUBLIC_ORIGIN", "", raising=False)
    assert host_validation._host_allowed("example.com") is True
    assert host_validation._host_allowed("evil.test") is False


def test_host_allowed_is_permissive_when_unconfigured(monkeypatch):
    monkeypatch.setattr(config, "ALLOWED_HOSTS", "", raising=False)
    monkeypatch.setattr(config, "PUBLIC_ORIGIN", "", raising=False)
    assert host_validation._host_allowed("anything.test") is True
