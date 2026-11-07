import json
from pathlib import Path
from datetime import UTC, datetime, timedelta

from typer.testing import CliRunner

import video_orchestrator.cli as cli_module
import video_orchestrator.runtime.page_link as page_link_module
from video_orchestrator.cli import app
from video_orchestrator.runtime.config import api_base_url
from video_orchestrator.runtime.subprocess_runner import CommandResult
from video_orchestrator.runtime.task_store import TaskStore


runner = CliRunner()


def test_route_outputs_json():
    result = runner.invoke(app, ["route", "https://example.com/live/master.m3u8", "--format", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["route"]["kind"] == "manifest"
    assert payload["route"]["preferred_backend"] == "n_m3u8dl_re"
    assert payload["site_adapter"]["matched"] is False
    assert payload["will_download"] is False


def test_openclaw_plan_outputs_planning_only_payload(tmp_path):
    raw_path = tmp_path / "telegram.txt"
    raw_path.write_text("看看这个 https://example.com/video.mp4?token=secret", encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "openclaw-plan",
            str(raw_path),
            "--output-dir",
            str(tmp_path / "openclaw"),
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["contract"] == "openclaw_telegram_link_plan_v1"
    assert payload["will_download"] is False
    assert payload["items"][0]["route"]["kind"] == "direct-transfer"
    assert payload["items"][0]["url"].endswith("token=%3Credacted%3E")
    assert payload["items"][0]["manifest_path"]


def test_openclaw_execute_requires_confirm_from_allowed_actor(tmp_path):
    raw_path = tmp_path / "telegram.txt"
    raw_path.write_text("看看这个 https://example.com/video.mp4", encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "openclaw-execute",
            str(raw_path),
            "--actor-id",
            "42",
            "--allowed-actor-id",
            "42",
            "--output-dir",
            str(tmp_path / "openclaw"),
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["contract"] == "openclaw_telegram_execute_v1"
    assert payload["status"] == "confirmation_required"
    assert payload["will_download"] is False
    assert payload["actor"]["authorized"] is True


def test_task_vkp_handoff_outputs_planning_payload(tmp_path):
    db_path = tmp_path / "tasks.sqlite"
    media = tmp_path / "downloads" / "video.mp4"
    media.parent.mkdir(parents=True)
    media.write_bytes(b"video")
    review_path = tmp_path / "downloads" / ".vdo" / "reports" / "1" / "review-checklist.json"
    review_path.parent.mkdir(parents=True)
    review_path.write_text(
        json.dumps({"checks": [{"name": "output_files", "status": "ok"}], "backend_result": {"output_file": str(media), "size_matches_page": True}}),
        encoding="utf-8",
    )
    store = TaskStore(db_path)
    task_id = store.create_task("https://example.com/video.mp4", "curl")
    store.mark_finished(task_id, 0)
    store.add_artifact(task_id, "review_checklist", review_path)

    result = runner.invoke(
        app,
        [
            "task",
            "vkp-handoff",
            str(task_id),
            "--db-path",
            str(db_path),
            "--output-dir",
            str(tmp_path / "handoffs"),
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["contract"] == "vdo_vkp_handoff_v1"
    assert payload["will_download"] is False
    assert payload["execute"] is False
    assert payload["media"]["media_exists"] is True
    assert payload["human_summary"]["headline"] == "VKP 交接包已准备好。"
    assert payload["vkp_ingest_suggestion"]["will_execute"] is False


def test_route_includes_site_adapter_for_tencent_meeting():
    result = runner.invoke(app, ["route", "https://meeting.tencent.com/meeting-record/shares?id=share-id", "--format", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["route"]["kind"] == "tencent-meeting"
    assert payload["site_adapter"]["matched"] is True
    assert payload["site_adapter"]["name"] == "tencent-meeting"


def test_route_reports_missing_backend_guidance(monkeypatch):
    def fake_command_exists(command):
        return command == "yt-dlp"

    monkeypatch.setattr(cli_module, "command_exists", fake_command_exists)
    result = runner.invoke(app, ["route", "https://example.com/live/master.m3u8", "--format", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    guidance = payload["backend_guidance"]
    assert guidance["selected_backend"] == "n_m3u8dl_re"
    assert guidance["selected_available"] is False
    assert guidance["next_available_backend"] == "yt-dlp"
    assert guidance["fallback_backends"] == ["vsd", "yt-dlp-api", "yt-dlp"]
    assert "N_m3u8DL-RE" in guidance["install_hint"]


def test_inspect_uses_ytdlp_api_without_download(monkeypatch):
    def fake_extract(url, *, flat=True):
        return {"available": True, "status": "ok", "title": "Example", "playlist_count": None, "format_summary": []}

    monkeypatch.setattr(cli_module, "extract_ytdlp_metadata", fake_extract)
    result = runner.invoke(app, ["inspect", "https://example.com/video", "--format", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["will_download"] is False
    assert payload["metadata"]["title"] == "Example"


def test_doctor_outputs_backend_checks():
    result = runner.invoke(app, ["doctor", "--format", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["service"] == "video-download-orchestrator"
    assert payload["backends"]


def test_doctor_includes_backend_version_when_available(monkeypatch):
    def fake_command_exists(command):
        return command == "yt-dlp"

    def fake_run_command(args, **kwargs):
        return CommandResult(args=args, returncode=0, stdout="2026.01.01\n", stderr="")

    monkeypatch.setattr(cli_module, "command_exists", fake_command_exists)
    monkeypatch.setattr(cli_module, "run_command", fake_run_command)
    result = runner.invoke(app, ["doctor", "--format", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    ytdlp = next(backend for backend in payload["backends"] if backend["name"] == "yt-dlp")
    assert ytdlp["version"] == "2026.01.01"
    assert ytdlp["version_command"] == ["yt-dlp", "--version"]


def test_sniff_diagnostics_lists_redacted_records(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    vdo_dir = tmp_path / ".vdo"
    vdo_dir.mkdir()
    (vdo_dir / "sniffed-media.jsonl").write_text(
        json.dumps({"url": "https://example.com/video.mp4?token=%3Credacted%3E", "request_headers": {"Cookie": "<redacted>"}}) + "\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["sniff-diagnostics", "--limit", "10", "--format", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"
    assert payload["captures"][0]["request_headers"]["Cookie"] == "<redacted>"
    assert "secret" not in result.stdout


def test_sniff_cleanup_prunes_old_records(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    vdo_dir = tmp_path / ".vdo"
    vdo_dir.mkdir()
    old_time = (datetime.now(UTC) - timedelta(days=10)).isoformat()
    new_time = datetime.now(UTC).isoformat()
    path = vdo_dir / "sniffed-media.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps({"capture_id": "old", "captured_at": old_time}),
                json.dumps({"capture_id": "new", "captured_at": new_time}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    dry_run = runner.invoke(app, ["sniff-cleanup", "--older-than-days", "7", "--dry-run", "--format", "json"])
    live_run = runner.invoke(app, ["sniff-cleanup", "--older-than-days", "7", "--format", "json"])

    assert dry_run.exit_code == 0
    assert json.loads(dry_run.stdout)["removed_count"] == 1
    assert live_run.exit_code == 0
    payload = json.loads(live_run.stdout)
    assert payload["removed_count"] == 1
    remaining = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert [record["capture_id"] for record in remaining] == ["new"]


def test_backends_outputs_known_backends():
    result = runner.invoke(app, ["backends", "--format", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert {backend["name"] for backend in payload["backends"]} >= {
        "yt-dlp",
        "yt-dlp-api",
        "streamlink",
        "gallery-dl",
        "aria2",
        "curl",
        "python-http",
        "feishu",
        "vsd",
        "spotdl",
        "tencent-meeting",
    }


def test_uis_outputs_external_ui_integrations():
    result = runner.invoke(app, ["uis", "--format", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["will_download"] is False
    assert {ui["name"] for ui in payload["uis"]} >= {"metube", "hls-downloader", "motrix-next"}


def test_uis_check_outputs_structured_external_ui_status():
    result = runner.invoke(app, ["uis", "--check", "--format", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    metube = next(ui for ui in payload["uis"] if ui["name"] == "metube")
    assert "status" in metube
    assert "handoff_modes" in metube
    assert "routes" in metube
    assert payload["will_download"] is False


def test_page_link_doctor_reports_api_extension_and_backends(monkeypatch):
    def fake_api_get_json(api_base_url, path, params):
        if path == "/health":
            return {"service": "video-download-orchestrator", "status": "ok"}
        if path == "/sniffed-media/latest":
            return {"status": "not_found", "capture": None}
        raise AssertionError(path)

    def fake_command_exists(command):
        return command in {"yt-dlp", "curl.exe", "python"}

    monkeypatch.setattr(cli_module, "_api_get_json", fake_api_get_json)
    monkeypatch.setattr(cli_module, "command_exists", fake_command_exists)
    result = runner.invoke(app, ["page-link-doctor", "--format", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["service"] == "video-download-orchestrator"
    assert payload["status"] == "ok"
    checks = {check["name"]: check for check in payload["checks"]}
    assert checks["api"]["status"] == "ok"
    assert checks["browser_sniffer_config"]["status"] == "ok"
    assert checks["extension_recent_submit"]["status"] == "unknown"
    assert checks["required_backend"]["status"] == "ok"
    assert checks["direct_transfer_fallback"]["status"] == "ok"


def test_operator_doctor_reports_operational_status(monkeypatch, tmp_path):
    def fake_api_get_json(api_base_url, path, params):
        if path == "/health":
            return {"status": "ok"}
        if path == "/sniffed-media/latest":
            return {"status": "ok", "capture": {"capture_id": "cap-1"}, "capture_count": 1, "rejection_count": 0}
        raise AssertionError(path)

    monkeypatch.setattr(cli_module, "_api_get_json", fake_api_get_json)
    monkeypatch.setattr(cli_module, "command_exists", lambda command: command in {"yt-dlp", "curl.exe", "python"})
    store = TaskStore(tmp_path / "tasks.sqlite")
    task_id = store.create_task("https://example.com/watch", "browser-sniffer")
    store.mark_retryable(task_id)

    result = runner.invoke(app, ["operator-doctor", "--db-path", str(tmp_path / "tasks.sqlite"), "--format", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["service"] == "video-download-orchestrator"
    assert payload["status"] == "ok"
    assert payload["retryable_task_count"] == 1
    assert payload["latest_capture_status"] == "ok"
    assert "scripts\\operator-smoke.ps1" in payload["smoke_script"]


def test_sniff_outputs_vsd_capture_command():
    result = runner.invoke(
        app,
        [
            "sniff",
            "https://example.com/watch",
            "--proxy",
            "http://127.0.0.1:10808",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["will_download"] is False
    assert payload["capture_backend"] == "vsd"
    assert payload["command"] == [
        "vsd",
        "capture",
        "https://example.com/watch",
        "--proxy",
        "http://127.0.0.1:10808",
    ]


def test_handoff_cli_is_planning_only():
    result = runner.invoke(app, ["handoff", "https://example.com/video.mp4", "--format", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["will_download"] is False
    assert payload["recommended_ui"]["name"] == "motrix-next"


def test_task_handoff_for_retryable_task_includes_resume_command(tmp_path):
    db_path = tmp_path / "tasks.sqlite"
    store = TaskStore(db_path)
    task_id = store.create_task("https://example.com/watch", "browser-sniffer")
    store.mark_retryable(task_id)

    result = runner.invoke(app, ["task", "handoff", str(task_id), "--db-path", str(db_path), "--format", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["task"]["id"] == task_id
    assert payload["will_download"] is False
    assert "task resume" in payload["commands"]["task_resume_dry_run"]


def test_task_create_dry_run_does_not_write(tmp_path):
    db_path = tmp_path / "tasks.sqlite"
    result = runner.invoke(app, ["task", "create", "https://example.com/video", "--dry-run", "--db-path", str(db_path), "--format", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["dry_run"] is True
    assert payload["task"]["backend"] == "yt-dlp-api"
    assert not db_path.exists()


def test_task_create_and_status(tmp_path):
    db_path = tmp_path / "tasks.sqlite"
    create = runner.invoke(app, ["task", "create", "https://example.com/video", "--db-path", str(db_path), "--format", "json"])

    assert create.exit_code == 0
    created = json.loads(create.stdout)
    task_id = created["task"]["id"]

    status = runner.invoke(app, ["task", "status", str(task_id), "--db-path", str(db_path), "--format", "json"])
    assert status.exit_code == 0
    payload = json.loads(status.stdout)
    assert payload["task"]["id"] == task_id
    assert payload["task"]["status"] == "pending"


def test_task_retryable_lists_retryable_tasks(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite")
    task_id = store.create_task("https://example.com/watch", "browser-sniffer")
    store.mark_retryable(task_id)

    result = runner.invoke(app, ["task", "retryable", "--db-path", str(tmp_path / "tasks.sqlite"), "--format", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"
    assert payload["tasks"][0]["id"] == task_id


def test_task_resume_uses_latest_capture(monkeypatch, tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite")
    task_id = store.create_task("https://example.com/watch", "browser-sniffer")
    store.mark_retryable(task_id)
    wait_kwargs = {}

    def fake_wait_for_capture(**kwargs):
        wait_kwargs.update(kwargs)
        return {
            "capture_id": "cap-1",
            "page_url": "https://example.com/watch",
            "url": "https://cdn.example.com/video.mp4",
            "backend": "curl",
            "request_headers": {},
        }

    monkeypatch.setattr(cli_module, "_wait_for_sniffed_capture", fake_wait_for_capture)
    result = runner.invoke(
        app,
        [
            "task",
            "resume",
            str(task_id),
            "--dry-run",
            "--db-path",
            str(tmp_path / "tasks.sqlite"),
            "--output-dir",
            str(tmp_path / "downloads"),
            "--capture-session",
            "br-test",
            "--sniff-timeout",
            "3",
            "--sniff-poll-interval",
            "0.1",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "planned"
    assert payload["resumed_task"]["id"] == task_id
    assert payload["manifest"]["source_capture_id"] == "cap-1"
    assert wait_kwargs["capture_session_id"] == "br-test"
    assert wait_kwargs["timeout_seconds"] == 3


def test_task_review_summarizes_review_checklist(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite")
    task_id = store.create_task("https://example.com/video", "yt-dlp")
    store.mark_finished(task_id, 0)
    review_path = tmp_path / "downloads" / ".vdo" / "reports" / str(task_id) / "review-checklist.json"
    review_path.parent.mkdir(parents=True)
    review_path.write_text(
        json.dumps({"status": "ok", "checks": [{"name": "output_files", "status": "ok"}], "backend_result": {"size_matches_page": True}}),
        encoding="utf-8",
    )
    store.add_artifact(task_id, "review_checklist", review_path)

    result = runner.invoke(app, ["task", "review", str(task_id), "--db-path", str(tmp_path / "tasks.sqlite"), "--format", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"
    assert payload["verdict"] == "pass"
    assert payload["task"]["id"] == task_id


def test_task_export_review_writes_review_package(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite")
    task_id = store.create_task("https://example.com/video?token=secret", "yt-dlp")
    store.mark_finished(task_id, 0)
    report_path = tmp_path / "downloads" / ".vdo" / "reports" / str(task_id) / "report.md"
    review_path = report_path.with_name("review-checklist.json")
    report_path.parent.mkdir(parents=True)
    report_path.write_text("# report", encoding="utf-8")
    review_path.write_text(
        json.dumps({"checks": [{"name": "output_files", "status": "ok"}], "backend_result": {"size_matches_page": True}}),
        encoding="utf-8",
    )
    store.add_artifact(task_id, "report", report_path)
    store.add_artifact(task_id, "review_checklist", review_path)

    result = runner.invoke(
        app,
        [
            "task",
            "export-review",
            str(task_id),
            "--db-path",
            str(tmp_path / "tasks.sqlite"),
            "--output-dir",
            str(tmp_path / "review-package"),
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"
    assert payload["will_download"] is False
    assert payload["verdict"] == "pass"
    package_dir = tmp_path / "review-package" / f"task-{task_id}"
    assert (package_dir / "review-summary.md").exists()
    assert (package_dir / "task.json").exists()
    assert (package_dir / "report.md").exists()
    assert (package_dir / "review-checklist.json").exists()
    summary = (package_dir / "review-summary.md").read_text(encoding="utf-8")
    assert "secret" not in summary
    assert "Handoff" in summary
    assert "Recommended UI" in summary
    assert "handoff" in payload


def test_download_dry_run_writes_manifest(tmp_path):
    result = runner.invoke(
        app,
        [
            "download",
            "https://example.com/video",
            "--dry-run",
            "--output-dir",
            str(tmp_path / "downloads"),
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["dry_run"] is True
    assert payload["will_download"] is False
    assert payload["manifest"]["selected_backend"] == "yt-dlp-api"
    assert payload["manifest_path"].endswith(".json")


def test_download_dry_run_accepts_expected_duration_and_redacts_secrets(tmp_path):
    result = runner.invoke(
        app,
        [
            "download",
            "https://cdn.example.com/video.mp4?x-signature=url-secret&plain=ok",
            "--dry-run",
            "--expected-duration-seconds",
            "296.008",
            "--estimated-output-bytes",
            "1048576",
            "--header",
            "Cookie: header-secret",
            "--header",
            "X-Signature: header-signature-secret",
            "--output-dir",
            str(tmp_path / "downloads"),
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["manifest"]["sniffed_media"]["expected_duration_seconds"] == 296.008
    assert payload["manifest"]["estimated_output_bytes"] == 1048576
    stored = json.loads(Path(payload["manifest_path"]).read_text(encoding="utf-8"))
    assert stored["sniffed_media"]["expected_duration_seconds"] == 296.008
    assert stored["estimated_output_bytes"] == 1048576
    assert stored["request_headers"]["Cookie"] == "<redacted>"
    assert stored["request_headers"]["X-Signature"] == "<redacted>"
    assert "url-secret" not in result.stdout
    assert "header-secret" not in result.stdout
    assert "header-signature-secret" not in result.stdout
    assert "url-secret" not in json.dumps(stored)


def test_download_executes_with_artifacts(monkeypatch, tmp_path):
    seen = {}

    def fake_run_command(args, **kwargs):
        seen.update(kwargs)
        return CommandResult(args=args, returncode=0, stdout="ok\n", stderr="")

    monkeypatch.setattr(cli_module, "run_command", fake_run_command)
    result = runner.invoke(
        app,
        [
            "download",
            "https://example.com/video",
            "--output-dir",
            str(tmp_path / "downloads"),
            "--db-path",
            str(tmp_path / "tasks.sqlite"),
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "finished"
    assert payload["task"]["status"] == "finished"
    assert payload["artifacts"]["manifest_path"].endswith(".json")
    assert payload["artifacts"]["report_path"].endswith("report.md")
    assert payload["artifacts"]["review_checklist_path"].endswith("review-checklist.json")
    assert payload["transport_status"] == "pass"
    assert payload["media_integrity_status"] == "warn"
    assert seen["hard_timeout_seconds"] == 7200
    assert seen["idle_timeout_seconds"] == 300
    store = TaskStore(tmp_path / "tasks.sqlite")
    artifacts = store.list_artifacts(payload["task"]["id"])
    assert {artifact["kind"] for artifact in artifacts} == {"manifest", "log", "report", "review_checklist"}


def test_download_tencent_meeting_alias_uses_backend(monkeypatch, tmp_path):
    def fake_run_command(args, **kwargs):
        return CommandResult(
            args=args,
            returncode=0,
            stdout=json.dumps({"status": "finished", "output_file": str(tmp_path / "downloads" / "download.mp4"), "size_matches_page": True}),
            stderr="",
        )

    monkeypatch.setattr(cli_module, "run_command", fake_run_command)
    output = tmp_path / "downloads"
    output.mkdir()
    (output / "download.mp4").write_bytes(b"video")
    result = runner.invoke(
        app,
        [
            "download-tencent-meeting",
            "https://meeting.tencent.com/meeting-record/shares?id=share-id",
            "--output-dir",
            str(output),
            "--db-path",
            str(tmp_path / "tasks.sqlite"),
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "finished"
    assert payload["selected_backend"] == "tencent-meeting"


def test_download_writes_backend_json_and_ffprobe_to_review(monkeypatch, tmp_path):
    output = tmp_path / "downloads"
    output.mkdir()
    media = output / "download.mp4"
    media.write_bytes(b"video")

    def fake_run_command(args, **kwargs):
        return CommandResult(
            args=args,
            returncode=0,
            stdout=json.dumps({"status": "finished", "output_file": str(media), "size_matches_page": True}),
            stderr="",
        )

    def fake_probe_media_files(output_files):
        return [
            {
                "path": str(output_files[0]),
                "returncode": 0,
                "metadata": {"format": {"duration": "1.0", "size": "5", "format_name": "mov,mp4"}},
            }
        ]

    monkeypatch.setattr(cli_module, "run_command", fake_run_command)
    monkeypatch.setattr(cli_module, "probe_media_files", fake_probe_media_files)
    result = runner.invoke(
        app,
        [
            "download-tencent-meeting",
            "https://meeting.tencent.com/meeting-record/shares?id=share-id",
            "--output-dir",
            str(output),
            "--db-path",
            str(tmp_path / "tasks.sqlite"),
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    review_path = payload["artifacts"]["review_checklist_path"]
    review = json.loads(open(review_path, encoding="utf-8").read())
    assert review["backend_result"]["size_matches_page"] is True
    assert review["ffprobe"][0]["metadata"]["format"]["format_name"] == "mov,mp4"


def test_download_review_records_archive_sidecars(monkeypatch, tmp_path):
    output = tmp_path / "downloads"
    output.mkdir()
    media = output / "download.mp4"
    media.write_bytes(b"video")
    (output / "download.info.json").write_text("{}", encoding="utf-8")
    (output / "download.description").write_text("简介", encoding="utf-8")
    (output / "download.zh-Hans.vtt").write_text("WEBVTT", encoding="utf-8")

    def fake_run_command(args, **kwargs):
        return CommandResult(args=args, returncode=0, stdout="ok\n", stderr="")

    monkeypatch.setattr(cli_module, "run_command", fake_run_command)
    result = runner.invoke(
        app,
        [
            "download",
            "https://www.youtube.com/watch?v=abc",
            "--output-dir",
            str(output),
            "--db-path",
            str(tmp_path / "tasks.sqlite"),
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    review = json.loads(open(payload["artifacts"]["review_checklist_path"], encoding="utf-8").read())
    archive_check = next(check for check in review["checks"] if check["name"] == "archive_sidecars")
    assert archive_check["status"] == "ok"
    assert len(review["archive_files"]) == 3


def test_download_executes_fallback_when_primary_fails(monkeypatch, tmp_path):
    calls = []

    def fake_run_command(args, **kwargs):
        calls.append(args)
        if args[0] == "python":
            return CommandResult(args=args, returncode=1, stdout="", stderr="primary failed")
        return CommandResult(args=args, returncode=0, stdout="fallback ok\n", stderr="")

    monkeypatch.setattr(cli_module, "run_command", fake_run_command)
    result = runner.invoke(
        app,
        [
            "download",
            "https://example.com/video",
            "--output-dir",
            str(tmp_path / "downloads"),
            "--db-path",
            str(tmp_path / "tasks.sqlite"),
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "finished"
    assert payload["selected_backend"] == "yt-dlp"
    assert [attempt["backend"] for attempt in payload["attempts"]] == ["yt-dlp-api", "yt-dlp"]
    assert len(calls) == 2


def test_platform_download_failure_returns_browser_sniffing_recovery(monkeypatch, tmp_path):
    def fake_run_command(args, **kwargs):
        return CommandResult(args=args, returncode=1, stdout="", stderr="ERROR: Fresh cookies are needed")

    monkeypatch.setattr(cli_module, "run_command", fake_run_command)
    result = runner.invoke(
        app,
        [
            "download",
            "https://www.douyin.com/video/1234567890",
            "--output-dir",
            str(tmp_path / "downloads"),
            "--db-path",
            str(tmp_path / "tasks.sqlite"),
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "failed"
    assert payload["recovery"]["status"] == "needs_browser_sniffing"
    assert payload["recovery"]["requires_cookie_export"] is False


def test_download_passes_cli_timeout_overrides_to_backend(monkeypatch, tmp_path):
    seen = {}

    def fake_run_command(args, **kwargs):
        seen.update(kwargs)
        return CommandResult(args=args, returncode=0, stdout="ok\n", stderr="")

    monkeypatch.setattr(cli_module, "run_command", fake_run_command)
    result = runner.invoke(
        app,
        [
            "download",
            "https://example.com/video",
            "--hard-timeout",
            "9",
            "--idle-timeout",
            "3",
            "--output-dir",
            str(tmp_path / "downloads"),
            "--db-path",
            str(tmp_path / "tasks.sqlite"),
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0
    assert seen["hard_timeout_seconds"] == 9
    assert seen["idle_timeout_seconds"] == 3
    payload = json.loads(result.stdout)
    assert payload["attempts"][0]["hard_timeout_seconds"] == 9
    assert payload["attempts"][0]["idle_timeout_seconds"] == 3


def test_download_page_waits_for_capture_and_executes(monkeypatch, tmp_path):
    def fake_wait_for_capture(**kwargs):
        return {
            "capture_id": "cap-1",
            "captured_at": "2026-06-06T00:00:00+00:00",
            "page_url": "https://example.com/watch",
            "url": "https://cdn.example.com/video.mp4",
            "backend": "curl",
            "media_type": "MP4",
            "category": "files",
            "quality_score": 85,
            "download_candidate_reason": "direct media file",
            "request_headers": {"Cookie": "session=secret", "Referer": "https://example.com/watch"},
        }

    def fake_run_command(args, **kwargs):
        return CommandResult(args=args, returncode=0, stdout="ok\n", stderr="")

    monkeypatch.setattr(cli_module, "_wait_for_sniffed_capture", fake_wait_for_capture)
    monkeypatch.setattr(cli_module, "run_command", fake_run_command)
    result = runner.invoke(
        app,
        [
            "download-page",
            "https://example.com/watch",
            "--output-dir",
            str(tmp_path / "downloads"),
            "--db-path",
            str(tmp_path / "tasks.sqlite"),
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "finished"
    assert payload["selected_backend"] == "curl"
    assert payload["page_url"] == "https://example.com/watch"
    review = json.loads(open(payload["artifacts"]["review_checklist_path"], encoding="utf-8").read())
    assert review["sniffed_media"]["quality_score"] == 85
    assert payload["capture"]["request_headers"]["Cookie"] == "<redacted>"
    assert "session=secret" not in result.stdout


def test_download_page_passes_capture_session_to_latest_poll(monkeypatch, tmp_path):
    seen = {}

    def fake_api_get_json(api_base_url, path, params):
        seen.update(params)
        return {
            "status": "ok",
            "capture": {
                "capture_id": "cap-1",
                "captured_at": "2026-06-06T00:00:00+00:00",
                "page_url": "https://example.com/watch",
                "url": "https://cdn.example.com/video.mp4",
                "backend": "curl",
                "capture_session_id": "session-1",
                "request_headers": {},
            },
        }

    monkeypatch.setattr(cli_module, "_api_get_json", fake_api_get_json)
    result = runner.invoke(
        app,
        [
            "download-page",
            "https://example.com/watch",
            "--capture-session",
            "session-1",
            "--dry-run",
            "--output-dir",
            str(tmp_path / "downloads"),
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["capture_session_id"] == "session-1"
    assert payload["capture"]["capture_session_id"] == "session-1"
    assert seen["capture_session_id"] == "session-1"


def test_download_page_timeout_reports_next_action(monkeypatch):
    seen = {}

    def fake_wait_for_capture(**kwargs):
        seen.update(kwargs)
        raise TimeoutError("no capture")

    monkeypatch.setattr(cli_module, "_wait_for_sniffed_capture", fake_wait_for_capture)
    result = runner.invoke(app, ["download-page", "https://example.com/watch", "--timeout", "1", "--format", "json"])

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["status"] == "timeout"
    assert payload["will_download"] is False
    assert payload["site_adapter"]["matched"] is False
    assert seen["api_base_url"] == api_base_url()


def test_download_page_timeout_reports_rejected_sniff_without_secret(monkeypatch):
    monotonic_values = iter([0, 0, 1])

    def fake_api_get_json(api_base_url, path, params):
        return {
            "status": "not_found",
            "capture": None,
            "capture_count": 0,
            "rejection_count": 1,
            "latest_rejection": {
                "reason": "sniffed media URL must be a playable media URL or manifest",
                "url": "https://example.com/watch?token=secret",
                "content_type": "text/html",
                "resource_type": "main_frame",
                "status_code": 200,
            },
        }

    monkeypatch.setattr(cli_module, "_api_get_json", fake_api_get_json)
    monkeypatch.setattr(page_link_module.time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(page_link_module.time, "sleep", lambda seconds: None)
    result = runner.invoke(
        app,
        [
            "download-page",
            "https://example.com/watch",
            "--timeout",
            "0",
            "--poll-interval",
            "0",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["status"] == "timeout"
    assert "latest rejected submission" in payload["error"]
    assert "text/html" in payload["error"]
    assert "token=%3Credacted%3E" in payload["error"]
    assert "secret" not in result.stdout


def test_download_page_timeout_tries_site_specific_backend(monkeypatch, tmp_path):
    def fake_wait_for_capture(**kwargs):
        raise TimeoutError("no capture")

    def fake_run_command(args, **kwargs):
        return CommandResult(args=args, returncode=0, stdout=json.dumps({"status": "finished", "size_matches_page": True}), stderr="")

    monkeypatch.setattr(cli_module, "_wait_for_sniffed_capture", fake_wait_for_capture)
    monkeypatch.setattr(cli_module, "run_command", fake_run_command)
    result = runner.invoke(
        app,
        [
            "download-page",
            "https://meeting.tencent.com/meeting-record/shares?id=share-id",
            "--output-dir",
            str(tmp_path / "downloads"),
            "--db-path",
            str(tmp_path / "tasks.sqlite"),
            "--timeout",
            "1",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "finished"
    assert payload["site_specific_fallback"] is True
    assert payload["site_adapter"]["name"] == "tencent-meeting"
    assert payload["selected_backend"] == "tencent-meeting"


def test_download_page_timeout_tries_feishu_site_backend(monkeypatch, tmp_path):
    def fake_wait_for_capture(**kwargs):
        raise TimeoutError("no capture")

    def fake_run_command(args, **kwargs):
        return CommandResult(args=args, returncode=0, stdout=json.dumps({"status": "finished"}), stderr="")

    monkeypatch.setattr(cli_module, "_wait_for_sniffed_capture", fake_wait_for_capture)
    monkeypatch.setattr(cli_module, "run_command", fake_run_command)
    result = runner.invoke(
        app,
        [
            "download-page",
            "https://my.feishu.cn/wiki/example",
            "--output-dir",
            str(tmp_path / "downloads"),
            "--db-path",
            str(tmp_path / "tasks.sqlite"),
            "--timeout",
            "1",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "finished"
    assert payload["site_specific_fallback"] is True
    assert payload["site_adapter"]["name"] == "feishu"
    assert payload["selected_backend"] == "feishu"


def test_rerun_dry_run_uses_versioned_directory(tmp_path):
    db_path = tmp_path / "tasks.sqlite"
    create = runner.invoke(app, ["task", "create", "https://example.com/video", "--db-path", str(db_path), "--format", "json"])
    task_id = json.loads(create.stdout)["task"]["id"]

    result = runner.invoke(
        app,
        [
            "rerun",
            str(task_id),
            "--dry-run",
            "--output-dir",
            str(tmp_path / "downloads"),
            "--db-path",
            str(db_path),
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["dry_run"] is True
    assert payload["original_task"]["id"] == task_id
    assert "/.vdo/reruns/" in payload["rerun_dir"].replace("\\", "/")
    assert payload["manifest"]["rerun_of_task_id"] == task_id


def test_rerun_executes_with_new_task_and_artifacts(monkeypatch, tmp_path):
    def fake_run_command(args, **kwargs):
        return CommandResult(args=args, returncode=0, stdout="ok\n", stderr="")

    monkeypatch.setattr(cli_module, "run_command", fake_run_command)
    db_path = tmp_path / "tasks.sqlite"
    create = runner.invoke(app, ["task", "create", "https://example.com/video", "--db-path", str(db_path), "--format", "json"])
    original_task_id = json.loads(create.stdout)["task"]["id"]

    result = runner.invoke(
        app,
        [
            "rerun",
            str(original_task_id),
            "--output-dir",
            str(tmp_path / "downloads"),
            "--db-path",
            str(db_path),
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "finished"
    assert payload["original_task"]["id"] == original_task_id
    assert payload["task"]["id"] != original_task_id
    assert payload["task"]["status"] == "finished"
    assert "/.vdo/reruns/" in payload["artifacts"]["manifest_path"].replace("\\", "/")
    assert payload["artifacts"]["log_path"].endswith(".log")


def test_batch_plans_items_without_execution(tmp_path):
    spec_path = tmp_path / "batch.json"
    spec_path.write_text(
        json.dumps(
            {
                "items": [
                    "https://example.com/video",
                    {"url": "https://example.com/live/master.m3u8", "backend": "n_m3u8dl_re"},
                ]
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "batch",
            str(spec_path),
            "--output-dir",
            str(tmp_path / "downloads"),
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["contract"] == "agent_batch_v1"
    assert payload["status"] == "planned"
    assert payload["execute"] is False
    assert payload["item_count"] == 2
    assert payload["items"][0]["will_download"] is False
    assert "/.vdo/batches/" in payload["batch_dir"].replace("\\", "/")
    assert payload["items"][1]["backend"] == "n_m3u8dl_re"
    assert payload["cleaning"]["item_count"] == 2


def test_batch_clean_command_writes_clean_spec(tmp_path):
    spec_path = tmp_path / "raw.txt"
    output_path = tmp_path / "clean.json"
    spec_path.write_text(
        "https://youtu.be/AbJWIknEA-8?si=one\nhttps://youtu.be/AbJWIknEA-8?si=two\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["batch-clean", str(spec_path), "--output", str(output_path), "--format", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["item_count"] == 1
    assert payload["skipped_count"] == 1
    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert written["items"][0]["url"] == "https://www.youtube.com/watch?v=AbJWIknEA-8"


def test_batch_accepts_raw_text_and_cleans_before_planning(tmp_path):
    spec_path = tmp_path / "raw.txt"
    spec_path.write_text(
        "简介 https://youtu.be/_0TY3cD90p8?si=tracking\n"
        "外链 https://www.youtube.com/redirect?q=https%3A%2F%2Fexample.com&html_redirect=1\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["batch", str(spec_path), "--output-dir", str(tmp_path / "downloads"), "--format", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["item_count"] == 1
    assert payload["items"][0]["url"] == "https://www.youtube.com/watch?v=_0TY3cD90p8"
    assert payload["cleaning"]["skipped"][0]["reason"] == "youtube_redirect_external"


def test_batch_execute_runs_each_item(monkeypatch, tmp_path):
    def fake_run_command(args, **kwargs):
        return CommandResult(args=args, returncode=0, stdout="ok\n", stderr="")

    monkeypatch.setattr(cli_module, "run_command", fake_run_command)
    spec_path = tmp_path / "batch.json"
    spec_path.write_text(json.dumps(["https://example.com/a", "https://example.com/b"]), encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "batch",
            str(spec_path),
            "--execute",
            "--output-dir",
            str(tmp_path / "downloads"),
            "--db-path",
            str(tmp_path / "tasks.sqlite"),
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "finished"
    assert payload["execute"] is True
    assert len(payload["items"]) == 2
    assert {item["task"]["status"] for item in payload["items"]} == {"finished"}
    assert all(item["artifacts"]["report_path"].endswith("report.md") for item in payload["items"])


def test_batch_execute_reports_failed_items(monkeypatch, tmp_path):
    def fake_run_command(args, **kwargs):
        return CommandResult(args=args, returncode=1, stdout="", stderr="failed")

    monkeypatch.setattr(cli_module, "run_command", fake_run_command)
    spec_path = tmp_path / "batch.json"
    spec_path.write_text(json.dumps(["https://example.com/a"]), encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "batch",
            str(spec_path),
            "--execute",
            "--output-dir",
            str(tmp_path / "downloads"),
            "--db-path",
            str(tmp_path / "tasks.sqlite"),
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "finished_with_failures"
    assert payload["items"][0]["status"] == "failed"


def test_batch_passes_ytdlp_cookies_to_fallbacks(monkeypatch, tmp_path):
    calls = []

    def fake_run_command(args, **kwargs):
        calls.append(args)
        return CommandResult(args=args, returncode=1, stdout="", stderr="failed")

    monkeypatch.setattr(cli_module, "run_command", fake_run_command)
    spec_path = tmp_path / "batch.json"
    spec_path.write_text(json.dumps(["https://www.youtube.com/watch?v=abc"]), encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "batch",
            str(spec_path),
            "--execute",
            "--cookies-from-browser",
            "chrome",
            "--output-dir",
            str(tmp_path / "downloads"),
            "--db-path",
            str(tmp_path / "tasks.sqlite"),
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0
    assert "--cookies-from-browser" in calls[0]
    assert "--cookies-from-browser" in calls[1]
    assert json.loads(result.stdout)["yt_dlp_options"]["cookies_from_browser"] == "chrome"


def test_batch_passes_explicit_ytdlp_ejs_options_to_fallbacks(monkeypatch, tmp_path):
    calls = []

    def fake_run_command(args, **kwargs):
        calls.append(args)
        return CommandResult(args=args, returncode=1, stdout="", stderr="failed")

    monkeypatch.setattr(cli_module, "run_command", fake_run_command)
    spec_path = tmp_path / "batch.json"
    spec_path.write_text(json.dumps(["https://www.youtube.com/watch?v=abc"]), encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "batch",
            str(spec_path),
            "--execute",
            "--cookies",
            str(tmp_path / "youtube-cookies.txt"),
            "--js-runtimes",
            "node",
            "--remote-component",
            "ejs:github",
            "--output-dir",
            str(tmp_path / "downloads"),
            "--db-path",
            str(tmp_path / "tasks.sqlite"),
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0
    assert "--js-runtimes" in calls[0]
    assert "--remote-components" in calls[0]
    assert "--js-runtimes" in calls[1]
    assert "--remote-components" in calls[1]
    payload = json.loads(result.stdout)
    assert payload["yt_dlp_options"]["cookies"] == "<cookies-file>"
    assert payload["yt_dlp_options"]["js_runtimes"] == "node"
    assert payload["yt_dlp_options"]["remote_components"] == ["ejs:github"]


def test_batch_execute_page_link_mode_sniff_waits_for_capture(monkeypatch, tmp_path):
    spec_path = tmp_path / "batch.json"
    spec_path.write_text(json.dumps(["https://example.com/watch"]), encoding="utf-8")

    def fake_wait_for_capture(**kwargs):
        return {
            "capture_id": "cap-1",
            "captured_at": "2026-06-06T00:00:00+00:00",
            "page_url": "https://example.com/watch",
            "url": "https://cdn.example.com/video.mp4",
            "backend": "curl",
            "request_headers": {},
        }

    def fake_run_command(args, **kwargs):
        return CommandResult(args=args, returncode=0, stdout="ok\n", stderr="")

    monkeypatch.setattr(cli_module, "_wait_for_sniffed_capture", fake_wait_for_capture)
    monkeypatch.setattr(cli_module, "run_command", fake_run_command)
    result = runner.invoke(
        app,
        [
            "batch",
            str(spec_path),
            "--execute",
            "--page-link-mode",
            "sniff",
            "--output-dir",
            str(tmp_path / "downloads"),
            "--db-path",
            str(tmp_path / "tasks.sqlite"),
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["items"][0]["page_link_mode"] == "sniff"
    assert payload["items"][0]["source_page_url"] == "https://example.com/watch"
    assert payload["items"][0]["selected_backend"] == "curl"
    assert payload["items"][0]["status"] == "finished"


def test_batch_page_link_timeout_creates_retryable_task(monkeypatch, tmp_path):
    spec_path = tmp_path / "batch.json"
    spec_path.write_text(json.dumps(["https://example.com/watch"]), encoding="utf-8")

    def fake_wait_for_capture(**kwargs):
        raise TimeoutError("no capture")

    monkeypatch.setattr(cli_module, "_wait_for_sniffed_capture", fake_wait_for_capture)
    result = runner.invoke(
        app,
        [
            "batch",
            str(spec_path),
            "--execute",
            "--page-link-mode",
            "sniff",
            "--output-dir",
            str(tmp_path / "downloads"),
            "--db-path",
            str(tmp_path / "tasks.sqlite"),
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "finished_with_retryable"
    assert payload["items"][0]["status"] == "retryable"
    assert payload["items"][0]["sniff_error"] == "no capture"
    store = TaskStore(tmp_path / "tasks.sqlite")
    assert store.list_tasks(problem_only=True)[0]["status"] == "retryable"
