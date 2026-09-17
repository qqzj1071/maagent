from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from loguru import logger

DEFAULT_PORT = 12701

# win32 screencap/mouse/keyboard bit flags (MXU)
SCREENCAP = {
    "DXGI_DesktopDup": 2,
    "DXGI_DesktopDup_Window": 8,
    "PrintWindow": 16,
    "ScreenDC": 32,
    "Foreground": 40,
    "Background": 18,
}
INPUT_METHOD = {
    "Seize": 1,
    "SendMessage": 2,
    "PostMessage": 4,
    "LegacyEvent": 8,
    "PostThreadMessage": 16,
}

VK = {
    "BACKSPACE": 8, "TAB": 9, "ENTER": 13, "SHIFT": 16, "CTRL": 17, "ALT": 18,
    "META": 91, "COMMAND": 91, "CMD": 91, "PAUSE": 19, "CAPSLOCK": 20, "ESC": 27,
    "SPACE": 32, "PAGEUP": 33, "PAGEDOWN": 34, "END": 35, "HOME": 36, "LEFT": 37,
    "UP": 38, "RIGHT": 39, "DOWN": 40, "INSERT": 45, "DELETE": 46,
    **{str(d): 48 + d for d in range(10)},
    **{chr(65 + i): 65 + i for i in range(26)},
    **{f"F{i}": 111 + i for i in range(1, 13)},
}

YES_NAMES = ("Yes", "yes", "Y", "y")
NO_NAMES = ("No", "no", "N", "n")


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

    def interface(self) -> dict[str, Any]:
        data = self.get("/interface") or {}
        return data.get("interface") or {}

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

    def instance_config(self, instance_id: str | None = None) -> dict[str, Any] | None:
        instance_id = instance_id or self.instance_id()
        for inst in self.config().get("instances") or []:
            if inst.get("id") == instance_id:
                return inst
        return None

    def instance_state(self, instance_id: str | None = None) -> dict[str, Any]:
        instance_id = instance_id or self.instance_id()
        return (self.state().get("instances") or {}).get(instance_id or "", {})

    def cached_windows(self) -> list[dict[str, Any]]:
        return self.state().get("cached_win32_windows") or []

    def find_window(
        self, class_regex: str | None = None, window_regex: str | None = None
    ) -> int | None:
        """Trigger a fresh desktop window search (maa_find_win)."""
        params = []
        if class_regex:
            params.append("class_regex=" + urllib.parse.quote(class_regex))
        if window_regex:
            params.append("window_regex=" + urllib.parse.quote(window_regex))
        path = "/maa/windows" + ("?" + "&".join(params) if params else "")
        wins = self.get(path) or []
        if wins:
            return int(wins[0]["handle"])
        return None

    def connect(self, handle: int, controller: dict[str, Any] | None = None) -> Any:
        controller = controller or {}
        win32 = controller.get("win32") or {}
        cfg = {
            "type": "Win32",
            "handle": int(handle),
            "screencap_method": SCREENCAP.get(win32.get("screencap", "ScreenDC"), 32),
            "mouse_method": INPUT_METHOD.get(win32.get("mouse", "Seize"), 1),
            "keyboard_method": INPUT_METHOD.get(win32.get("keyboard", "Seize"), 1),
        }
        logger.info("MaaEnd：连接控制器 {}", cfg)
        return self.post(f"/maa/instances/{self.instance_id()}/connect", cfg)

    def start_tasks(self, tasks: list[dict[str, Any]]) -> Any:
        body = {
            "tasks": tasks,
            "agent_configs": None,
            "cwd": None,
            "tcp_compat_mode": False,
            "pi_envs": None,
            "reset_state": True,
            "controller_info": None,
        }
        return self.post(f"/maa/instances/{self.instance_id()}/tasks/start", body)

    def stop_tasks(self) -> Any:
        return self.post(f"/maa/instances/{self.instance_id()}/tasks/stop")


