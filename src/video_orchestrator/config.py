from __future__ import annotations

import json

import typer

from video_orchestrator.runtime.config import api_base_url
from video_orchestrator.runtime.config import browser_sniffer_config
from video_orchestrator.runtime.config import check_config
from video_orchestrator.runtime.config import config_path
from video_orchestrator.runtime.config import execution_config
from video_orchestrator.runtime.config import load_config
from video_orchestrator.runtime.config import sniffer_plan_endpoint
from video_orchestrator.runtime.config import web_config


app = typer.Typer(help="Print video-download-orchestrator runtime configuration.")


@app.command(name="show")
def show() -> None:
    payload = {
        "config_path": str(config_path()),
        "config": load_config(),
        "web": web_config(),
        "browser_sniffer": browser_sniffer_config(),
        "execution": execution_config(),
        "api_base_url": api_base_url(),
        "sniffer_plan_endpoint": sniffer_plan_endpoint(),
    }
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


@app.command(name="check")
def check() -> None:
    payload = check_config()
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    if payload["status"] != "ok":
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
