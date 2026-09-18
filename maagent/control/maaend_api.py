from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

DEFAULT_PORT = 12701


class MaaEndApiError(RuntimeError):
    pass


class MaaEndApi:
    """Thin client for MaaEnd's local HTTP API (MaaFramework / MXU)."""

    def __init__(self, port: int = DEFAULT_PORT, timeout: float = 10.0) -> None:
        self.base = f"http://127.0.0.1:{int(port)}/api"
        self.timeout = timeout

    def _request(self, method: str, path: str, data: Any = None):
        url = self.base + path
        body = None
        headers = {}
        if data is not None:
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", "replace")
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:200]
            raise MaaEndApiError(f"{method} {path} -> HTTP {e.code}: {detail}") from e
        except Exception as e:
            raise MaaEndApiError(f"{method} {path} 失败: {e}") from e

    def get(self, path: str):
        return self._request("GET", path)

    def post(self, path: str, data: Any = None):
        return self._request("POST", path, data)

    def ping(self) -> bool:
        try:
            self.get("/maa/initialized")
            return True
        except Exception:
            return False

    def config(self) -> dict[str, Any]:
        return self.get("/config") or {}

    def save_config(self, cfg: dict[str, Any]) -> None:
        self.post("/config", cfg)

    def state(self) -> dict[str, Any]:
        return self.get("/maa/state") or {}

    def logs(self) -> dict[str, Any]:
        return self.get("/logs") or {}

    def instance_id(self) -> str | None:
        cfg = self.config()
        instances = cfg.get("instances") or []
        if not instances:
            return None
        active = cfg.get("lastActiveInstanceId")
        if active and any(i.get("id") == active for i in instances):
            return active
        return instances[0].get("id")

    def instance_state(self, instance_id: str | None = None) -> dict[str, Any]:
        instance_id = instance_id or self.instance_id()
        return (self.state().get("instances") or {}).get(instance_id or "", {})
