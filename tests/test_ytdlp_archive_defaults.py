from pathlib import Path

from video_orchestrator.backends.ytdlp import build_ytdlp_command
from video_orchestrator.backends.ytdlp_api import build_ytdlp_api_command
from video_orchestrator.backends.ytdlp_defaults import DEFAULT_SUBTITLE_LANGS


def test_ytdlp_api_command_archives_metadata_and_subtitles():
    command = build_ytdlp_api_command("https://www.youtube.com/watch?v=abc", Path("downloads"))

    assert "--no-playlist" in command
    assert "--write-info-json" in command
    assert "--write-description" in command
    assert "--write-subs" in command
    assert "--write-auto-subs" in command
    assert command[command.index("--sub-langs") + 1] == DEFAULT_SUBTITLE_LANGS
    assert "all,-live_chat" not in command


def test_ytdlp_binary_command_archives_metadata_and_subtitles():
    command = build_ytdlp_command("https://www.youtube.com/watch?v=abc", Path("downloads"))

    assert "--no-playlist" in command
    assert "--write-info-json" in command
    assert "--write-description" in command
    assert "--write-subs" in command
    assert "--write-auto-subs" in command
    assert command[command.index("--sub-langs") + 1] == DEFAULT_SUBTITLE_LANGS
    assert "all,-live_chat" not in command
