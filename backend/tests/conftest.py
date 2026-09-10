import socket

import pytest

from backend.tests.support.contract import client, patch_upstream

__all__ = ["client", "patch_upstream"]


@pytest.fixture(autouse=True)
def example_host_dns(monkeypatch):
    """Mock service fixtures must not depend on the host's DNS or VPN fake IPs.

    Keep validation itself intact. Security tests can override getaddrinfo (or
    resolve_hostname) explicitly to exercise private addresses and rebinding.
    """
    original = socket.getaddrinfo
    fixture_hosts = {"example.com", "api.example.com", "account.r2.cloudflarestorage.com"}

    def getaddrinfo(host, *args, **kwargs):
        return original("93.184.216.34" if host in fixture_hosts else host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
