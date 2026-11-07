from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import requests

from video_orchestrator.runtime.redaction import redact_headers
from video_orchestrator.runtime.redaction import redact_url


def build_python_http_command(url: str, output_dir: Path, headers: dict[str, str] | None = None) -> list[str]:
    command = [
        "python",
        "-m",
        "video_orchestrator.backends.python_http",
        url,
        "--output-dir",
        output_dir.as_posix(),
    ]
    for name, value in (headers or {}).items():
        command.extend(["--header", f"{name}: {value}"])
    return command


def download_http(
    url: str,
    output_dir: Path,
    *,
    headers: dict[str, str] | None = None,
    output_name: str = "download.mp4",
    timeout: tuple[int, int] = (30, 120),
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / output_name
    part_file = output_file.with_suffix(output_file.suffix + ".part")
    request_headers = dict(headers or {})
    existing = part_file.stat().st_size if part_file.exists() else 0
    if existing:
        request_headers["Range"] = f"bytes={existing}-"

    with requests.get(url, headers=request_headers, timeout=timeout, stream=True) as response:
        if response.status_code == 416 and part_file.exists():
            part_file.replace(output_file)
        else:
            response.raise_for_status()
            mode = "ab" if existing and response.status_code == 206 else "wb"
            with part_file.open(mode) as handle:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        handle.write(chunk)
            part_file.replace(output_file)

    return {
        "status": "finished",
        "backend": "python-http",
        "output_file": str(output_file),
        "bytes": output_file.stat().st_size,
        "redacted_url": redact_url(url),
        "request_headers": redact_headers(request_headers),
    }


def _parse_headers(values: list[str]) -> dict[str, str]:
    headers: dict[str, str] = {}
    for value in values:
        name, sep, header_value = value.partition(":")
        if not sep or not name.strip():
            raise ValueError(f"header must use 'Name: value' format: {value}")
        headers[name.strip()] = header_value.strip()
    return headers


def main() -> None:
    parser = argparse.ArgumentParser(description="Download a direct HTTP(S) URL with Python requests.")
    parser.add_argument("url")
    parser.add_argument("--output-dir", type=Path, default=Path("downloads"))
    parser.add_argument("--header", action="append", default=[])
    args = parser.parse_args()
    result = download_http(args.url, args.output_dir, headers=_parse_headers(args.header))
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
