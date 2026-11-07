from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from video_orchestrator.runtime.site_adapters import host_matches_suffixes


Resolver = Callable[[str, int | None], list[str]]


@dataclass(frozen=True)
class UnsafeUrlError(ValueError):
    reason: str
    details: dict[str, Any] | None = None


def validate_adapter_probe_url(url: str, *, allowed_host_suffixes: Sequence[str]) -> str:
    parsed, hostname = _parse_http_url(url)
    if parsed.username or parsed.password:
        raise UnsafeUrlError("userinfo_not_allowed")
    if hostname == "localhost" or hostname.endswith(".localhost") or hostname in {"metadata", "metadata.google.internal"}:
        raise UnsafeUrlError("dangerous_host")
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        raise UnsafeUrlError("ip_literal_not_allowed")
    port = _port(parsed)
    if port is not None and port not in {80, 443}:
        raise UnsafeUrlError("unexpected_port")
    if not host_matches_suffixes(hostname, tuple(allowed_host_suffixes)):
        raise UnsafeUrlError("host_not_allowed")
    return hostname


def validate_loopback_proxy_url(url: str) -> None:
    parsed, hostname = _parse_http_url(url)
    if parsed.username or parsed.password:
        raise UnsafeUrlError("proxy_credentials_not_allowed")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError as exc:
        raise UnsafeUrlError("proxy_must_use_loopback_ip") from exc
    if not address.is_loopback or _port(parsed) is None:
        raise UnsafeUrlError("proxy_must_use_loopback_ip")


def validate_public_http_url(url: str, *, resolver: Resolver | None = None) -> None:
    parsed, hostname = _parse_http_url(url)
    if parsed.username or parsed.password:
        raise UnsafeUrlError("userinfo_not_allowed")
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise UnsafeUrlError("localhost")
    addresses = (resolver or _resolve_all)(hostname, _port(parsed))
    if not addresses:
        raise UnsafeUrlError("host_unresolved")
    for raw_address in addresses:
        try:
            address = ipaddress.ip_address(raw_address)
        except ValueError as exc:
            raise UnsafeUrlError("host_unresolved") from exc
        if not address.is_global:
            raise UnsafeUrlError("non_public_address")


def _parse_http_url(url: str):
    if "\r" in url or "\n" in url:
        raise UnsafeUrlError("invalid_url")
    try:
        parsed = urlsplit(url)
    except ValueError as exc:
        raise UnsafeUrlError("invalid_url") from exc
    if parsed.scheme.lower() not in {"http", "https"}:
        raise UnsafeUrlError("unsupported_scheme")
    if not parsed.hostname:
        raise UnsafeUrlError("missing_host")
    try:
        hostname = parsed.hostname.encode("idna").decode("ascii").lower().rstrip(".")
    except UnicodeError as exc:
        raise UnsafeUrlError("invalid_host") from exc
    _port(parsed)
    return parsed, hostname


def _port(parsed) -> int | None:
    try:
        return parsed.port
    except ValueError as exc:
        raise UnsafeUrlError("invalid_port") from exc


def _resolve_all(hostname: str, port: int | None) -> list[str]:
    try:
        infos = socket.getaddrinfo(hostname, port or 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise UnsafeUrlError("host_unresolved") from exc
    return sorted({info[4][0] for info in infos})