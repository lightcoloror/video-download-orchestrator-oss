from pathlib import Path

from video_orchestrator.backends.aria2 import build_aria2_command
from video_orchestrator.backends.curl import build_curl_command
from video_orchestrator.backends.feishu import build_feishu_command
from video_orchestrator.backends.n_m3u8dl_re import build_n_m3u8dl_re_command
from video_orchestrator.backends.python_http import build_python_http_command
from video_orchestrator.backends.spotdl import build_spotdl_command
from video_orchestrator.backends.streamlink import build_streamlink_command
from video_orchestrator.backends.tencent_meeting import build_tencent_meeting_command
from video_orchestrator.backends.vsd import build_vsd_capture_command
from video_orchestrator.backends.vsd import build_vsd_command
from video_orchestrator.backends.ytdlp_api import build_ytdlp_api_command
from video_orchestrator.backends.ytdlp import build_ytdlp_command


def test_ytdlp_command_uses_output_dir():
    cmd = build_ytdlp_command("https://example.com/video", Path("downloads"))

    assert cmd[:2] == ["yt-dlp", "--newline"]
    assert "-P" in cmd
    assert "downloads" in cmd
    assert cmd[-1] == "https://example.com/video"


def test_streamlink_command_records_best_stream():
    cmd = build_streamlink_command("https://www.twitch.tv/example", Path("downloads/out.ts"))

    assert cmd == ["streamlink", "--output", "downloads/out.ts", "https://www.twitch.tv/example", "best"]


def test_manifest_command_sets_save_dir():
    cmd = build_n_m3u8dl_re_command("https://example.com/master.m3u8", Path("downloads"))

    assert cmd[0] == "N_m3u8DL-RE"
    assert "https://example.com/master.m3u8" in cmd
    assert "--save-dir" in cmd
    assert "--auto-select" in cmd


def test_aria2_command_sets_output_dir():
    cmd = build_aria2_command("magnet:?xt=urn:btih:abc123", Path("downloads"))

    assert cmd == [
        "aria2c",
        "--continue=true",
        "--max-connection-per-server=8",
        "--split=8",
        "--dir",
        "downloads",
        "magnet:?xt=urn:btih:abc123",
    ]


def test_aria2_command_passes_request_headers():
    cmd = build_aria2_command(
        "https://example.com/video",
        Path("downloads"),
        {"Referer": "https://example.com/", "Cookie": "session=secret"},
    )

    assert "--header=Referer: https://example.com/" in cmd
    assert "--header=Cookie: session=secret" in cmd
    assert cmd[-1] == "https://example.com/video"


def test_curl_command_passes_request_headers_and_output_file():
    cmd = build_curl_command(
        "https://example.com/video",
        Path("downloads"),
        {"Referer": "https://example.com/", "Cookie": "session=secret"},
    )

    assert cmd[:2] == ["curl.exe", "-L"]
    assert "--output" in cmd
    assert "downloads/download.mp4" in cmd
    assert ["--header", "Referer: https://example.com/"] == cmd[cmd.index("--header") : cmd.index("--header") + 2]
    assert "Cookie: session=secret" in cmd
    assert cmd[-1] == "https://example.com/video"


def test_python_http_command_passes_request_headers_and_output_dir():
    cmd = build_python_http_command(
        "https://example.com/video.mp4",
        Path("downloads"),
        {"Referer": "https://example.com/", "Cookie": "session=secret"},
    )

    assert cmd[:3] == ["python", "-m", "video_orchestrator.backends.python_http"]
    assert "--output-dir" in cmd
    assert "downloads" in cmd
    assert ["--header", "Referer: https://example.com/"] == cmd[cmd.index("--header") : cmd.index("--header") + 2]
    assert "Cookie: session=secret" in cmd


def test_feishu_command_passes_request_headers_and_output_dir():
    cmd = build_feishu_command(
        "https://my.feishu.cn/wiki/example",
        Path("downloads"),
        {"Referer": "https://my.feishu.cn/", "Cookie": "session=secret"},
    )

    assert cmd[:3] == ["python", "-m", "video_orchestrator.backends.feishu"]
    assert "--output-dir" in cmd
    assert "downloads" in cmd
    assert ["--header", "Referer: https://my.feishu.cn/"] == cmd[cmd.index("--header") : cmd.index("--header") + 2]
    assert "Cookie: session=secret" in cmd


def test_vsd_command_passes_headers_threads_and_retries():
    cmd = build_vsd_command(
        "https://example.com/master.m3u8",
        Path("downloads"),
        {"Referer": "https://example.com/", "Cookie": "session=secret"},
    )

    assert cmd[:3] == ["vsd", "save", "https://example.com/master.m3u8"]
    assert "--directory" in cmd
    assert "downloads" in cmd
    assert "--threads" in cmd
    assert "--retries" in cmd
    assert "Cookie: session=secret" in cmd


def test_vsd_capture_command_can_use_proxy_and_cookies():
    cmd = build_vsd_capture_command(
        "https://example.com/watch",
        proxy="http://127.0.0.1:10808",
        headless=True,
        save_cookies=True,
        cookies_path=Path("cookies.txt"),
    )

    assert cmd == [
        "vsd",
        "capture",
        "https://example.com/watch",
        "--proxy",
        "http://127.0.0.1:10808",
        "--headless",
        "--save-cookies",
        "--cookies",
        "cookies.txt",
    ]


def test_spotdl_command_sets_output_dir():
    cmd = build_spotdl_command("https://open.spotify.com/track/example", Path("downloads"))

    assert cmd == ["spotdl", "download", "https://open.spotify.com/track/example", "--output", "downloads"]


def test_ytdlp_api_command_uses_python_module_entrypoint():
    cmd = build_ytdlp_api_command("https://example.com/video", Path("downloads"))

    assert cmd[:3] == ["python", "-m", "yt_dlp"]
    assert "-P" in cmd
    assert cmd[-1] == "https://example.com/video"


def test_tencent_meeting_command_uses_python_module_entrypoint():
    cmd = build_tencent_meeting_command("https://meeting.tencent.com/meeting-record/shares?id=share-id", Path("downloads"))

    assert cmd[:3] == ["python", "-m", "video_orchestrator.backends.tencent_meeting"]
    assert "--output-dir" in cmd
    assert "downloads" in cmd
    assert "https://meeting.tencent.com/meeting-record/shares?id=share-id" in cmd
