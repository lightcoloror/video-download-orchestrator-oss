import json
from pathlib import Path

import video_orchestrator.runtime.openclaw as openclaw_module
from video_orchestrator.runtime.openclaw import build_openclaw_link_plan


def test_openclaw_plan_writes_only_redacted_manifest(monkeypatch):
    written = []

    def fake_write_manifest(manifest, output_dir):
        written.append(manifest)
        return Path("redacted-manifest.json")

    monkeypatch.setattr(openclaw_module, "write_manifest", fake_write_manifest)
    payload = build_openclaw_link_plan("https://example.com/video.mp4?token=top-secret")

    serialized = json.dumps({"payload": payload, "written": written})
    assert payload["will_download"] is False
    assert written
    assert "top-secret" not in serialized
    assert "%3Credacted%3E" in serialized
