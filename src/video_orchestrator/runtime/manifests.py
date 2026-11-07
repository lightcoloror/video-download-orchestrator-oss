from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from video_orchestrator.runtime.redaction import redact_manifest


def write_manifest(manifest: dict[str, Any], output_dir: Path) -> Path:
    manifest_dir = output_dir / ".vdo" / "manifests"
    manifest_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backend = str(manifest.get("selected_backend", "unknown")).replace("/", "_")
    path = manifest_dir / f"{timestamp}-{backend}.json"
    path.write_text(
        json.dumps(redact_manifest(manifest), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return path
