from __future__ import annotations

import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    import filetype
except ImportError:
    filetype = None

from video_orchestrator.runtime.media_completeness import downloaded_completeness_check
from video_orchestrator.runtime.media_completeness import sniffed_completeness_diagnostic
from video_orchestrator.runtime.redaction import redact_attempts
from video_orchestrator.runtime.redaction import redact_sniffed_media


TRANSPORT_CHECK_NAMES = {
    "backend_status",
    "stderr_warnings",
    "backend_attempt_errors",
    "fallback_used",
    "backend_timeouts",
}
MEDIA_INTEGRITY_CHECK_NAMES = {
    "output_files",
    "expected_extension",
    "backend_stdout_json",
    "ffprobe",
    "media_completeness",
    "output_type",
}


def build_quality_review(
    *,
    status: str,
    output_files: list[Path],
    expected_extension: str | None = None,
    stderr: str = "",
    fallback_used: bool = False,
    backend_result: dict[str, Any] | None = None,
    ffprobe_results: list[dict[str, Any]] | None = None,
    attempts: list[dict[str, Any]] | None = None,
    sniffed_media: dict[str, Any] | None = None,
    archive_files: list[Path] | None = None,
    archive_expected: bool = False,
) -> dict[str, Any]:
    checks = [
        _output_files_check(output_files),
        _archive_files_check(archive_files or [], archive_expected=archive_expected),
        _return_code_check(status),
        _extension_check(output_files, expected_extension),
        _output_type_check(output_files, backend_result),
        _stderr_warning_check(stderr),
        _attempt_error_check(attempts or []),
        _fallback_check(fallback_used),
        _timeout_check(attempts or []),
        _sniffed_media_check(sniffed_media),
        _backend_result_check(backend_result),
        _ffprobe_check(ffprobe_results or [], output_files_present=bool(output_files)),
        downloaded_completeness_check(
            sniffed_media=sniffed_media,
            ffprobe_results=ffprobe_results or [],
        ),
    ]

    review = {
        "status": status,
        "needs_review": True,
        "checks": checks,
        "backend_result": backend_result or {},
        "ffprobe": ffprobe_results or [],
        "attempts": redact_attempts(attempts or []),
        "sniffed_media": redact_sniffed_media(sniffed_media) if sniffed_media else {},
        "archive_files": [_file_payload(path) for path in archive_files or []],
        "manual_review_required": True,
        "next_action": "Review output files and checks before accepting the task.",
        "created_at": datetime.now(UTC).isoformat(),
    }
    review.update(quality_outcome(review))
    return review


def quality_outcome(review: dict[str, Any]) -> dict[str, str]:
    checks = [check for check in review.get("checks", []) if isinstance(check, dict)]
    legacy = _legacy_quality_outcome(checks)
    transport = _axis_outcome(
        checks,
        check_names=TRANSPORT_CHECK_NAMES,
        prefix="transport",
        labels={
            "pass": "下载传输完成",
            "warn": "下载传输存在警告",
            "fail": "下载传输失败",
        },
        next_actions={
            "pass": "传输层未发现异常；继续检查媒体完整性。",
            "warn": "已使用降级路径或存在后端警告；请结合媒体完整性结果判断文件。",
            "fail": "请检查后端错误和超时记录后重试下载。",
        },
    )
    media_integrity = _axis_outcome(
        checks,
        check_names=MEDIA_INTEGRITY_CHECK_NAMES,
        prefix="media_integrity",
        labels={
            "pass": "媒体完整性初检通过",
            "warn": "媒体完整性仍需确认",
            "fail": "媒体完整性检查失败",
        },
        next_actions={
            "pass": "请人工播放确认时长和内容后再标记通过。",
            "warn": "请检查可播放性、时长和内容完整性。",
            "fail": "不要把该文件当作完整结果；请重新捕获或下载。",
        },
    )
    return {**legacy, **transport, **media_integrity}


