from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from video_orchestrator.runtime.quality import quality_outcome


def artifact_map(artifacts: list[dict]) -> dict[str, str]:
    return {artifact["kind"]: artifact["path"] for artifact in artifacts}


def read_json_artifact(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    artifact_path = Path(path)
    if not artifact_path.exists():
        return {}
    try:
        payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def review_verdict(task: dict, review: dict) -> str:
    if task.get("status") not in {"finished", "accepted"}:
        return "fail"
    checks = review.get("checks") if isinstance(review.get("checks"), list) else []
    warning_checks = [check for check in checks if isinstance(check, dict) and check.get("status") == "warning"]
    failed_checks = [check for check in checks if isinstance(check, dict) and check.get("status") == "failed"]
    backend_result = review.get("backend_result") if isinstance(review.get("backend_result"), dict) else {}
    if failed_checks or backend_result.get("size_matches_page") is False:
        return "fail"
    if warning_checks:
        return "warn"
    return "pass"


def _review_checks(review: dict) -> list[dict[str, Any]]:
    checks = review.get("checks") if isinstance(review.get("checks"), list) else []
    return [check for check in checks if isinstance(check, dict)]


def _has_range_warning(check: dict[str, Any], review: dict) -> bool:
    message = str(check.get("message") or check.get("detail") or "").lower()
    if "range" in message:
        return True
    sniffed_media = review.get("sniffed_media") if isinstance(review.get("sniffed_media"), dict) else {}
    if sniffed_media.get("content_range"):
        return True
    backend_result = review.get("backend_result") if isinstance(review.get("backend_result"), dict) else {}
    return bool(backend_result.get("content_range"))


def _reason_for_check(check: dict[str, Any], review: dict) -> str:
    name = str(check.get("name") or "unknown")
    status = str(check.get("status") or "")
    if status == "failed":
        if name == "output_files":
            return "未检测到有效输出文件"
        if name == "ffprobe":
            return "媒体探测失败"
        return f"检查项失败：{name}"
    if status == "warning":
        if name == "backend_attempt_errors":
            errors = check.get("errors") if isinstance(check.get("errors"), list) else []
            labels = []
            for error in errors:
                if not isinstance(error, dict) or not error.get("error_label"):
                    continue
                label = str(error["error_label"])
                if label not in labels:
                    labels.append(label)
            return "；".join(labels) if labels else "后端错误需要处理"
        if name == "sniffed_media" and _has_range_warning(check, review):
            return "捕获到 Range 分片"
        if name == "media_completeness":
            label = str(check.get("quality_label") or "")
            reasons = check.get("risk_reasons") if isinstance(check.get("risk_reasons"), list) else []
            if label:
                return label
            if reasons:
                return "；".join(str(reason) for reason in reasons)
            return "媒体完整性需要人工确认"
        if name == "ffprobe":
            return "缺少或无法完成媒体探测"
        if name == "output_files":
            return "输出文件需要人工确认"
        return f"检查项警告：{name}"
    if status == "ok" and name == "output_files":
        return "输出文件检查通过"
    return ""


def _quality_reason_labels(task: dict, review: dict, verdict: str) -> list[str]:
    reasons: list[str] = []
    status = task.get("status")
    if status == "retryable":
        reasons.append("等待页面嗅探恢复")
    elif status not in {"finished", "accepted"}:
        reasons.append("任务未完成或下载失败")

    for check in _review_checks(review):
        reason = _reason_for_check(check, review)
        if reason:
            reasons.append(reason)

    backend_result = review.get("backend_result") if isinstance(review.get("backend_result"), dict) else {}
    if backend_result.get("size_matches_page") is False:
        reasons.append("文件大小与页面信息不匹配")
    if not review and status in {"finished", "accepted"}:
        reasons.append("缺少检查清单")
    if verdict == "pass" and not reasons:
        reasons.append("输出文件检查通过")

    deduped: list[str] = []
    for reason in reasons:
        if reason and reason not in deduped:
            deduped.append(reason)
    return deduped


def _recommended_actions(quality_status: str, can_accept: bool, can_rerun: bool, can_resume: bool) -> list[str]:
    actions: list[str] = []
    if quality_status == "pass" and can_accept:
        actions.append("人工确认后标记通过")
    if quality_status == "warn" and can_accept:
        actions.append("人工确认输出文件")
        actions.append("确认无误后标记通过")
    if can_resume:
        actions.append("页面嗅探恢复")
    if can_rerun:
        actions.append("重新执行")
    if quality_status == "fail" and not actions:
        actions.append("查看报告和日志后重新处理")
    return actions


def _quality_next_action(quality_status: str, actions: list[str]) -> str:
    if quality_status == "pass":
        return "质量检查通过。请人工确认输出文件可播放后点击标记通过。"
    if quality_status == "warn":
        return "质量检查存在警告。请人工确认输出文件；如不可靠，可重新执行或页面嗅探恢复。"
    if actions:
        return f"质量检查失败。建议先执行：{actions[0]}。"
    return "质量检查失败。请查看报告和日志后重新处理。"


def operator_review_summary(task: dict, artifacts: dict[str, str]) -> dict[str, Any]:
    review = read_json_artifact(artifacts.get("review_checklist"))
    summary = read_json_artifact(artifacts.get("summary"))
    verdict = review_verdict(task, review)
    quality_status = verdict
    quality_label = {
        "pass": "可标记通过",
        "warn": "需要人工确认",
        "fail": "需要恢复",
    }[quality_status]
    axis_outcomes = quality_outcome(review) if review else {}
    transport_status = str(review.get("transport_status") or axis_outcomes.get("transport_status") or quality_status)
    media_integrity_status = str(
        review.get("media_integrity_status") or axis_outcomes.get("media_integrity_status") or quality_status
    )
    task_status = str(task.get("status") or "")
    backend = str(task.get("backend") or "")
    can_accept = quality_status in {"pass", "warn"} and task_status != "accepted"
    can_resume = task_status == "retryable" or backend == "browser-sniffer"
    can_rerun = quality_status in {"warn", "fail"} and task_status in {"finished", "failed"}
    reason_labels = _quality_reason_labels(task, review, verdict)
    recommended_actions = _recommended_actions(quality_status, can_accept, can_rerun, can_resume)
    return {
        "verdict": verdict,
        "quality_status": quality_status,
        "quality_label": quality_label,
        "transport_status": transport_status,
        "transport_label": str(
            review.get("transport_label") or axis_outcomes.get("transport_label") or quality_label
        ),
        "media_integrity_status": media_integrity_status,
        "media_integrity_label": str(
            review.get("media_integrity_label") or axis_outcomes.get("media_integrity_label") or quality_label
        ),
        "reason_labels": reason_labels,
        "recommended_actions": recommended_actions,
        "can_accept": can_accept,
        "can_rerun": can_rerun,
        "can_resume": can_resume,
        "review": review,
        "summary": summary,
        "next_action": _quality_next_action(quality_status, recommended_actions),
    }
