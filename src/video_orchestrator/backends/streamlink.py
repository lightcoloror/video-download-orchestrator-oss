from pathlib import Path


def build_streamlink_command(url: str, output_file: Path) -> list[str]:
    return ["streamlink", "--output", output_file.as_posix(), url, "best"]
