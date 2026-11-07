from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path


def build_curl_command(url: str, output_dir: Path, headers: Mapping[str, str] | None = None) -> list[str]:
    output_path = output_dir / "download.mp4"
    command = [
        "curl.exe",
        "-L",
        "--fail",
        "--retry",
        "3",
        "-C",
        "-",
        "--create-dirs",
        "--output",
        output_path.as_posix(),
    ]
    for name, value in (headers or {}).items():
        command.extend(["--header", f"{name}: {value}"])
    command.append(url)
    return command
