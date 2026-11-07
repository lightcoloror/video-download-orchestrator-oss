from pathlib import Path


def build_spotdl_command(url: str, output_dir: Path) -> list[str]:
    return ["spotdl", "download", url, "--output", output_dir.as_posix()]