# --------------------------------------------------------------------------- #
# pipeline override computation (ports MXU's Ea/ns/$s/Qh/jn)
# --------------------------------------------------------------------------- #
def default_option_value(opt: dict[str, Any]) -> dict[str, Any]:
    t = opt.get("type")
    if t == "input":
        return {"type": "input", "values": {i["name"]: i.get("default", "") for i in opt.get("inputs", [])}}
    if t == "hotkey":
        return {"type": "hotkey", "values": {h["name"]: h.get("default", "") for h in opt.get("hotkeys", [])}}
    cases = opt.get("cases") or []
    if t == "switch":
        name = opt.get("default_case") or (cases[0].get("name") if cases else "Yes")
        return {"type": "switch", "value": name in YES_NAMES}
    if t == "checkbox":
        return {"type": "checkbox", "caseNames": list(opt.get("default_case") or [])}
    name = opt.get("default_case") or (cases[0].get("name") if cases else "")
    return {"type": "select", "caseName": name}


def normalize_stored(stored: Any, opt: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(stored, dict):
        return None
    expected = opt.get("type") or "select"
    if stored.get("type") != expected:
        return None
    if expected == "select":
        names = {c.get("name") for c in opt.get("cases", [])}
        if stored.get("caseName") not in names:
            return None
    if expected == "checkbox":
        names = {c.get("name") for c in opt.get("cases", [])}
        kept = [n for n in stored.get("caseNames", []) if n in names]
        if stored.get("caseNames") and not kept:
            return None
        return {"type": "checkbox", "caseNames": kept}
    return stored


def option_applies(opt: dict[str, Any], controller: str | None, resource: str | None) -> bool:
    ctrls = opt.get("controller")
    if ctrls and (not controller or controller not in ctrls):
        return False
    resources = opt.get("resource")
    if resources and (not resource or resource not in resources):
        return False
    return True


def _parse_combo(combo: str) -> tuple[int, list[int]]:
    parts = [p.strip().upper() for p in str(combo or "").split("+") if p.strip()]
    if not parts:
        return 0, []
    primary = VK.get(parts[-1], 0)
    mods = [VK.get(p, 0) for p in parts[:-1]]
    return primary, mods


def _substitute(opt: dict[str, Any], value: dict[str, Any]) -> dict[str, Any]:
    raw = json.dumps(opt.get("pipeline_override") or {}, ensure_ascii=False, separators=(",", ":"))
    if opt.get("type") == "hotkey":
        for item in opt.get("hotkeys", []):
            name = item["name"]
            combo = (value.get("values") or {}).get(name) or item.get("default", "")
            primary, mods = _parse_combo(combo)
            repl = {
                "{%s}" % name: str(primary),
                "{%s.primary}" % name: str(primary),
                "{%s.modifier1}" % name: str(mods[0] if len(mods) > 0 else 0),
                "{%s.modifier2}" % name: str(mods[1] if len(mods) > 1 else 0),
            }
            for key, val in repl.items():
                raw = raw.replace('"%s"' % key, val).replace(key, val)
    else:
        for item in opt.get("inputs", []):
            name = item["name"]
            val = (value.get("values") or {}).get(name)
            if val is None:
                val = item.get("default", "")
            ptype = item.get("pipeline_type", "string")
            quoted, bare = '"{%s}"' % name, "{%s}" % name
            if ptype == "int":
                raw = raw.replace(quoted, str(val or "0")).replace(bare, str(val or "0"))
            elif ptype == "bool":
                b = "true" if str(val).lower() in ("true", "1", "yes", "y") else "false"
                raw = raw.replace(quoted, b).replace(bare, b)
            else:
                s = str(val or "")
                raw = raw.replace(quoted, json.dumps(s, ensure_ascii=False))
                raw = raw.replace(bare, json.dumps(s, ensure_ascii=False)[1:-1])
    try:
        return json.loads(raw)
    except Exception as e:
        logger.warning("MaaEnd：解析选项覆盖失败 {}: {}", opt.get("label"), e)
        return {}


def _collect_option(
    name: str,
    values: dict[str, Any],
    overrides: list[dict[str, Any]],
    option_defs: dict[str, Any],
    controller: str | None,
    resource: str | None,
) -> None:
    opt = option_defs.get(name)
    if not opt or not option_applies(opt, controller, resource):
        return
    value = normalize_stored(values.get(name), opt) or default_option_value(opt)
    t = value.get("type")
    if t == "checkbox" and opt.get("type") == "checkbox":
        selected = set(value.get("caseNames") or [])
        for case in opt.get("cases", []):
            if case.get("name") in selected and case.get("pipeline_override"):
                overrides.append(case["pipeline_override"])
    elif t in ("select", "switch") and "cases" in opt:
        if t == "switch":
            wanted = YES_NAMES if value.get("value") else NO_NAMES
            case = next((c for c in opt.get("cases", []) if c.get("name") in wanted), None)
        else:
            case = next((c for c in opt.get("cases", []) if c.get("name") == value.get("caseName")), None)
        if case and case.get("pipeline_override"):
            overrides.append(case["pipeline_override"])
        if case and case.get("option"):
            for sub in case["option"]:
                _collect_option(sub, values, overrides, option_defs, controller, resource)
    elif t in ("input", "hotkey") and opt.get("pipeline_override"):
        overrides.append(_substitute(opt, value))


def _target() -> list[int]:
    return [0, 0, 1, 1]


def _special(name: str, entry: str, action: str, options: list[str],
             option_defs: dict[str, Any]) -> dict[str, Any]:
    return {
        "entry": entry,
        "options": options,
        "pipeline_override": {entry: {"action": "Custom", "custom_action": action, "target": _target()}},
        "option_defs": option_defs,
    }


def _input_override(entry: str, fields: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "type": "input",
        "inputs": fields,
        "pipeline_override": {entry: {"custom_action_param": {f["name"]: "{%s}" % f["name"] for f in fields}}},
    }


def _switch_override(entry: str, key: str, default: str = "No") -> dict[str, Any]:
    return {
        "type": "switch",
        "default_case": default,
        "cases": [
            {"name": "Yes", "pipeline_override": {entry: {"custom_action_param": {key: True}}}},
            {"name": "No", "pipeline_override": {entry: {"custom_action_param": {key: False}}}},
        ],
    }


MXU_SPECIAL: dict[str, dict[str, Any]] = {
    "__MXU_SLEEP__": _special("__MXU_SLEEP__", "MXU_SLEEP", "MXU_SLEEP_ACTION",
        ["__MXU_SLEEP_OPTION__"],
        {"__MXU_SLEEP_OPTION__": _input_override("MXU_SLEEP", [
            {"name": "sleep_time", "default": "5", "pipeline_type": "int"}])}),
    "__MXU_WAITUNTIL__": _special("__MXU_WAITUNTIL__", "MXU_WAITUNTIL", "MXU_WAITUNTIL_ACTION",
        ["__MXU_WAITUNTIL_OPTION__"],
        {"__MXU_WAITUNTIL_OPTION__": _input_override("MXU_WAITUNTIL", [
            {"name": "target_time", "default": "08:00", "pipeline_type": "string"}])}),
    "__MXU_LAUNCH__": _special("__MXU_LAUNCH__", "MXU_LAUNCH", "MXU_LAUNCH_ACTION",
        ["__MXU_LAUNCH_OPTION__", "__MXU_LAUNCH_WAIT_OPTION__",
         "__MXU_LAUNCH_SKIP_OPTION__", "__MXU_LAUNCH_CMD_OPTION__"],
        {
            "__MXU_LAUNCH_OPTION__": _input_override("MXU_LAUNCH", [
                {"name": "program", "default": "", "pipeline_type": "string"},
                {"name": "args", "default": "", "pipeline_type": "string"}]),
            "__MXU_LAUNCH_WAIT_OPTION__": _switch_override("MXU_LAUNCH", "wait_for_exit"),
            "__MXU_LAUNCH_SKIP_OPTION__": _switch_override("MXU_LAUNCH", "skip_if_running"),
            "__MXU_LAUNCH_CMD_OPTION__": _switch_override("MXU_LAUNCH", "use_cmd"),
        }),
    "__MXU_KILLPROC__": _special("__MXU_KILLPROC__", "MXU_KILLPROC", "MXU_KILLPROC_ACTION",
        ["__MXU_KILLPROC_SELF_OPTION__"],
        {
            "__MXU_KILLPROC_SELF_OPTION__": {
                "type": "switch", "default_case": "Yes",
                "cases": [
                    {"name": "Yes", "pipeline_override": {"MXU_KILLPROC": {"custom_action_param": {"kill_self": True}}}},
                    {"name": "No", "option": ["__MXU_KILLPROC_NAME_OPTION__"],
                     "pipeline_override": {"MXU_KILLPROC": {"custom_action_param": {"kill_self": False}}}},
                ],
            },
            "__MXU_KILLPROC_NAME_OPTION__": _input_override("MXU_KILLPROC", [
                {"name": "process_name", "default": "", "pipeline_type": "string"}]),
        }),
    "__MXU_WEBHOOK__": _special("__MXU_WEBHOOK__", "MXU_WEBHOOK", "MXU_WEBHOOK_ACTION",
        ["__MXU_WEBHOOK_OPTION__"],
        {"__MXU_WEBHOOK_OPTION__": _input_override("MXU_WEBHOOK", [
            {"name": "url", "default": "", "pipeline_type": "string"}])}),
    "__MXU_NOTIFY__": _special("__MXU_NOTIFY__", "MXU_NOTIFY", "MXU_NOTIFY_ACTION",
        ["__MXU_NOTIFY_OPTION__"],
        {"__MXU_NOTIFY_OPTION__": _input_override("MXU_NOTIFY", [
            {"name": "title", "default": "MXU", "pipeline_type": "string"},
            {"name": "body", "default": "", "pipeline_type": "string"}])}),
    "__MXU_POWER__": _special("__MXU_POWER__", "MXU_POWER", "MXU_POWER_ACTION",
        ["__MXU_POWER_OPTION__"],
        {"__MXU_POWER_OPTION__": {
            "type": "select", "default_case": "shutdown",
            "cases": [
                {"name": a, "pipeline_override": {"MXU_POWER": {"custom_action_param": {"power_action": a}}}}
                for a in ("shutdown", "restart", "screenoff", "sleep", "mute", "unmute")
            ],
        }}),
}


def deep_merge(*dicts: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for d in dicts:
        for k, v in d.items():
            if isinstance(v, dict) and isinstance(result.get(k), dict):
                result[k] = deep_merge(result[k], v)
            else:
                result[k] = v
    return result


def build_special_task(task: dict[str, Any]) -> dict[str, Any] | None:
    special = MXU_SPECIAL.get(task.get("taskName"))
    if not special:
        return None
    overrides: list[dict[str, Any]] = []
    if special.get("pipeline_override"):
        overrides.append(special["pipeline_override"])
    values = task.get("optionValues") or {}
    for name in special.get("options") or []:
        _collect_option(name, values, overrides, special["option_defs"], None, None)
    override = deep_merge(*overrides) if overrides else {}
    return {
        "entry": special["entry"],
        "pipeline_override": json.dumps([override], ensure_ascii=False, separators=(",", ":")),
        "selected_task_id": task.get("id"),
        "task_name": special["entry"],
    }


def global_option_names(interface: dict[str, Any]) -> list[str]:
    names = list(interface.get("global_option") or [])
    if not names:
        for group in interface.get("setting") or []:
            names.extend(group.get("option") or [])
    return names


def build_tasks(
    interface: dict[str, Any],
    instance: dict[str, Any],
    global_option_values: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build the ``tasks`` payload MXU sends to /tasks/start."""
    option_defs = interface.get("option") or {}
    controller_name = instance.get("controllerName")
    resource_name = instance.get("resourceName")
    global_names = global_option_names(interface)
    resource = next((r for r in interface.get("resource") or [] if r.get("name") == resource_name), {})
    controller = next(
        (c for c in interface.get("controller") or [] if c.get("name") == controller_name), {}
    )
    tasks: list[dict[str, Any]] = []
    for task in instance.get("tasks") or []:
        if not task.get("enabled", True):
            continue
        special = build_special_task(task)
        if special is not None:
            tasks.append(special)
            continue
        task_def = next(
            (d for d in interface.get("task") or [] if d.get("name") == task.get("taskName")), None
        )
        if not task_def or not task_def.get("entry"):
            logger.warning("MaaEnd：未找到任务定义 {}", task.get("taskName"))
            continue
        overrides: list[dict[str, Any]] = []
        if task_def.get("pipeline_override"):
            overrides.append(task_def["pipeline_override"])
        values = task.get("optionValues") or {}
        for name in global_names:
            _collect_option(name, global_option_values, overrides, option_defs, controller_name, resource_name)
        for name in resource.get("option") or []:
            _collect_option(name, values, overrides, option_defs, controller_name, resource_name)
        for name in controller.get("option") or []:
            _collect_option(name, values, overrides, option_defs, controller_name, resource_name)
        for name in task_def.get("option") or []:
            _collect_option(name, values, overrides, option_defs, controller_name, resource_name)
        tasks.append({
            "entry": task_def["entry"],
            "pipeline_override": json.dumps(overrides, ensure_ascii=False, separators=(",", ":")),
            "selected_task_id": task.get("id"),
            "task_name": task_def["name"],
        })
    return tasks
