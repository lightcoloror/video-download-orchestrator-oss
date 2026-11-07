from __future__ import annotations

import argparse
import json
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs
from urllib.parse import urlencode
from urllib.parse import urlparse

import requests

from video_orchestrator.runtime.redaction import redact_url


DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/148.0.0.0 Safari/537.36 Edg/148.0.0.0"
)
SIGN_ENDPOINT = "https://meeting.tencent.com/wemeet-cloudrecording-webapi/v1/sign"


@dataclass(frozen=True)
class TencentMeetingContext:
    page_url: str
    share_id: str
    recording_share_id: str
    meeting_id: str | None
    recording_id: str | None
    expected_size: int | None
    allow_download: bool | None


def build_tencent_meeting_command(url: str, output_dir: Path) -> list[str]:
    return [
        "python",
        "-m",
        "video_orchestrator.backends.tencent_meeting",
        url,
        "--output-dir",
        output_dir.as_posix(),
    ]


def download_tencent_meeting_share(
    page_url: str,
    output_dir: Path,
    *,
    user_agent: str = DEFAULT_USER_AGENT,
    timeout: tuple[int, int] = (30, 120),
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    headers = {"User-Agent": user_agent, "Referer": page_url}

    page_response = session.get(page_url, headers=headers, timeout=timeout[0])
    page_response.raise_for_status()
    context = extract_tencent_meeting_context(page_url, page_response.text)

    media_url = fetch_signed_media_url(session, context, user_agent=user_agent, timeout=timeout[0])
    output_file = stream_download(session, media_url, output_dir / "download.mp4", headers=headers, timeout=timeout)

    size = output_file.stat().st_size
    return {
        "status": "finished",
        "backend": "tencent-meeting",
        "page_url": page_url,
        "recording_share_id": context.recording_share_id,
        "meeting_id": context.meeting_id,
        "recording_id": context.recording_id,
        "allow_download": context.allow_download,
        "expected_size": context.expected_size,
        "output_file": str(output_file),
        "bytes": size,
        "size_matches_page": context.expected_size is None or context.expected_size == size,
        "redacted_media_url": redact_url(media_url),
    }


def extract_tencent_meeting_context(page_url: str, html: str) -> TencentMeetingContext:
    share_id = _query_value(page_url, "id")
    if not share_id:
        raise ValueError("Tencent Meeting share URL must include id query parameter")

    server_data = _extract_server_data(html)
    recordings = server_data.get("recordings")
    if not isinstance(recordings, list) or not recordings:
        raise ValueError("Tencent Meeting page did not include recordings data")
    recording = recordings[0]
    recording_share_id = str(recording.get("sharing_id") or "")
    if not recording_share_id:
        raise ValueError("Tencent Meeting page did not include recording sharing_id")

    meeting_info = server_data.get("meeting_info") if isinstance(server_data.get("meeting_info"), dict) else {}
    return TencentMeetingContext(
        page_url=page_url,
        share_id=share_id,
        recording_share_id=recording_share_id,
        meeting_id=_optional_str(meeting_info.get("meeting_id")),
        recording_id=_optional_str(recording.get("id")),
        expected_size=_optional_int(server_data.get("total_recording_size") or recording.get("size")),
        allow_download=server_data.get("allow_download") if isinstance(server_data.get("allow_download"), bool) else None,
    )


def fetch_signed_media_url(
    session: requests.Session,
    context: TencentMeetingContext,
    *,
    user_agent: str = DEFAULT_USER_AGENT,
    timeout: int = 30,
) -> str:
    nonce = secrets.token_urlsafe(6).replace("-", "")[:9]
    params = {
        "c_app_id": "",
        "c_os_model": "web",
        "c_os": "web",
        "c_os_version": user_agent,
        "c_timestamp": str(int(time.time() * 1000)),
        "c_nonce": nonce,
        "c_app_version": "",
        "c_instance_id": "5",
        "rnds": nonce,
        "c_district": "0",
        "platform": "Web",
        "c_app_uid": "",
        "c_account_corp_id": "",
        "trace-id": secrets.token_hex(16),
        "id": context.recording_share_id,
        "source": "shares",
        "pwd": "",
        "activity_uid": "",
        "tk": "",
        "sharing_id": context.share_id,
        "need_multi_stream": "1",
        "enter_from": "share",
        "c_lang": "zh-CN",
    }
    response = session.get(
        f"{SIGN_ENDPOINT}?{urlencode(params)}",
        headers={"User-Agent": user_agent, "Referer": context.page_url},
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") != 0:
        raise ValueError(f"Tencent Meeting sign API returned code={payload.get('code')}: {payload.get('msg')}")
    media_url = payload.get("signurl") or (payload.get("data") or {}).get("signurl")
    if not isinstance(media_url, str) or not media_url:
        raise ValueError("Tencent Meeting sign API did not return signurl")
    return media_url


def stream_download(
    session: requests.Session,
    url: str,
    output_file: Path,
    *,
    headers: dict[str, str],
    timeout: tuple[int, int] = (30, 120),
) -> Path:
    part_file = output_file.with_suffix(output_file.suffix + ".part")
    request_headers = dict(headers)
    existing = part_file.stat().st_size if part_file.exists() else 0
    if existing:
        request_headers["Range"] = f"bytes={existing}-"

    with session.get(url, headers=request_headers, timeout=timeout, stream=True) as response:
        if response.status_code == 416 and part_file.exists():
            part_file.replace(output_file)
            return output_file
        response.raise_for_status()
        mode = "ab" if existing and response.status_code == 206 else "wb"
        with part_file.open(mode) as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    handle.write(chunk)
    part_file.replace(output_file)
    return output_file


def _extract_server_data(html: str) -> dict[str, Any]:
    marker = '"serverData":'
    start = html.find(marker)
    escaped = False
    if start < 0:
        marker = '\\"serverData\\":'
        start = html.find(marker)
        escaped = True
    if start < 0:
        raise ValueError("Tencent Meeting page did not include serverData")
    object_start = html.find("{", start + len(marker))
    if object_start < 0:
        raise ValueError("Tencent Meeting serverData was not an object")
    boundary = html.find(',\\"appUid\\"', object_start) if escaped else html.find(',"appUid"', object_start)
    if boundary > object_start:
        candidate = html[object_start:boundary]
        if escaped:
            candidate = candidate.replace('\\"', '"')
        return json.loads(candidate)

    depth = 0
    in_string = False
    escaped = False
    for index in range(object_start, len(html)):
        char = html[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                candidate = html[object_start : index + 1]
                if escaped:
                    candidate = candidate.replace('\\"', '"')
                return json.loads(candidate)
    raise ValueError("Tencent Meeting serverData object was not closed")


def _query_value(url: str, name: str) -> str | None:
    values = parse_qs(urlparse(url).query).get(name)
    return values[0] if values else None


def _optional_str(value: Any) -> str | None:
    return str(value) if value not in {None, ""} else None


def _optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Download a Tencent Meeting recording share URL.")
    parser.add_argument("url")
    parser.add_argument("--output-dir", type=Path, default=Path("downloads"))
    args = parser.parse_args()
    result = download_tencent_meeting_share(args.url, args.output_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
