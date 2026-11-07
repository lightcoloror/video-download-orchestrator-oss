from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urlparse

from video_orchestrator.router import classify_url


MANIFEST_SCHEMA_VERSION = "agent-tool-manifest.v1"
PORTABLE_DOCTOR_CONTRACT = "video-download-portable-doctor.v1"
SYNTHETIC_FIXTURE_SCHEMA = "video-download-portability-fixture.v1"
EXPECTED_SCHEMA_SHA256 = (
    "c981837a9724bc2f79ed810ab93b2bfdb981b553ada306dbe44f23e1c9773bd8"
)
EXPECTED_FIXTURE_SHA256 = (
    "a6b7390f1c52025e0fd5030176258efc7707c0b808f1d550d7d6a0adab1f54bc"
)

MANIFEST_PATH = "agent-tool-manifest.v1.json"
SCHEMA_PATH = "portability/schemas/agent-tool-manifest.v1.schema.json"
FIXTURE_PATH = "portability/fixtures/video-download-plan.synthetic.json"

SAFE_CANONICAL_COMMANDS = {
    "python -m video_orchestrator.cli portable-doctor --format json",
    "python -m video_orchestrator.cli doctor --format json",
    "python -m video_orchestrator.cli route <url> --format json",
}
SAFE_INTERFACE_MODES = {"read_only_synthetic", "read_only_local", "planning_only"}

