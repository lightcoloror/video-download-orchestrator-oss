from __future__ import annotations

import uvicorn

from video_orchestrator.api import app
from video_orchestrator.runtime.config import web_host
from video_orchestrator.runtime.config import web_port


def main() -> None:
    uvicorn.run(app, host=web_host(), port=web_port())


if __name__ == "__main__":
    main()
