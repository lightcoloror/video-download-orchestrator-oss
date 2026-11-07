from pathlib import Path
from collections.abc import Mapping


def build_aria2_command(url: str, output_dir: Path, headers: Mapping[str, str] | None = None) -> list[str]:
    command = ["aria2c", "--continue=true", "--max-connection-per-server=8", "--split=8", "--dir", output_dir.as_posix()]
    for name, value in (headers or {}).items():
        command.append(f"--header={name}: {value}")
    command.append(url)
    return command
