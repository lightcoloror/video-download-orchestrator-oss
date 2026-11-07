from pathlib import Path


def build_lux_command(url: str, output_dir: Path) -> list[str]:
    return ["lux", "--output-path", output_dir.as_posix(), url]
