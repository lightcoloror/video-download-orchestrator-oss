import pytest

from video_orchestrator.runtime.url_safety import UnsafeUrlError
from video_orchestrator.runtime.url_safety import validate_adapter_probe_url
from video_orchestrator.runtime.url_safety import validate_loopback_proxy_url
from video_orchestrator.runtime.url_safety import validate_public_http_url


@pytest.mark.parametrize(
    ("url", "addresses", "reason"),
    [
        ("ftp://example.com/file", ["8.8.8.8"], "unsupported_scheme"),
        ("http://localhost/video", ["127.0.0.1"], "localhost"),
        ("http://private.example/video", ["192.168.1.2"], "non_public_address"),
        ("http://loopback.example/video", ["::1"], "non_public_address"),
        ("http://metadata.example/video", ["169.254.169.254"], "non_public_address"),
    ],
)
def test_validate_public_http_url_blocks_non_public_targets(url, addresses, reason):
    with pytest.raises(UnsafeUrlError, match=reason):
        validate_public_http_url(url, resolver=lambda _host, _port: addresses)


def test_validate_public_http_url_allows_global_address():
    validate_public_http_url("https://public.example/video", resolver=lambda _host, _port: ["8.8.8.8"])


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("https://evil-bilibili.com/video/BV1", "host_not_allowed"),
        ("https://user:pass@www.bilibili.com/video/BV1", "userinfo_not_allowed"),
        ("https://127.0.0.1/video/BV1", "ip_literal_not_allowed"),
        ("https://www.bilibili.com:8443/video/BV1", "unexpected_port"),
        ("https://localhost/video/BV1", "dangerous_host"),
    ],
)
def test_validate_adapter_probe_url_blocks_unsafe_targets(url, reason):
    with pytest.raises(UnsafeUrlError, match=reason):
        validate_adapter_probe_url(url, allowed_host_suffixes=("bilibili.com", "b23.tv"))


def test_validate_adapter_probe_url_uses_label_boundaries():
    assert validate_adapter_probe_url(
        "https://www.bilibili.com/video/BV1", allowed_host_suffixes=("bilibili.com", "b23.tv")
    ) == "www.bilibili.com"


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("http://192.168.1.2:1", "proxy_must_use_loopback_ip"),
        ("socks5://127.0.0.1:1", "unsupported_scheme"),
        ("http://user:pass@127.0.0.1:1", "proxy_credentials_not_allowed"),
        ("http://127.0.0.1", "proxy_must_use_loopback_ip"),
    ],
)
def test_validate_loopback_proxy_url_blocks_invalid_proxy(url, reason):
    with pytest.raises(UnsafeUrlError, match=reason):
        validate_loopback_proxy_url(url)


def test_validate_loopback_proxy_url_allows_configured_proxy():
    validate_loopback_proxy_url("http://127.0.0.1:1")