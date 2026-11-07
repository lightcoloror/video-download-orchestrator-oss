from __future__ import annotations

import argparse
import html
import json
import re
from pathlib import Path
from typing import Any

import requests

from video_orchestrator.backends.python_http import download_http
from video_orchestrator.runtime.redaction import redact_headers
from video_orchestrator.runtime.redaction import redact_url


DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/148.0.0.0 Safari/537.36 Edg/148.0.0.0"
)


def build_feishu_command(url: str, output_dir: Path, headers: dict[str, str] | None = None) -> list[str]:
    command = [
        "python",
        "-m",
        "video_orchestrator.backends.feishu",
        url,
        "--output-dir",
        output_dir.as_posix(),
    ]
    for name, value in (headers or {}).items():
        command.extend(["--header", f"{name}: {value}"])
    return command


def download_feishu_page(
    page_url: str,
    output_dir: Path,
    *,
    headers: dict[str, str] | None = None,
    timeout: tuple[int, int] = (30, 120),
) -> dict[str, Any]:
    request_headers = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Referer": "https://my.feishu.cn/",
        **dict(headers or {}),
    }
    response = requests.get(page_url, headers=request_headers, timeout=timeout[0])
    response.raise_for_status()

    media_url = extract_feishu_video_url(response.text)
    download_headers = dict(request_headers)
    download_headers.setdefault("Referer", "https://my.feishu.cn/")
    result = download_http(media_url, output_dir, headers=download_headers, timeout=timeout)
    result.update(
        {
            "backend": "feishu",
            "page_url": page_url,
            "redacted_media_url": redact_url(media_url),
            "request_headers": redact_headers(download_headers),
        }
    )
    return result


def extract_feishu_video_url(page_text: str) -> str:
    candidates = extract_feishu_video_urls(page_text)
    if not candidates:
        raise ValueError(
            "Feishu page did not expose a drive video stream URL in fetched HTML. "
            "Use download-page with browser sniffing, or pass valid browser cookies/headers to the feishu backend."
        )
    return candidates[0]


def extract_feishu_video_urls(page_text: str) -> list[str]:
    normalized = _normalize_embedded_text(page_text)
    pattern = re.compile(r"https://internal-api-drive-stream\.feishu\.cn/[^\"'<>\\\s]+/download/video/[^\"'<>\\\s]+")
    seen: set[str] = set()
    urls: list[str] = []
    for match in pattern.finditer(normalized):
        url = match.group(0).rstrip("),.;")
        if url not in seen:
            seen.add(url)
            urls.append(url)
    urls.sort(key=_feishu_quality_rank)
    return urls


def _normalize_embedded_text(value: str) -> str:
    text = html.unescape(value)
    for _ in range(2):
        text = text.replace("\\u002F", "/").replace("\\/", "/").replace("\\u003D", "=").replace("\\u0026", "&")
        text = text.replace('\\"', '"')
    return text


def _feishu_quality_rank(url: str) -> int:
    if "quality=1080p" in url:
        return 0
    if "quality=720p" in url:
        return 1
    if "quality=480p" in url:
        return 2
    return 3


def _parse_headers(values: list[str]) -> dict[str, str]:
    headers: dict[str, str] = {}
    for value in values:
        name, sep, header_value = value.partition(":")
        if not sep or not name.strip():
            raise ValueError(f"header must use 'Name: value' format: {value}")
        headers[name.strip()] = header_value.strip()
    return headers


def main() -> None:
    parser = argparse.ArgumentParser(description="Download a Feishu page video by extracting its drive stream URL.")
    parser.add_argument("url")
    parser.add_argument("--output-dir", type=Path, default=Path("downloads"))
    parser.add_argument("--header", action="append", default=[])
    args = parser.parse_args()
    result = download_feishu_page(args.url, args.output_dir, headers=_parse_headers(args.header))
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
