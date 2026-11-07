from __future__ import annotations

import json
import struct
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "config" / "video_orchestrator.json"


def default_endpoint() -> str:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    web = config["web"]
    sniffer = config["browser_sniffer"]
    return f"{web['scheme']}://{web['host']}:{web['port']}{sniffer['plan_path']}"


def read_message() -> dict[str, Any] | None:
    raw_length = sys.stdin.buffer.read(4)
    if not raw_length:
        return None
    message_length = struct.unpack("<I", raw_length)[0]
    raw_message = sys.stdin.buffer.read(message_length)
    return json.loads(raw_message.decode("utf-8"))


def write_message(message: dict[str, Any]) -> None:
    encoded = json.dumps(message, ensure_ascii=False).encode("utf-8")
    sys.stdout.buffer.write(struct.pack("<I", len(encoded)))
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()


def post_json(endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        body = response.read().decode("utf-8")
    return json.loads(body)


def handle_message(message: dict[str, Any]) -> dict[str, Any]:
    action = message.get("action")
    endpoint = str(message.get("endpoint") or default_endpoint())
    if action == "ping":
        return {"ok": True, "message": "native host ready"}
    if action == "plan_sniffed_media":
        payload = message.get("payload")
        if not isinstance(payload, dict):
            return {"ok": False, "error": "payload must be an object"}
        try:
            return {"ok": True, "response": post_json(endpoint, payload)}
        except urllib.error.HTTPError as exc:
            return {"ok": False, "status": exc.code, "error": exc.read().decode("utf-8", errors="replace")}
        except OSError as exc:
            return {"ok": False, "error": str(exc)}
    return {"ok": False, "error": f"unsupported action: {action}"}


def main() -> None:
    message = read_message()
    if message is None:
        return
    write_message(handle_message(message))


if __name__ == "__main__":
    main()
