from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from video_orchestrator.runtime.artifact_lifecycle import ArtifactLifecycleAdapterError
from video_orchestrator.runtime.artifact_lifecycle import doctor
from video_orchestrator.runtime.artifact_lifecycle import explain_artifact
from video_orchestrator.runtime.artifact_lifecycle import media_operation_preflight
from video_orchestrator.runtime.artifact_lifecycle import plan_gc
from video_orchestrator.runtime.artifact_lifecycle import resolve_declared_root
from video_orchestrator.runtime.artifact_lifecycle import restore_synthetic
from video_orchestrator.runtime.artifact_lifecycle import scan_media_root
from video_orchestrator.runtime.artifact_lifecycle import utc_run_id
from video_orchestrator.runtime.artifact_lifecycle import verify_gc_plan


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="VDO media artifact lifecycle adapter (plan-only)")
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor_parser = subparsers.add_parser("doctor")
    doctor_parser.add_argument("--config", type=Path)

    scan_parser = subparsers.add_parser("scan")
    scan_parser.add_argument("--root-id", required=True)
    scan_parser.add_argument("--output", type=Path, required=True)
    scan_parser.add_argument("--checkpoint", type=Path, required=True)
    scan_parser.add_argument("--closure-run-id", default=None)
    scan_parser.add_argument("--config", type=Path)

    explain_parser = subparsers.add_parser("explain")
    explain_parser.add_argument("--manifest", type=Path, required=True)
    explain_parser.add_argument("--artifact-id", required=True)

    plan_parser = subparsers.add_parser("plan-gc")
    plan_parser.add_argument("--manifest", type=Path, required=True)
    plan_parser.add_argument("--root-id", required=True)
    plan_parser.add_argument("--quarantine-root", type=Path, required=True)
    plan_parser.add_argument("--output", type=Path, required=True)
    plan_parser.add_argument("--owner-idle-confirmed", action="store_true")
    plan_parser.add_argument("--no-active-dispatch-confirmed", action="store_true")
    plan_parser.add_argument("--config", type=Path)

    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--plan", type=Path, required=True)
    verify_parser.add_argument("--manifest", type=Path, required=True)
    verify_parser.add_argument("--root-id", required=True)
    verify_parser.add_argument("--quarantine-root", type=Path, required=True)
    verify_parser.add_argument("--config", type=Path)

    restore_parser = subparsers.add_parser("restore")
    restore_parser.add_argument("--synthetic", action="store_true")
    restore_parser.add_argument("--root", type=Path, required=True)
    restore_parser.add_argument("--quarantine-root", type=Path, required=True)
    restore_parser.add_argument("--plan", type=Path, required=True)
    restore_parser.add_argument("--receipt", type=Path, required=True)
    restore_parser.add_argument("--output", type=Path, required=True)
    restore_parser.add_argument("--config", type=Path)

    preflight_parser = subparsers.add_parser("preflight")
    preflight_parser.add_argument("--output-dir", type=Path, required=True)
    preflight_parser.add_argument("--operation", choices=["download", "merge", "remux", "transcode"], required=True)
    preflight_parser.add_argument("--expected-duration-seconds", type=float)
    preflight_parser.add_argument("--estimated-output-bytes", type=int)
    preflight_parser.add_argument("--config", type=Path)
    return parser


def _run(args: argparse.Namespace) -> dict[str, Any]:
    if args.command == "doctor":
        return doctor(args.config)
    if args.command == "scan":
        declaration = resolve_declared_root(args.root_id, args.config)
        return scan_media_root(
            declaration,
            output=args.output,
            checkpoint=args.checkpoint,
            closure_run_id=args.closure_run_id or utc_run_id(),
            config_path=args.config,
        )
    if args.command == "explain":
        return explain_artifact(args.manifest, args.artifact_id)
    if args.command == "plan-gc":
        declaration = resolve_declared_root(args.root_id, args.config)
        return plan_gc(
            args.manifest,
            declaration,
            quarantine_root=args.quarantine_root,
            output=args.output,
            owner_state="idle_confirmed" if args.owner_idle_confirmed else "unknown",
            dispatch_state="none_active" if args.no_active_dispatch_confirmed else "unknown",
            config_path=args.config,
        )
    if args.command == "verify":
        declaration = resolve_declared_root(args.root_id, args.config)
        return verify_gc_plan(args.plan, args.manifest, declaration, args.quarantine_root)
    if args.command == "restore":
        if not args.synthetic:
            raise ArtifactLifecycleAdapterError("REAL_RESTORE_DISABLED")
        return restore_synthetic(
            synthetic_root=args.root,
            quarantine_root=args.quarantine_root,
            plan_path=args.plan,
            receipt_path=args.receipt,
            output=args.output,
            config_path=args.config,
        )
    if args.command == "preflight":
        return media_operation_preflight(
            args.output_dir,
            operation=args.operation,
            expected_duration_seconds=args.expected_duration_seconds,
            estimated_output_bytes=args.estimated_output_bytes,
            config_path=args.config,
        )
    raise ArtifactLifecycleAdapterError("COMMAND_NOT_IMPLEMENTED", str(args.command))


def main(argv: list[str] | None = None) -> int:
    try:
        payload = _run(_parser().parse_args(argv))
    except (ArtifactLifecycleAdapterError, OSError, ValueError) as exc:
        payload = {
            "contract": "vdo-artifact-lifecycle-adapter.v1",
            "status": "blocked",
            "error_code": getattr(exc, "code", exc.__class__.__name__),
            "error": str(exc),
            "cleanup_mode": "plan_only",
            "filesystem_mutation_performed": False,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 1
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
