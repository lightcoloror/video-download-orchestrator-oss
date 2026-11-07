from video_orchestrator.backends.base import BackendSpec
from video_orchestrator.runtime.subprocess_runner import command_exists


def test_backend_spec_declares_command():
    spec = BackendSpec(
        name="yt-dlp",
        command="yt-dlp",
        required=True,
        install_hint="Install yt-dlp with pipx or pip.",
        reference_url="https://github.com/yt-dlp/yt-dlp",
        recommended_for=["general video downloads"],
    )

    assert spec.name == "yt-dlp"
    assert spec.command == "yt-dlp"
    assert spec.required is True
    assert spec.install_hint.startswith("Install")
    assert spec.reference_url == "https://github.com/yt-dlp/yt-dlp"
    assert spec.recommended_for == ["general video downloads"]


def test_command_exists_detects_python():
    assert command_exists("python") or command_exists("python3")
