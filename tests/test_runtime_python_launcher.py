import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(os.name != "nt", reason="Windows desktop launcher")
def test_launcher_prefers_project_interpreter_and_backend_path(tmp_path):
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if not shell:
        pytest.skip("PowerShell unavailable")
    script = tmp_path / "scripts" / "runtime-python.ps1"
    script.parent.mkdir()
    shutil.copyfile(Path(__file__).parents[1] / "scripts" / script.name, script)
    python = tmp_path / ".venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.touch()  # Selection only: never run this synthetic executable.
    escaped = str(script).replace("'", "''")
    command = f". '{escaped}'; @{{python=$VdoPython; first=($env:PATH -split ';')[0]}} | ConvertTo-Json -Compress"
    result = subprocess.run([shell, "-NoProfile", "-Command", command], capture_output=True, text=True, timeout=15, check=True)
    data = json.loads(result.stdout)
    assert Path(data["python"]) == python
    assert Path(data["first"]) == python.parent


def test_start_and_status_share_runtime_selector_and_check_start_failures():
    scripts = Path(__file__).parents[1] / "scripts"
    start = (scripts / "start-ui.ps1").read_text(encoding="utf-8-sig")
    status = (scripts / "status-ui.ps1").read_text(encoding="utf-8-sig")
    client = (scripts / "start-mediago-client.ps1").read_text(encoding="utf-8-sig")
    assert "runtime-python.ps1" in start and "runtime-python.ps1" in status
    assert '-FilePath $VdoPython' in start
    assert '& $VdoPython -m video_orchestrator.cli' in status
    assert 'if ($LASTEXITCODE -ne 0)' in client
