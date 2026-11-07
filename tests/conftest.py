from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def synthetic_media_space_preflight(monkeypatch, tmp_path_factory):
    """Keep existing execution fixtures independent from the host disk state."""
    import video_orchestrator.runtime.execution as execution

    monkeypatch.setenv("TEST_TMP_ROOT", str(tmp_path_factory.getbasetemp()))
    monkeypatch.setattr(
        execution,
        "media_operation_preflight",
        lambda output_dir, **kwargs: {
            "contract": "vdo-artifact-lifecycle-adapter.v1",
            "operation": kwargs.get("operation"),
            "status": "ok",
            "enforced": False,
            "synthetic_test_scope": True,
            "filesystem_mutation_performed": False,
        },
    )


"""Keep the portable suite offline while allowing its loopback mock servers."""

import socket

import pytest


@pytest.fixture(autouse=True)
def offline_connections(monkeypatch):
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NO_PROXY", "*")
    original = socket.create_connection
    original_connect = socket.socket.connect
    original_getaddrinfo = socket.getaddrinfo

    def getaddrinfo(host, *args, **kwargs):
        if host not in {"127.0.0.1", "::1", "localhost", None}:
            raise OSError("External DNS is disabled in the offline test suite")
        return original_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)

    def connect(address, *args, **kwargs):
        if address[0] not in {"127.0.0.1", "::1", "localhost"}:
            raise OSError("External network is disabled in the offline test suite")
        return original(address, *args, **kwargs)

    monkeypatch.setattr(socket, "create_connection", connect)

    def socket_connect(sock, address):
        if not isinstance(address, tuple) or address[0] not in {"127.0.0.1", "::1", "localhost"}:
            raise OSError("External network is disabled in the offline test suite")
        return original_connect(sock, address)

    monkeypatch.setattr(socket.socket, "connect", socket_connect)


@pytest.fixture(autouse=True)
def offline_http_requests(monkeypatch):
    """Reject external targets even if a configured loopback proxy is present."""
    from urllib.parse import urlsplit
    import requests

    original = requests.sessions.Session.request

    def request(session, method, url, *args, **kwargs):
        if urlsplit(url).hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise requests.ConnectionError("External HTTP is disabled in the offline test suite")
        return original(session, method, url, *args, **kwargs)

    monkeypatch.setattr(requests.sessions.Session, "request", request)
