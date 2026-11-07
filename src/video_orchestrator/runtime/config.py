from __future__ import annotations

import ipaddress
import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


DEFAULT_CONFIG_RELATIVE_PATH = Path("config") / "video_orchestrator.json"
CONFIG_ENV_VAR = "VIDEO_ORCHESTRATOR_CONFIG"


def project_root() -> Path:
    checkout = Path(__file__).resolve().parents[3]
    return checkout if (checkout / "pyproject.toml").is_file() else Path.cwd()


def project_asset(relative: str | Path) -> Path:
    local = project_root() / relative
    if local.is_file():
        return local
    return Path(__file__).resolve().parents[1] / "assets" / relative


def config_path() -> Path:
    override = os.environ.get(CONFIG_ENV_VAR)
    if override:
        return Path(override).expanduser().resolve()
    return project_asset(DEFAULT_CONFIG_RELATIVE_PATH)


@lru_cache(maxsize=1)
def load_config() -> dict[str, Any]:
    path = config_path()
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def web_config() -> dict[str, Any]:
    return dict(load_config().get("web") or {})


def browser_sniffer_config() -> dict[str, Any]:
    return dict(load_config().get("browser_sniffer") or {})


def execution_config() -> dict[str, Any]:
    return dict(load_config().get("execution") or {})


def runtime_config() -> dict[str, Any]:
    return dict(load_config().get("runtime") or {})


def runtime_state_dir() -> Path:
    value = str(runtime_config().get("state_dir") or ".vdo")
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = project_root() / path
    return path.resolve()


def network_config() -> dict[str, Any]:
    return dict(load_config().get("network") or {})


def site_probe_config() -> dict[str, Any]:
    return dict(network_config().get("site_probe") or {})


def external_uis_config() -> dict[str, Any]:
    return dict(load_config().get("external_uis") or {})


def mediago_config() -> dict[str, Any]:
    external = external_uis_config()
    return dict(external.get("mediago") or {})


def wechat_channels_config() -> dict[str, Any]:
    return dict(load_config().get("wechat_channels") or {})


def web_scheme() -> str:
    return str(web_config()["scheme"])


def web_host() -> str:
    return str(web_config()["host"])


def web_port() -> int:
    return int(web_config()["port"])


def api_base_url() -> str:
    return f"{web_scheme()}://{web_host()}:{web_port()}"


def sniffer_plan_path() -> str:
    return str(browser_sniffer_config()["plan_path"])


def sniffer_plan_endpoint() -> str:
    return api_base_url().rstrip("/") + sniffer_plan_path()


def sniffer_output_dir() -> str:
    return str(browser_sniffer_config()["output_dir"])


def hard_timeout_seconds() -> int:
    return int(execution_config()["hard_timeout_seconds"])


def idle_timeout_seconds() -> int:
    return int(execution_config()["idle_timeout_seconds"])


def browser_sniffer_generated_config_path() -> Path:
    return project_asset("integrations/browser-sniffer/orchestrator-config.js")


def browser_sniffer_manifest_path() -> Path:
    return project_asset("integrations/browser-sniffer/manifest.json")


def agent_discovery_json_path() -> Path:
    return project_asset("integrations/agent-discovery.json")


def check_config() -> dict[str, Any]:
    checks = [
        _config_file_check(),
        _web_config_check(),
        _browser_sniffer_config_check(),
        _execution_config_check(),
        _runtime_config_check(),
        _site_probe_config_check(),
        _wechat_channels_config_check(),
        _browser_sniffer_generated_config_check(),
        _browser_sniffer_manifest_check(),
        _agent_discovery_json_check(),
    ]
    ok = all(check["status"] == "ok" for check in checks)
    return {
        "status": "ok" if ok else "failed",
        "config_path": str(config_path()),
        "api_base_url": api_base_url() if ok else None,
        "sniffer_plan_endpoint": sniffer_plan_endpoint() if ok else None,
        "checks": checks,
    }


def _config_file_check() -> dict[str, Any]:
    path = config_path()
    if not path.exists():
        return {"name": "config_file", "status": "failed", "message": f"missing config file: {path}"}
    try:
        load_config()
    except (OSError, json.JSONDecodeError) as exc:
        return {"name": "config_file", "status": "failed", "message": str(exc)}
    return {"name": "config_file", "status": "ok", "path": str(path)}


def _web_config_check() -> dict[str, Any]:
    try:
        scheme = web_scheme()
        host = web_host()
        port = web_port()
    except (KeyError, TypeError, ValueError) as exc:
        return {"name": "web_config", "status": "failed", "message": str(exc)}
    if scheme not in {"http", "https"}:
        return {"name": "web_config", "status": "failed", "message": f"unsupported scheme: {scheme}"}
    if not host:
        return {"name": "web_config", "status": "failed", "message": "host is empty"}
    if port < 1 or port > 65535:
        return {"name": "web_config", "status": "failed", "message": f"invalid port: {port}"}
    return {"name": "web_config", "status": "ok", "api_base_url": api_base_url()}


