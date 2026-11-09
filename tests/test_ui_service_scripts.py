from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _script(name: str) -> str:
    return (PROJECT_ROOT / "scripts" / name).read_text(encoding="utf-8")


def test_service_scripts_do_not_require_privileged_tcp_enumeration():
    for name in ("start-ui.ps1", "start-ui-scheduled.ps1", "stop-ui.ps1"):
        assert "Get-NetTCPConnection" not in _script(name)


def test_start_script_waits_for_vdo_health_and_keeps_obsidian_opt_in():
    script = _script("start-ui.ps1")

    assert "/health" in script
    assert "AddSeconds(15)" in script
    assert "[switch]$UpdatePortRecord" in script
    assert "if ($UpdatePortRecord)" in script
    assert "process_start_time_utc" in script
    assert "process_path" in script
    assert 'service_lifecycle.status -ne "managed_running"' in script


def test_scheduled_start_requires_owned_process_and_vdo_health():
    script = _script("start-ui-scheduled.ps1")

    assert "/health" in script
    assert "$process -and (Test-ApiHealthy)" in script
    assert "AddSeconds(15)" in script


def test_stop_script_verifies_process_identity_before_stopping():
    script = _script("stop-ui.ps1")

    assert "process_start_time_utc" in script
    assert "process_path" in script
    assert "Get-Process -Id $PidValue" in script
    assert "Get-CimInstance" in script


def test_start_script_uses_explicit_optional_port_record_path():
    script = _script("start-ui.ps1")

    assert '[string]$PortRecordPath = ""' in script
    assert '$PortRecord = "' not in script
    assert "Test-Path -LiteralPath $RecordPath" in script
    assert "-RecordPath $PortRecordPath" in script


def test_launchers_use_ordinary_policy_invocations_only():
    for name in ("start-ui.ps1", "start-ui-scheduled.ps1"):
        assert "ExecutionPolicy Bypass" not in _script(name)


def test_serve_script_uses_managed_python_and_checks_exit_codes():
    script = _script("serve-ui.ps1")

    assert '. (Join-Path $ProjectRoot "scripts\\runtime-python.ps1")' in script
    assert "& $VdoPython scripts\\sync_runtime_config.py" in script
    assert "& $VdoPython -m video_orchestrator.config check" in script
    assert "exit $LASTEXITCODE" in script


def test_scheduled_script_checks_native_scheduler_exit_codes():
    script = _script("start-ui-scheduled.ps1")

    assert script.count("if ($LASTEXITCODE -ne 0)") == 2
