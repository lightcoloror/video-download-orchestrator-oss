from pathlib import Path


def build_n_m3u8dl_re_command(url: str, output_dir: Path) -> list[str]:
    return ["N_m3u8DL-RE", url, "--save-dir", output_dir.as_posix(), "--auto-select"]
