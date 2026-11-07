from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path


def build_vsd_command(url: str, output_dir: Path, headers: Mapping[str, str] | None = None) -> list[str]:
    command = [
        "vsd",
        "save",
        url,
        "--directory",
        output_dir.as_posix(),
        "--threads",
        "8",
        "--retries",
        "10",
    ]
    for name, value in (headers or {}).items():
        command.extend(["--header", f"{name}: {value}"])
    return command


def build_vsd_capture_command(
    url: str,
    *,
    proxy: str | None = None,
    headless: bool = False,
    save_cookies: bool = False,
    cookies_path: Path | None = None,
) -> list[str]:
    command = ["vsd", "capture", url]
    if proxy:
        command.extend(["--proxy", proxy])
    if headless:
        command.append("--headless")
    if save_cookies:
        command.append("--save-cookies")
    if cookies_path:
        command.extend(["--cookies", cookies_path.as_posix()])
    return command
