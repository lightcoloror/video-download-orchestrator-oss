from pathlib import Path

from video_orchestrator.backends.base import get_backend_registry
from video_orchestrator.backends.gallery_dl import build_gallery_dl_command
from video_orchestrator.external_uis import get_external_ui_status


def test_mediago_current_source_keeps_private_checkout_and_handoff_contract():
    spec = next(item for item in get_external_ui_status(check=False) if item.name == "mediago")
    assert spec.source_project == "https://github.com/mediago-dev/mediago"
    assert spec.integration_mode == "external_desktop_ui_http_handoff"
    assert "http_api_import" in spec.handoff_modes
    if spec.local_path:
        assert Path(spec.local_path).name == "mediago"


def test_gallery_source_is_codeberg_not_stale_or_incorrect_github_mirror():
    adapter = get_backend_registry()["gallery-dl"]
    assert adapter.spec.reference_url == "https://codeberg.org/mikf/gallery-dl"
    assert adapter.spec.command == "gallery-dl"
    assert adapter.spec.fallbacks == ["yt-dlp"]
    assert build_gallery_dl_command("https://example.test/gallery", Path("out")) == [
        "gallery-dl", "--directory", "out", "https://example.test/gallery"
    ]