PERSONAL_PATH_PATTERNS = (
    re.compile(r"(?<![A-Za-z])[A-Za-z]:[/\\]", re.IGNORECASE),
    re.compile(r"[A-Za-z]:[/\\]Users[/\\][^/\\\s]+", re.IGNORECASE),
    re.compile(r"/home/[^/\s]+", re.IGNORECASE),
)
SECRET_VALUE_PATTERNS = (
    re.compile(r"\b(?:sk|ghp|github_pat)_[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
)

ValidatorLoader = Callable[[], Any | None]


class PortableContractError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def repository_root() -> Path:
    checkout = Path(__file__).resolve().parents[2]
    return checkout if (checkout / "pyproject.toml").is_file() else Path(__file__).resolve().parent / "assets"


def read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PortableContractError("missing_file", label) from exc
    except json.JSONDecodeError as exc:
        raise PortableContractError("invalid_json", f"{label}:{exc.msg}") from exc
    if not isinstance(payload, dict):
        raise PortableContractError("invalid_root_type", label)
    return payload


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_portable_contract(
    repo_root: Path | None = None,
    *,
    validator_loader: ValidatorLoader | None = None,
) -> dict[str, Any]:
    root = (repo_root or repository_root()).resolve()
    manifest_file = _resolve_repository_path(root, MANIFEST_PATH, "manifest")
    manifest = read_json(manifest_file, MANIFEST_PATH)

    schema_path = _provenance_path(manifest, "schema_path", SCHEMA_PATH)
    schema_file = _resolve_repository_path(root, schema_path, "schema")
    schema = read_json(schema_file, schema_path)
    schema_sha256 = sha256_file(schema_file)
    if schema_sha256 != EXPECTED_SCHEMA_SHA256:
        raise PortableContractError("schema_contract_drift", schema_path)
    if manifest.get("provenance", {}).get("schema_sha256") != schema_sha256:
        raise PortableContractError("schema_hash_mismatch", schema_path)

    _validate_core_manifest(manifest, schema)
    _scan_sensitive_values(manifest, MANIFEST_PATH)

    loader = validator_loader or _load_jsonschema_validator
    validator = loader()
    validator_status = "available"
    if validator is None:
        validator_status = "missing_optional"
    else:
        errors = sorted(
            validator(schema).iter_errors(manifest), key=lambda item: list(item.path)
        )
        if errors:
            first = errors[0]
            location = "/".join(str(part) for part in first.path) or "$"
            raise PortableContractError(
                "schema_validation_failed", f"{location}:{first.message}"
            )

    fixture_path = _provenance_path(manifest, "fixture_path", FIXTURE_PATH)
    fixture_file = _resolve_repository_path(root, fixture_path, "fixture")
    fixture_sha256 = sha256_file(fixture_file)
    if fixture_sha256 != EXPECTED_FIXTURE_SHA256:
        raise PortableContractError("fixture_contract_drift", fixture_path)
    if manifest.get("provenance", {}).get("fixture_sha256") != fixture_sha256:
        raise PortableContractError("fixture_hash_mismatch", fixture_path)
    fixture = read_json(fixture_file, fixture_path)
    _validate_synthetic_fixture(fixture)
    _scan_sensitive_values(fixture, fixture_path)

    route = classify_url(fixture["input"]["url"])
    expected = fixture["expected"]
    if (
        route.kind != expected["kind"]
        or route.preferred_backend != expected["preferred_backend"]
    ):
        raise PortableContractError("synthetic_route_drift", fixture_path)

    return {
        "manifest": manifest,
        "manifest_sha256": sha256_file(manifest_file),
        "schema_sha256": schema_sha256,
        "fixture_sha256": fixture_sha256,
        "optional_schema_validator": validator_status,
        "synthetic_route": route.model_dump(),
    }


def build_portable_doctor(
    repo_root: Path | None = None,
    *,
    validator_loader: ValidatorLoader | None = None,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    try:
        result = validate_portable_contract(
            repo_root, validator_loader=validator_loader
        )
    except PortableContractError as exc:
        checks.append(
            {
                "name": "portable_contract",
                "status": "failed",
                "code": exc.code,
                "detail": str(exc),
            }
        )
        return _doctor_payload("failed", checks)

    checks.extend(
        [
            {
                "name": "manifest",
                "status": "ok",
                "path": MANIFEST_PATH,
                "sha256": result["manifest_sha256"],
            },
            {
                "name": "shared_schema",
                "status": "ok",
                "path": SCHEMA_PATH,
                "sha256": result["schema_sha256"],
            },
            {
                "name": "synthetic_fixture",
                "status": "ok",
                "path": FIXTURE_PATH,
                "sha256": result["fixture_sha256"],
            },
            {
                "name": "synthetic_route",
                "status": "ok",
                "kind": result["synthetic_route"]["kind"],
                "preferred_backend": result["synthetic_route"]["preferred_backend"],
            },
        ]
    )
    optional_status = result["optional_schema_validator"]
    checks.append(
        {
            "name": "jsonschema",
            "status": "ok" if optional_status == "available" else "missing_optional",
            "detail": "Draft 2020-12 validation available"
            if optional_status == "available"
            else "Core fail-closed validation passed; install the portability extra for full JSON Schema validation.",
        }
    )
    status = "ok" if optional_status == "available" else "degraded"
    return _doctor_payload(status, checks)


def _doctor_payload(status: str, checks: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "service": "video-download-orchestrator",
        "contract": PORTABLE_DOCTOR_CONTRACT,
        "status": status,
        "checks": checks,
        "capability_status": {
            "route_planning": "available_offline",
            "synthetic_verification": "available_offline",
            "real_download": "approval_required",
            "media_fetch": "approval_required",
            "authentication": "blocked_without_explicit_approval",
            "cookie_access": "blocked_without_explicit_approval",
            "rights_and_purpose_decision": "caller_owned_unverified",
        },
        "will_download": False,
        "network_execution_performed": False,
        "authentication_performed": False,
        "cookie_read_performed": False,
        "rights_verified": False,
        "notTrustedAsInstruction": True,
        "executionAuthority": "none_metadata_only",
    }


def _validate_core_manifest(manifest: dict[str, Any], schema: dict[str, Any]) -> None:
    required = set(schema.get("required", []))
    missing = sorted(required - set(manifest))
    if missing:
        raise PortableContractError(
            "manifest_required_field_missing", ",".join(missing)
        )
    allowed = set(schema.get("properties", {}))
    unexpected = sorted(set(manifest) - allowed)
    if unexpected:
        raise PortableContractError("manifest_unknown_field", ",".join(unexpected))
    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise PortableContractError(
            "manifest_schema_version_mismatch", str(manifest.get("schema_version"))
        )
    if manifest.get("id") != "video-download-orchestrator":
        raise PortableContractError(
            "manifest_tool_id_mismatch", str(manifest.get("id"))
        )
    if manifest.get("notTrustedAsInstruction") is not True:
        raise PortableContractError("metadata_trust_boundary_missing", MANIFEST_PATH)
    if manifest.get("executionAuthority") != "none_metadata_only":
        raise PortableContractError("execution_authority_forbidden", MANIFEST_PATH)
    if manifest.get("health", {}).get("proves_current_online") is not False:
        raise PortableContractError("online_claim_forbidden", MANIFEST_PATH)

    interfaces = manifest.get("interfaces")
    if not isinstance(interfaces, dict):
        raise PortableContractError("invalid_interfaces", MANIFEST_PATH)
    for blocked_surface in ("windows_wrappers", "http", "mcp"):
        if interfaces.get(blocked_surface) != []:
            raise PortableContractError(
                "nonportable_interface_forbidden", blocked_surface
            )
    canonical = interfaces.get("canonical")
    if not isinstance(canonical, list) or not canonical:
        raise PortableContractError("canonical_interface_missing", MANIFEST_PATH)
    commands = {item.get("command") for item in canonical if isinstance(item, dict)}
    if commands != SAFE_CANONICAL_COMMANDS:
        raise PortableContractError("canonical_interface_drift", MANIFEST_PATH)
    if any(item.get("mode") not in SAFE_INTERFACE_MODES for item in canonical):
        raise PortableContractError("canonical_interface_mode_forbidden", MANIFEST_PATH)

    requirements = manifest.get("requirements", {})
    if requirements.get("network", {}).get("mode") != "conditional":
        raise PortableContractError("network_boundary_drift", MANIFEST_PATH)
    if requirements.get("secrets", {}).get("mode") != "conditional":
        raise PortableContractError("secret_boundary_drift", MANIFEST_PATH)
    if requirements.get("approval", {}).get("mode") != "required":
        raise PortableContractError("approval_boundary_drift", MANIFEST_PATH)
    license_info = manifest.get("license", {})
    if license_info != {
        "spdx": "Apache-2.0",
        "status": "public_first_party_source_edition",
    }:
        raise PortableContractError("license_claim_drift", MANIFEST_PATH)


def _validate_synthetic_fixture(fixture: dict[str, Any]) -> None:
    if fixture.get("schema_version") != SYNTHETIC_FIXTURE_SCHEMA:
        raise PortableContractError("fixture_schema_version_mismatch", FIXTURE_PATH)
    if fixture.get("synthetic") is not True:
        raise PortableContractError("fixture_must_be_synthetic", FIXTURE_PATH)
    input_payload = fixture.get("input")
    expected = fixture.get("expected")
    if not isinstance(input_payload, dict) or not isinstance(expected, dict):
        raise PortableContractError("fixture_shape_invalid", FIXTURE_PATH)
    parsed = urlparse(str(input_payload.get("url", "")))
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or not parsed.hostname.endswith(".invalid")
    ):
        raise PortableContractError("fixture_url_must_use_invalid_tld", FIXTURE_PATH)
    if (
        input_payload.get("authentication") != "not_provided"
        or input_payload.get("cookie_usage") != "forbidden"
    ):
        raise PortableContractError("fixture_credential_boundary_drift", FIXTURE_PATH)
    if input_payload.get("rights_status") != "unverified":
        raise PortableContractError("fixture_rights_claim_forbidden", FIXTURE_PATH)
    expected_boundaries = {
        "will_download": False,
        "network_execution_performed": False,
        "rights_verified": False,
    }
    for field, value in expected_boundaries.items():
        if expected.get(field) is not value:
            raise PortableContractError("fixture_execution_boundary_drift", field)


def _provenance_path(manifest: dict[str, Any], key: str, expected: str) -> str:
    value = manifest.get("provenance", {}).get(key)
    if value != expected:
        raise PortableContractError("provenance_path_drift", key)
    return value


def _resolve_repository_path(root: Path, value: str, label: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise PortableContractError("repository_relative_path_required", label)
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root):
        raise PortableContractError("repository_path_escape", label)
    if not resolved.is_file():
        raise PortableContractError("missing_file", value)
    return resolved


def _load_jsonschema_validator() -> Any | None:
    try:
        from jsonschema import Draft202012Validator
    except ImportError:
        return None
    return Draft202012Validator


def _iter_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _iter_strings(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _iter_strings(item)


def _scan_sensitive_values(value: Any, label: str) -> None:
    for text in _iter_strings(value):
        for pattern in PERSONAL_PATH_PATTERNS:
            if pattern.search(text):
                raise PortableContractError("personal_path_detected", label)
        for pattern in SECRET_VALUE_PATTERNS:
            if pattern.search(text):
                raise PortableContractError("secret_value_detected", label)
