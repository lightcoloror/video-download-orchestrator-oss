from video_orchestrator.runtime.redaction import redact_command
from video_orchestrator.runtime.redaction import redact_manifest
from video_orchestrator.runtime.redaction import redact_url


def test_redacts_sensitive_header_command_values():
    command = [
        "aria2c",
        "--header=Referer: https://example.com/",
        "--header=Cookie: session=secret",
        "--header=Authorization: Bearer secret",
        "https://example.com/video",
    ]

    assert redact_command(command) == [
        "aria2c",
        "--header=Referer: https://example.com/",
        "--header=Cookie: <redacted>",
        "--header=Authorization: <redacted>",
        "https://example.com/video",
    ]


def test_redacts_curl_style_separate_header_values():
    command = [
        "curl.exe",
        "--header",
        "Cookie: session=secret",
        "--header",
        "Referer: https://example.com/",
        "https://example.com/video",
    ]

    assert redact_command(command) == [
        "curl.exe",
        "--header",
        "Cookie: <redacted>",
        "--header",
        "Referer: https://example.com/",
        "https://example.com/video",
    ]


def test_redacts_ytdlp_cookie_file_command_values():
    command = [
        "yt-dlp",
        "--cookies",
        "fixtures/test-cookies.txt",
        "--cookies=another-cookies.txt",
        "https://www.youtube.com/watch?v=abc",
    ]

    assert redact_command(command) == [
        "yt-dlp",
        "--cookies",
        "<redacted>",
        "--cookies=<redacted>",
        "https://www.youtube.com/watch?v=abc",
    ]


def test_redacts_manifest_request_headers():
    manifest = {
        "command": ["aria2c", "--header=Cookie: session=secret", "https://example.com/video"],
        "request_headers": {"Cookie": "session=secret", "Referer": "https://example.com/"},
        "sniffed_media": {
            "headers": {"Cookie": "session=secret"},
            "request_headers": {"Cookie": "session=secret"},
            "response_headers": {"Set-Cookie": "session=secret"},
        },
    }

    redacted = redact_manifest(manifest)

    assert redacted["command"][1] == "--header=Cookie: <redacted>"
    assert redacted["request_headers"]["Cookie"] == "<redacted>"
    assert redacted["request_headers"]["Referer"] == "https://example.com/"
    assert redacted["sniffed_media"]["headers"]["Cookie"] == "<redacted>"
    assert redacted["sniffed_media"]["request_headers"]["Cookie"] == "<redacted>"
    assert redacted["sniffed_media"]["response_headers"]["Set-Cookie"] == "<redacted>"


def test_redacts_sensitive_url_query_values_in_commands_and_manifests():
    url = "https://example.com/video.mp4?token=secret&x=1"
    command = ["curl.exe", url]
    manifest = {
        "url": url,
        "route": {"url": url},
        "command": command,
        "sniffed_media": {"url": url},
    }

    assert redact_url(url) == "https://example.com/video.mp4?token=%3Credacted%3E&x=1"
    assert redact_command(command)[1] == "https://example.com/video.mp4?token=%3Credacted%3E&x=1"
    redacted = redact_manifest(manifest)
    assert redacted["url"] == "https://example.com/video.mp4?token=%3Credacted%3E&x=1"
    assert redacted["route"]["url"] == "https://example.com/video.mp4?token=%3Credacted%3E&x=1"
    assert redacted["sniffed_media"]["url"] == "https://example.com/video.mp4?token=%3Credacted%3E&x=1"


def test_redacts_signature_name_variants_in_queries_and_headers():
    url = "https://example.com/video.mp4?x-signature=secret&plain=ok"
    manifest = {
        "url": url,
        "request_headers": {"X-Signature": "header-secret"},
    }

    redacted = redact_manifest(manifest)

    assert redacted["url"] == "https://example.com/video.mp4?x-signature=%3Credacted%3E&plain=ok"
    assert redacted["request_headers"]["X-Signature"] == "<redacted>"