def _browser_sniffer_config_check() -> dict[str, Any]:
    try:
        path = sniffer_plan_path()
        output_dir = sniffer_output_dir()
    except KeyError as exc:
        return {"name": "browser_sniffer_config", "status": "failed", "message": str(exc)}
    if not path.startswith("/"):
        return {"name": "browser_sniffer_config", "status": "failed", "message": "plan_path must start with /"}
    if not output_dir:
        return {"name": "browser_sniffer_config", "status": "failed", "message": "output_dir is empty"}
    return {"name": "browser_sniffer_config", "status": "ok", "sniffer_plan_endpoint": sniffer_plan_endpoint()}


def _execution_config_check() -> dict[str, Any]:
    try:
        hard_timeout = hard_timeout_seconds()
        idle_timeout = idle_timeout_seconds()
    except (KeyError, TypeError, ValueError) as exc:
        return {"name": "execution_config", "status": "failed", "message": str(exc)}
    if hard_timeout < 1:
        return {"name": "execution_config", "status": "failed", "message": "hard_timeout_seconds must be positive"}
    if idle_timeout < 1:
        return {"name": "execution_config", "status": "failed", "message": "idle_timeout_seconds must be positive"}
    if idle_timeout > hard_timeout:
        return {"name": "execution_config", "status": "failed", "message": "idle_timeout_seconds must not exceed hard_timeout_seconds"}
    return {
        "name": "execution_config",
        "status": "ok",
        "hard_timeout_seconds": hard_timeout,
        "idle_timeout_seconds": idle_timeout,
    }


def _runtime_config_check() -> dict[str, Any]:
    try:
        state_dir = runtime_state_dir()
        workspace_root = project_root().parent.resolve()
        state_dir.relative_to(workspace_root)
    except (OSError, TypeError, ValueError) as exc:
        return {"name": "runtime_config", "status": "failed", "message": str(exc)}
    if state_dir == project_root().resolve():
        return {"name": "runtime_config", "status": "failed", "message": "state_dir cannot be the project root"}
    return {"name": "runtime_config", "status": "ok", "state_dir": str(state_dir)}


def _site_probe_config_check() -> dict[str, Any]:
    config = site_probe_config()
    try:
        transport = str(config["transport"])
        proxy_url = str(config["proxy_url"])
        timeout_seconds = int(config["timeout_seconds"])
        max_redirects = int(config["max_redirects"])
        max_response_bytes = int(config["max_response_bytes"])
    except (KeyError, TypeError, ValueError) as exc:
        return {"name": "site_probe_config", "status": "failed", "message": str(exc)}
    if transport != "trusted_local_proxy":
        return {"name": "site_probe_config", "status": "failed", "message": f"unsupported transport: {transport}"}
    try:
        parsed = urlsplit(proxy_url)
        address = ipaddress.ip_address(parsed.hostname or "")
    except (ValueError, TypeError):
        return {"name": "site_probe_config", "status": "failed", "message": "proxy_url must use a loopback IP address"}
    if parsed.scheme not in {"http", "https"} or not address.is_loopback or parsed.username or parsed.password or parsed.port is None:
        return {"name": "site_probe_config", "status": "failed", "message": "proxy_url must be an unauthenticated loopback HTTP(S) proxy"}
    if timeout_seconds < 1 or max_redirects < 0 or max_response_bytes < 1024:
        return {"name": "site_probe_config", "status": "failed", "message": "site probe limits must be positive"}
    return {
        "name": "site_probe_config",
        "status": "ok",
        "transport": transport,
        "proxy_url": proxy_url,
        "timeout_seconds": timeout_seconds,
        "max_redirects": max_redirects,
        "max_response_bytes": max_response_bytes,
    }