def _legacy_quality_outcome(checks: list[dict[str, Any]]) -> dict[str, str]:
    if any(check.get("status") == "failed" for check in checks):
        return {
            "quality_status": "fail",
            "quality_label": "完整性检查失败",
            "quality_next_action": "不要把该文件当作成功结果；请重新捕获主视频并下载。",
        }
    if any(check.get("status") == "warning" for check in checks):
        return {
            "quality_status": "warn",
            "quality_label": "下载完成，仍需人工确认",
            "quality_next_action": "请检查可播放性、时长和内容完整性后再标记通过。",
        }
    return {
        "quality_status": "pass",
        "quality_label": "完整性初检通过",
        "quality_next_action": "请人工播放确认后标记通过。",
    }


def _axis_outcome(
    checks: list[dict[str, Any]],
    *,
    check_names: set[str],
    prefix: str,
    labels: dict[str, str],
    next_actions: dict[str, str],
) -> dict[str, str]:
    selected = [check for check in checks if str(check.get("name") or "") in check_names]
    if any(check.get("status") == "failed" for check in selected):
        status = "fail"
    elif (
        not any(check.get("status") == "ok" for check in selected)
        or any(check.get("status") not in {"ok", "skipped"} for check in selected)
    ):
        status = "warn"
    else:
        status = "pass"
    return {
        f"{prefix}_status": status,
        f"{prefix}_label": labels[status],
        f"{prefix}_next_action": next_actions[status],
    }


