import importlib.util
from pathlib import Path

from video_orchestrator.runtime.config import sniffer_plan_endpoint


def load_native_host():
    path = Path(__file__).parents[1] / "integrations" / "native-messaging" / "native_host.py"
    spec = importlib.util.spec_from_file_location("native_host", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_native_host_ping():
    native_host = load_native_host()

    assert native_host.handle_message({"action": "ping"}) == {"ok": True, "message": "native host ready"}


def test_native_host_rejects_missing_payload():
    native_host = load_native_host()

    assert native_host.handle_message({"action": "plan_sniffed_media"}) == {"ok": False, "error": "payload must be an object"}


def test_native_host_default_endpoint_reads_project_config():
    native_host = load_native_host()

    assert native_host.default_endpoint() == sniffer_plan_endpoint()