def _wechat_channels_config_check() -> dict[str, Any]:
    config = wechat_channels_config()
    defaults = dict(config.get("defaults") or {})
    provider = str(defaults.get("provider") or "")
    transport = str(defaults.get("transport") or "")
    quality = str(defaults.get("quality") or "")
    if provider not in {"auto", "local-helper", "public-profile"}:
        return {"name": "wechat_channels_config", "status": "failed", "message": "invalid default provider"}
    if transport not in {"auto", "direct", "proxy"}:
        return {"name": "wechat_channels_config", "status": "failed", "message": "invalid default transport"}
    if quality not in {"highest", "compatible", "original", "h264", "h265"}:
        return {"name": "wechat_channels_config", "status": "failed", "message": "invalid default quality"}

    public = dict((config.get("providers") or {}).get("public-profile") or {})
    endpoint = str(public.get("endpoint") or "")
    suffixes = tuple(str(item).lower().rstrip(".") for item in public.get("allowed_host_suffixes") or ())
    try:
        parsed = urlsplit(endpoint)
        port = parsed.port
    except ValueError:
        return {"name": "wechat_channels_config", "status": "failed", "message": "invalid public provider endpoint"}
    hostname = str(parsed.hostname or "").lower().rstrip(".")
    host_allowed = any(hostname == suffix or hostname.endswith(f".{suffix}") for suffix in suffixes)
    if (
        parsed.scheme != "https"
        or not hostname
        or parsed.username
        or parsed.password
        or port not in {None, 443}
        or parsed.query
        or parsed.fragment
        or not host_allowed
    ):
        return {"name": "wechat_channels_config", "status": "failed", "message": "public provider must use allowlisted HTTPS"}
    order = public.get("transport_order") or []
    if not isinstance(order, list) or not order or any(item not in {"direct", "proxy"} for item in order):
        return {"name": "wechat_channels_config", "status": "failed", "message": "invalid public provider transport order"}
    try:
        timeout_seconds = float(public["timeout_seconds"])
        max_response_bytes = int(public["max_response_bytes"])
    except (KeyError, TypeError, ValueError) as exc:
        return {"name": "wechat_channels_config", "status": "failed", "message": str(exc)}
    if timeout_seconds <= 0 or max_response_bytes < 1024:
        return {"name": "wechat_channels_config", "status": "failed", "message": "provider limits must be positive"}
    return {
        "name": "wechat_channels_config",
        "status": "ok",
        "default_provider": provider,
        "default_transport": transport,
        "default_quality": quality,
        "start_download": bool(defaults.get("start_download")),
        "public_provider_enabled": bool(public.get("enabled")),
    }

def _browser_sniffer_generated_config_check() -> dict[str, Any]:
    path = browser_sniffer_generated_config_path()
    if not path.exists():
        return {"name": "browser_sniffer_generated_config", "status": "failed", "message": f"missing generated config: {path}"}
    content = path.read_text(encoding="utf-8")
    expected = {
        "apiBaseUrl": api_base_url(),
        "planPath": sniffer_plan_path(),
        "outputDir": sniffer_output_dir(),
    }
    missing = [f'"{key}": "{value}"' for key, value in expected.items() if f'"{key}": "{value}"' not in content]
    if missing:
        return {
            "name": "browser_sniffer_generated_config",
            "status": "failed",
            "message": "generated config is out of sync",
            "missing": missing,
        }
    return {"name": "browser_sniffer_generated_config", "status": "ok", "path": str(path)}


def _browser_sniffer_manifest_check() -> dict[str, Any]:
    path = browser_sniffer_manifest_path()
    if not path.exists():
        return {"name": "browser_sniffer_manifest", "status": "failed", "message": f"missing manifest: {path}"}
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"name": "browser_sniffer_manifest", "status": "failed", "message": str(exc)}
    permissions = manifest.get("host_permissions") or []
    if "http://127.0.0.1:*/*" not in permissions:
        return {
            "name": "browser_sniffer_manifest",
            "status": "failed",
            "message": "manifest must use localhost port wildcard instead of a copied configured port",
        }
    copied = f"http://127.0.0.1:{web_port()}/*"
    if copied in permissions:
        return {"name": "browser_sniffer_manifest", "status": "failed", "message": f"manifest contains copied port: {copied}"}
    return {"name": "browser_sniffer_manifest", "status": "ok", "path": str(path)}


def _agent_discovery_json_check() -> dict[str, Any]:
    path = agent_discovery_json_path()
    if not path.exists():
        return {"name": "agent_discovery_json", "status": "failed", "message": f"missing agent discovery JSON: {path}"}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"name": "agent_discovery_json", "status": "failed", "message": str(exc)}
    if payload.get("service") != "video-download-orchestrator":
        return {"name": "agent_discovery_json", "status": "failed", "message": "unexpected service name"}
    runtime = payload.get("runtime") if isinstance(payload.get("runtime"), dict) else {}
    if runtime.get("api_base_url") != api_base_url():
        return {"name": "agent_discovery_json", "status": "failed", "message": "agent discovery API URL is out of sync"}
    interfaces = payload.get("interfaces") if isinstance(payload.get("interfaces"), dict) else {}
    mcp = interfaces.get("mcp") if isinstance(interfaces.get("mcp"), dict) else {}
    tools = mcp.get("tools") if isinstance(mcp.get("tools"), list) else []
    if "vdo_plan_download" not in tools or "vdo_read_artifact" not in tools:
        return {"name": "agent_discovery_json", "status": "failed", "message": "agent discovery MCP tools are incomplete"}
    return {"name": "agent_discovery_json", "status": "ok", "path": str(path)}
