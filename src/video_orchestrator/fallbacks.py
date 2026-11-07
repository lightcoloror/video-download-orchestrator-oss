from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from video_orchestrator.backends.base import get_backend_registry
from video_orchestrator.router import classify_url


def fallback_backends_for(backend: str) -> list[str]:
    adapter = get_backend_registry().get(backend)
    return list(adapter.spec.fallbacks) if adapter else []


def fallback_plan_for_url(url: str, backend: str | None = None) -> dict[str, Any]:
    classification = classify_url(url)
    selected_backend = backend or classification.preferred_backend
    fallback_backends = fallback_backends_for(selected_backend)
    if selected_backend == "aria2":
        parsed = urlparse(url)
        if parsed.scheme.lower() in {"http", "https"} and not parsed.path.lower().endswith(".torrent"):
            fallback_backends = ["curl", "python-http", "yt-dlp"]
        else:
            fallback_backends = []
    return {
        "url": url,
        "classification": classification.kind,
        "route": classification.model_dump(),
        "selected_backend": selected_backend,
        "selection_source": "explicit" if backend else "url_classification",
        "fallback_backends": fallback_backends,
    }
