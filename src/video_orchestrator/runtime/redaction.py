from __future__ import annotations

from copy import deepcopy
from typing import Any
from urllib.parse import parse_qsl
from urllib.parse import urlencode
from urllib.parse import urlsplit
from urllib.parse import urlunsplit


SENSITIVE_HEADER_NAMES = {
    "authorization",
    "cookie",
    "set-cookie",
    "x-csrf-token",
    "x-xsrf-token",
}
SENSITIVE_NAME_PARTS = ("token", "session", "secret", "credential", "sign")
SENSITIVE_QUERY_NAMES = {
    "access_token",
    "auth",
    "authorization",
    "credential",
    "key",
    "secret",
    "session",
    "sign",
    "signature",
    "token",
}


def redact_header_value(name: str, value: str) -> str:
    lowered = name.lower()
    if lowered in SENSITIVE_HEADER_NAMES or any(part in lowered for part in SENSITIVE_NAME_PARTS):
        return "<redacted>"
    return value


def redact_headers(headers: dict[str, str]) -> dict[str, str]:
    return {name: redact_header_value(name, value) for name, value in headers.items()}


def redact_ephemeral_headers(headers: dict[str, str]) -> dict[str, str]:
    redacted: dict[str, str] = {}
    for name, value in headers.items():
        safe_value = redact_header_value(name, value)
        if name.lower() in {"referer", "origin"} and safe_value != "<redacted>":
            safe_value = redact_ephemeral_url(safe_value)
        redacted[name] = safe_value
    return redacted


def redact_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return value
    if not parsed.scheme or not parsed.netloc or not parsed.query:
        return value

    redacted_pairs = []
    changed = False
    for name, query_value in parse_qsl(parsed.query, keep_blank_values=True):
        lowered = name.lower()
        if lowered in SENSITIVE_QUERY_NAMES or any(part in lowered for part in SENSITIVE_NAME_PARTS):
            redacted_pairs.append((name, "<redacted>"))
            changed = True
        else:
            redacted_pairs.append((name, query_value))
    if not changed:
        return value
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(redacted_pairs), parsed.fragment))


def redact_ephemeral_url(value: str) -> str:
    """Remove the entire replay-bearing query from a short-lived media URL."""

    try:
        parsed = urlsplit(value)
    except ValueError:
        return value
    if not parsed.scheme or not parsed.netloc:
        return value
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def redact_command(command: list[str], *, ephemeral_urls: bool = False) -> list[str]:
    redacted = []
    redact_next_header = False
    redact_next_value = False
    for token in command:
        if redact_next_value:
            redacted.append("<redacted>")
            redact_next_value = False
            continue
        if redact_next_header:
            name, sep, value = token.partition(":")
            safe_value = (
                redact_ephemeral_headers({name.strip(): value.strip()})[name.strip()]
                if ephemeral_urls
                else redact_header_value(name.strip(), value.strip())
            )
            redacted.append(f"{name}:{sep and ' '}{safe_value}")
            redact_next_header = False
            continue
        lowered = token.lower()
        if lowered.startswith("--header="):
            header = token[len("--header=") :]
            name, sep, value = header.partition(":")
            safe_value = (
                redact_ephemeral_headers({name.strip(): value.strip()})[name.strip()]
                if ephemeral_urls
                else redact_header_value(name.strip(), value.strip())
            )
            redacted.append(f"--header={name}:{sep and ' '}{safe_value}")
        elif lowered.startswith("--cookies="):
            redacted.append("--cookies=<redacted>")
        elif lowered in {"--header", "-h"}:
            redacted.append(token)
            redact_next_header = True
        elif lowered == "--cookies":
            redacted.append(token)
            redact_next_value = True
        else:
            redacted.append(redact_ephemeral_url(token) if ephemeral_urls else redact_url(token))
    return redacted


def redact_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    redacted = deepcopy(manifest)
    sniffed_media = redacted.get("sniffed_media") if isinstance(redacted.get("sniffed_media"), dict) else {}
    ephemeral_capture = (
        redacted.get("source") == "browser_sniffer"
        or bool(redacted.get("source_capture_id"))
        or bool(sniffed_media.get("capture_id"))
        or bool(sniffed_media.get("capture_session_id"))
    )
    if isinstance(redacted.get("url"), str):
        redacted["url"] = redact_ephemeral_url(redacted["url"]) if ephemeral_capture else redact_url(redacted["url"])
    if isinstance(redacted.get("source_page_url"), str):
        redacted["source_page_url"] = redact_url(redacted["source_page_url"])
    if isinstance(redacted.get("page_url"), str):
        redacted["page_url"] = redact_url(redacted["page_url"])
    if isinstance(redacted.get("route"), dict) and isinstance(redacted["route"].get("url"), str):
        redacted["route"]["url"] = (
            redact_ephemeral_url(redacted["route"]["url"])
            if ephemeral_capture
            else redact_url(redacted["route"]["url"])
        )
    if isinstance(redacted.get("command"), list):
        redacted["command"] = redact_command(redacted["command"], ephemeral_urls=ephemeral_capture)
    if isinstance(redacted.get("request_headers"), dict):
        redacted["request_headers"] = (
            redact_ephemeral_headers(redacted["request_headers"])
            if ephemeral_capture
            else redact_headers(redacted["request_headers"])
        )
    if isinstance(redacted.get("sniffed_media"), dict) and isinstance(redacted["sniffed_media"].get("headers"), dict):
        redacted["sniffed_media"]["headers"] = (
            redact_ephemeral_headers(redacted["sniffed_media"]["headers"])
            if ephemeral_capture
            else redact_headers(redacted["sniffed_media"]["headers"])
        )
    if isinstance(redacted.get("sniffed_media"), dict) and isinstance(redacted["sniffed_media"].get("url"), str):
        redacted["sniffed_media"]["url"] = (
            redact_ephemeral_url(redacted["sniffed_media"]["url"])
            if ephemeral_capture
            else redact_url(redacted["sniffed_media"]["url"])
        )
    if isinstance(redacted.get("sniffed_media"), dict) and isinstance(redacted["sniffed_media"].get("request_headers"), dict):
        redacted["sniffed_media"]["request_headers"] = (
            redact_ephemeral_headers(redacted["sniffed_media"]["request_headers"])
            if ephemeral_capture
            else redact_headers(redacted["sniffed_media"]["request_headers"])
        )
    if isinstance(redacted.get("sniffed_media"), dict) and isinstance(redacted["sniffed_media"].get("response_headers"), dict):
        redacted["sniffed_media"]["response_headers"] = redact_headers(redacted["sniffed_media"]["response_headers"])
    return redacted


def redact_sniffed_media(sniffed_media: dict[str, Any]) -> dict[str, Any]:
    """Redact a capture fragment without requiring its parent manifest."""

    redacted = redact_manifest(
        {
            "source": "browser_sniffer",
            "sniffed_media": sniffed_media,
        }
    )
    return redacted["sniffed_media"]


def redact_attempts(attempts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    redacted = deepcopy(attempts)
    for attempt in redacted:
        if isinstance(attempt.get("command"), list):
            attempt["command"] = redact_command(attempt["command"])
    return redacted
