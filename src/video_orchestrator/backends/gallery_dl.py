from pathlib import Path


def build_gallery_dl_command(url: str, output_dir: Path) -> list[str]:
    return ["gallery-dl", "--directory", output_dir.as_posix(), url]
