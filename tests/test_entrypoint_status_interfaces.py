import json

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from video_orchestrator.api import app as api_app
from video_orchestrator.cli import app as cli_app


def test_entrypoint_status_cli_keeps_planning_ready_when_listener_stopped(monkeypatch):
    monkeypatch.setattr(
        "video_orchestrator.runtime.entrypoint_status._tcp_listener_probe",
        lambda host, port: False,
    )
    result = CliRunner().invoke(cli_app, ["entrypoint-status", "--format", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "cli_planning_ready"
    assert payload["planning_cli"]["status"] == "ready"
    assert payload["http_listener"]["status"] == "stopped"
    assert payload["execute_gate"]["status"] == "blocked"
    assert payload["will_download"] is False


def test_entrypoint_status_http_is_planning_only_even_when_listener_healthy():
    response = TestClient(api_app).get("/entrypoints/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ready_for_planning"
    assert payload["http_listener"]["status"] == "healthy"
    assert payload["execute_gate"]["reason"] == "no_execution_request"
    assert payload["will_download"] is False
    assert payload["execute"] is False
