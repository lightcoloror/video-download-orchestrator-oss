import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from video_orchestrator.cli import app
from video_orchestrator.portability import FIXTURE_PATH
from video_orchestrator.portability import MANIFEST_PATH
from video_orchestrator.portability import SCHEMA_PATH
from video_orchestrator.portability import PortableContractError
from video_orchestrator.portability import build_portable_doctor
from video_orchestrator.portability import repository_root
from video_orchestrator.portability import validate_portable_contract


runner = CliRunner()


def test_root_manifest_is_clone_discoverable_and_metadata_only():
    root = repository_root()
    result = validate_portable_contract(root)
    manifest = result["manifest"]

    assert (root / MANIFEST_PATH).is_file()
    assert manifest["notTrustedAsInstruction"] is True
    assert manifest["executionAuthority"] == "none_metadata_only"
    assert manifest["portability"]["in_repo_discovery"] is True
    assert manifest["license"] == {
        "spdx": "Apache-2.0",
        "status": "public_first_party_source_edition",
    }
    commands = [item["command"] for item in manifest["interfaces"]["canonical"]]
    assert all(" --execute" not in command for command in commands)
    assert all(" download " not in command for command in commands)


def test_clone_contract_copy_validates_without_registry_or_source_inventory(tmp_path):
    clone_root = _copy_contract(tmp_path)

    result = validate_portable_contract(clone_root)

    assert result["synthetic_route"]["kind"] == "direct-transfer"
    assert result["synthetic_route"]["preferred_backend"] == "aria2"


def test_portable_doctor_is_offline_fail_closed_and_idempotent():
    first = build_portable_doctor()
    second = build_portable_doctor()

    assert first == second
    assert first["status"] in {"ok", "degraded"}
    assert first["will_download"] is False
    assert first["network_execution_performed"] is False
    assert first["authentication_performed"] is False
    assert first["cookie_read_performed"] is False
    assert first["rights_verified"] is False
    assert first["capability_status"]["real_download"] == "approval_required"


def test_portable_doctor_cli_emits_machine_readable_receipt():
    result = runner.invoke(app, ["portable-doctor", "--format", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["contract"] == "video-download-portable-doctor.v1"
    assert payload["executionAuthority"] == "none_metadata_only"
    assert payload["will_download"] is False


def test_missing_optional_jsonschema_is_degraded_but_core_validation_runs():
    payload = build_portable_doctor(validator_loader=lambda: None)

    assert payload["status"] == "degraded"
    check = next(item for item in payload["checks"] if item["name"] == "jsonschema")
    assert check["status"] == "missing_optional"
    assert payload["will_download"] is False


def test_corrupted_manifest_execution_authority_fails_closed(tmp_path):
    clone_root = _copy_contract(tmp_path)
    manifest_path = clone_root / MANIFEST_PATH
    manifest = _read_json(manifest_path)
    manifest["executionAuthority"] = "execute"
    _write_json(manifest_path, manifest)

    with pytest.raises(PortableContractError, match="agent-tool-manifest") as error:
        validate_portable_contract(clone_root)

    assert error.value.code == "execution_authority_forbidden"


def test_schema_content_drift_fails_closed(tmp_path):
    clone_root = _copy_contract(tmp_path)
    schema_path = clone_root / SCHEMA_PATH
    schema_path.write_text(
        schema_path.read_text(encoding="utf-8") + "\n", encoding="utf-8"
    )

    with pytest.raises(PortableContractError) as error:
        validate_portable_contract(clone_root)

    assert error.value.code == "schema_contract_drift"


def test_fixture_path_drift_fails_closed(tmp_path):
    clone_root = _copy_contract(tmp_path)
    manifest_path = clone_root / MANIFEST_PATH
    manifest = _read_json(manifest_path)
    manifest["provenance"]["fixture_path"] = "fixtures/other.json"
    _write_json(manifest_path, manifest)

    with pytest.raises(PortableContractError) as error:
        validate_portable_contract(clone_root)

    assert error.value.code == "provenance_path_drift"


@pytest.mark.parametrize(
    ("description", "expected_code"),
    [
        (
            "Local path " + "C:" + "\\Users\\example\\cookies.txt",
            "personal_path_detected",
        ),
        ("Token " + "ghp_" + ("a" * 22), "secret_value_detected"),
    ],
)
def test_manifest_rejects_personal_paths_and_secret_values(
    tmp_path, description, expected_code
):
    clone_root = _copy_contract(tmp_path)
    manifest_path = clone_root / MANIFEST_PATH
    manifest = _read_json(manifest_path)
    manifest["description"] = description
    _write_json(manifest_path, manifest)

    with pytest.raises(PortableContractError) as error:
        validate_portable_contract(clone_root)

    assert error.value.code == expected_code


def _copy_contract(tmp_path: Path) -> Path:
    source_root = repository_root()
    clone_root = tmp_path / "authorized-clone"
    for relative in (MANIFEST_PATH, SCHEMA_PATH, FIXTURE_PATH):
        source = source_root / relative
        target = clone_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    return clone_root


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
