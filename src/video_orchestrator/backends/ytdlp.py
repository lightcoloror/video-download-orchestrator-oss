from pathlib import Path

from video_orchestrator.backends.ytdlp_defaults import DEFAULT_SUBTITLE_LANGS


def build_ytdlp_command(url: str, output_dir: Path) -> list[str]:
    return [
        "yt-dlp",
        "--newline",
        "--progress",
        "--no-color",
        "--no-playlist",
        "--write-info-json",
        "--write-description",
        "--write-subs",
        "--write-auto-subs",
        "--sub-langs",
        DEFAULT_SUBTITLE_LANGS,
        "-P",
        output_dir.as_posix(),
        url,
    ]