def write_quality_review(report_dir: Path, review: dict[str, Any]) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / "review-checklist.json"
    serializable = dict(review)
    if isinstance(serializable.get("sniffed_media"), dict):
        serializable["sniffed_media"] = redact_sniffed_media(serializable["sniffed_media"])
    if isinstance(serializable.get("attempts"), list):
        serializable["attempts"] = redact_attempts(serializable["attempts"])
    path.write_text(
        json.dumps(serializable, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return path


def _output_files_check(output_files: list[Path]) -> dict[str, Any]:
    if not output_files:
        return {
            "name": "output_files",
            "status": "warning",
            "message": "no output files were detected",
            "files": [],
        }

    files = []
    for path in output_files:
        try:
            exists = path.is_file()
            files.append({"path": path.as_posix(), "exists": exists,
                          "size": path.stat().st_size if exists else None})
        except OSError:
            files.append({"path": path.as_posix(), "exists": False, "size": None, "error": "unreadable"})
    if any(file["exists"] and (file["size"] or 0) > 0 for file in files):
        status = "ok"
        message = "output files detected"
    else:
        status = "warning"
        message = "output files were listed but are missing or empty"

    return {
        "name": "output_files",
        "status": status,
        "message": message,
        "files": files,
    }


def _output_type_check(output_files: list[Path], backend_result: dict[str, Any] | None) -> dict[str, Any]:
    """Inspect at most 8 KiB per output; signatures establish type, not decodability."""
    records = []
    reported = backend_result or {}
    aliases = ({"mp4", "m4v", "m4a", "m4b", "mov"}, {"ogg", "oga", "opus"}, {"webm", "weba"}, {"aif", "aiff"})
    for path in output_files:
        record: dict[str, Any] = {"path": path.as_posix(), "status": "warning", "reason": "signature_unknown"}
        try:
            if not path.is_file() or path.stat().st_size == 0:
                record.update(status="failed", reason="output_missing_or_empty")
            else:
                with path.open("rb") as stream:
                    prefix = stream.read(8192)
                text = prefix.decode("utf-8-sig", errors="replace").lstrip().lower()
                if text.startswith(("<!doctype html", "<html", "<?xml", "{", "[")):
                    record.update(status="failed", reason="text_or_error_response_not_media")
                elif filetype is None:
                    record["reason"] = "signature_library_unavailable"
                else:
                    detected = filetype.guess(prefix)
                    if detected:
                        extension = path.suffix.lower().lstrip(".")
                        compatible = extension == detected.extension or any(
                            extension in group and detected.extension in group for group in aliases
                        )
                        record.update(detected_mime=detected.mime, detected_extension=detected.extension)
                        if not detected.mime.startswith(("video/", "audio/")):
                            record.update(status="failed", reason="non_media_signature")
                        elif not compatible:
                            record.update(status="failed", reason="extension_signature_mismatch")
                        else:
                            record.update(status="ok", reason="media_signature_matches_extension")
                        # A single backend MIME belongs to its named output, never every merged file.
                        if reported.get("output_file") and Path(str(reported["output_file"])) == path:
                            mime = str(reported.get("content_type") or "").split(";", 1)[0].strip().lower()
                            generic = {"", "application/octet-stream", "binary/octet-stream"}
                            if record["status"] == "ok" and mime not in generic and mime != detected.mime:
                                record.update(status="warning" if mime.startswith(("audio/", "video/")) else "failed",
                                              reason="reported_mime_signature_mismatch")
        except (OSError, ValueError, IndexError):
            record.update(status="failed", reason="output_unreadable_or_malformed")
        records.append(record)
    status = "skipped" if not records else "ok"
    if any(item["status"] == "failed" for item in records):
        status = "failed"
    elif any(item["status"] == "warning" for item in records):
        status = "warning"
    return {"name": "output_type", "status": status, "files": records,
            "message": "文件类型初检；魔数通过不代表媒体可完整播放。", "read_limit_bytes": 8192}


def _return_code_check(status: str) -> dict[str, Any]:
    return {
        "name": "backend_status",
        "status": "ok" if status in {"ok", "finished"} else "failed",
        "message": f"backend status is {status}",
    }


def _extension_check(output_files: list[Path], expected_extension: str | None) -> dict[str, Any]:
    if not expected_extension:
        return {
            "name": "expected_extension",
            "status": "skipped",
            "message": "no expected extension was provided",
        }

    normalized = expected_extension.lower().lstrip(".")
    mismatches = [path.as_posix() for path in output_files if path.suffix.lower().lstrip(".") != normalized]
    return {
        "name": "expected_extension",
        "status": "ok" if not mismatches else "warning",
        "message": "all output files match expected extension" if not mismatches else "some output files do not match expected extension",
        "expected_extension": normalized,
        "mismatches": mismatches,
    }


def _stderr_warning_check(stderr: str) -> dict[str, Any]:
    warning_count = sum(1 for line in stderr.splitlines() if "warning" in line.lower())
    return {
        "name": "stderr_warnings",
        "status": "ok" if warning_count == 0 else "warning",
        "message": f"{warning_count} warning line(s) detected in stderr",
        "warning_count": warning_count,
    }


def _attempt_error_check(attempts: list[dict[str, Any]]) -> dict[str, Any]:
    errors = [
        {
            "backend": attempt.get("backend"),
            "error_type": attempt.get("error_type"),
            "error_label": attempt.get("error_label"),
            "next_action": attempt.get("next_action"),
        }
        for attempt in attempts
        if attempt.get("error_type")
    ]
    return {
        "name": "backend_attempt_errors",
        "status": "warning" if errors else "ok",
        "message": f"{len(errors)} classified backend error(s) detected" if errors else "no classified backend errors detected",
        "errors": errors,
    }


def _fallback_check(fallback_used: bool) -> dict[str, Any]:
    return {
        "name": "fallback_used",
        "status": "warning" if fallback_used else "ok",
        "message": "fallback backend was used" if fallback_used else "no fallback backend was used",
        "fallback_used": fallback_used,
    }


def _timeout_check(attempts: list[dict[str, Any]]) -> dict[str, Any]:
    timed_out = [attempt for attempt in attempts if attempt.get("timed_out") or attempt.get("timeout")]
    return {
        "name": "backend_timeouts",
        "status": "warning" if timed_out else "ok",
        "message": f"{len(timed_out)} backend attempt(s) timed out" if timed_out else "no backend timeouts detected",
        "timed_out": [
            {
                "backend": attempt.get("backend"),
                "timeout_kind": attempt.get("timeout_kind") or attempt.get("timeout"),
                "duration_seconds": attempt.get("duration_seconds"),
                "hard_timeout_seconds": attempt.get("hard_timeout_seconds"),
                "idle_timeout_seconds": attempt.get("idle_timeout_seconds"),
            }
            for attempt in timed_out
        ],
    }


def _sniffed_media_check(sniffed_media: dict[str, Any] | None) -> dict[str, Any]:
    if not sniffed_media:
        return {
            "name": "sniffed_media",
            "status": "skipped",
            "message": "task was not created from a browser-sniffed capture",
        }
    has_headers = bool(sniffed_media.get("request_headers"))
    diagnostic = (
        sniffed_media.get("completeness")
        if isinstance(sniffed_media.get("completeness"), dict)
        else sniffed_completeness_diagnostic(sniffed_media)
    )
    risk = str(diagnostic.get("risk") or "unknown")
    warning = risk not in {"likely_complete"}
    return {
        "name": "sniffed_media",
        "status": "warning" if warning else "ok",
        "message": diagnostic.get("label") or "browser-sniffed capture recorded",
        "media_type": sniffed_media.get("media_type"),
        "media_kind": sniffed_media.get("media_kind"),
        "category": sniffed_media.get("category"),
        "backend": sniffed_media.get("backend"),
        "has_request_headers": has_headers,
        "quality_score": sniffed_media.get("quality_score"),
        "download_candidate_reason": sniffed_media.get("download_candidate_reason"),
        "risk": risk,
        "range_kind": diagnostic.get("range_kind"),
        "resource_role": diagnostic.get("resource_role"),
    }


def parse_backend_stdout_json(stdout: str) -> dict[str, Any] | None:
    text = stdout.strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            payload = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return payload if isinstance(payload, dict) else None


def discover_output_files(output_dir: Path, backend_result: dict[str, Any] | None = None) -> list[Path]:
    files: list[Path] = []
    if backend_result and isinstance(backend_result.get("output_file"), str):
        files.append(Path(backend_result["output_file"]))
    if backend_result and isinstance(backend_result.get("output_files"), list):
        files.extend(Path(path) for path in backend_result["output_files"] if isinstance(path, str) and path)
        files = list(dict.fromkeys(files))
    if not files:
        for path in output_dir.glob("*"):
            if path.is_file() and path.suffix.lower() in {
                ".aac",
                ".aif",
                ".aiff",
                ".flac",
                ".m4a",
                ".m4b",
                ".mkv",
                ".mov",
                ".mp3",
                ".mp4",
                ".oga",
                ".ogg",
                ".opus",
                ".ts",
                ".wav",
                ".weba",
                ".webm",
                ".wma",
            }:
                files.append(path)
    return files


def discover_archive_files(output_dir: Path) -> list[Path]:
    suffixes = {
        ".description",
        ".info.json",
        ".json",
        ".srt",
        ".srv1",
        ".srv2",
        ".srv3",
        ".ttml",
        ".vtt",
    }
    return sorted(path for path in output_dir.glob("*") if path.is_file() and any(path.name.endswith(suffix) for suffix in suffixes))


def classify_backend_error(stderr: str) -> dict[str, str] | None:
    lowered = stderr.lower()
    if "http error 412" in lowered or "status code 412" in lowered or "412 precondition failed" in lowered:
        return {
            "error_type": "http_precondition_failed",
            "error_label": "站点拒绝当前请求（412）",
            "next_action": "停止本次同站重试，稍后重试或使用授权浏览器捕获。",
        }
    if "fresh cookies are needed" in lowered:
        return {
            "error_type": "browser_session_required",
            "error_label": "站点要求新的浏览器会话授权",
            "next_action": "请在已登录浏览器中打开并播放视频，由 browser-sniffer 捕获真实媒体请求；不需要导出 Cookie。",
        }
    if "failed to decrypt with dpapi" in lowered or "app-bound" in lowered:
        return {
            "error_type": "browser_cookie_decryption_unavailable",
            "error_label": "Chrome 登录态无法由下载器解密",
            "next_action": "不要继续复制 Chrome Cookie 数据库。请使用已登录浏览器播放视频并通过 browser-sniffer 捕获。",
        }
    if "could not copy chrome cookie database" in lowered:
        return {
            "error_type": "chrome_cookie_database_locked",
            "error_label": "Chrome Cookie 数据库无法复制",
            "next_action": "优先保持浏览器登录并通过 browser-sniffer 捕获；如明确要读取 Cookie，再完全退出 Chrome 后重试。",
        }
    if "sign in to confirm" in lowered and "not a bot" in lowered:
        return {
            "error_type": "youtube_requires_login_or_cookies",
            "error_label": "YouTube 要求登录或真人校验",
            "next_action": "请显式授权 --cookies-from-browser，或提供导出的 cookies.txt 后重试。",
        }
    if "winerror 2" in lowered or "the system cannot find the file specified" in lowered:
        return {
            "error_type": "backend_command_missing",
            "error_label": "下载后端命令不存在",
            "next_action": "安装缺失后端，或忽略该可选 fallback。",
        }
    return None


def probe_media_files(output_files: list[Path]) -> list[dict[str, Any]]:
    ffprobe_path = shutil.which("ffprobe")
    if not output_files or ffprobe_path is None:
        return []
    return [_probe_media_file(path, ffprobe_path) for path in output_files]


def _probe_media_file(path: Path, ffprobe_path: str) -> dict[str, Any]:
    command = [
        ffprobe_path,
        "-v",
        "error",
        "-show_entries",
        "format=duration,size,format_name",
        "-show_entries",
        "stream=index,codec_type,codec_name,width,height,r_frame_rate",
        "-of",
        "json",
        path.as_posix(),
    ]
    completed = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    payload: dict[str, Any] = {
        "path": path.as_posix(),
        "returncode": completed.returncode,
    }
    if completed.returncode == 0:
        try:
            payload["metadata"] = json.loads(completed.stdout)
        except json.JSONDecodeError:
            payload["error"] = "ffprobe returned non-json output"
    else:
        payload["error"] = completed.stderr.strip()
    return payload


def _backend_result_check(backend_result: dict[str, Any] | None) -> dict[str, Any]:
    if not backend_result:
        return {
            "name": "backend_stdout_json",
            "status": "skipped",
            "message": "backend stdout did not contain structured JSON",
        }
    size_matches = backend_result.get("size_matches_page")
    status = "ok"
    message = "backend stdout JSON parsed"
    if size_matches is False:
        status = "warning"
        message = "backend reported output size does not match page metadata"
    return {
        "name": "backend_stdout_json",
        "status": status,
        "message": message,
        "size_matches_page": size_matches,
        "output_file": backend_result.get("output_file"),
    }


def _ffprobe_check(ffprobe_results: list[dict[str, Any]], *, output_files_present: bool) -> dict[str, Any]:
    if not ffprobe_results:
        return {
            "name": "ffprobe",
            "status": "warning" if output_files_present else "skipped",
            "message": "ffprobe was unavailable or no media files were detected",
        }
    failed = [item for item in ffprobe_results if item.get("returncode") != 0]
    return {
        "name": "ffprobe",
        "status": "ok" if not failed else "failed",
        "message": "ffprobe metadata extracted" if not failed else "some media files failed ffprobe validation",
        "files_checked": len(ffprobe_results),
        "failed": failed,
    }


def _archive_files_check(archive_files: list[Path], *, archive_expected: bool) -> dict[str, Any]:
    if not archive_files:
        return {
            "name": "archive_sidecars",
            "status": "warning" if archive_expected else "skipped",
            "message": "no subtitle, description, or info.json sidecar files were detected"
            if archive_expected
            else "archive sidecar files are only expected for metadata-capable backends",
            "files": [],
        }
    return {
        "name": "archive_sidecars",
        "status": "ok",
        "message": "subtitle, description, or metadata sidecar files detected",
        "files": [_file_payload(path) for path in archive_files],
    }


def _file_payload(path: Path) -> dict[str, Any]:
    return {
        "path": path.as_posix(),
        "exists": path.exists(),
        "size": path.stat().st_size if path.exists() else None,
    }
