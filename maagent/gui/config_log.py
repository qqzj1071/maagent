from __future__ import annotations

import json

from loguru import logger

from maagent.control.weekly import WEEKDAY_NAMES
from maagent.i18n import DEFAULT_LANGUAGE, LANGUAGES


def _fmt_days(days) -> str:
    names = [
        WEEKDAY_NAMES[d]
        for d in (days or [])
        if isinstance(d, int) and 0 <= d < len(WEEKDAY_NAMES)
    ]
    return "、".join(names) or "无"


def _onoff(value) -> str:
    return "开" if value else "关"


def config_snapshot(config: dict) -> dict[str, str]:
    """A flat, human-readable view of the settings we report on save."""
    app = config.get("app", {}) or {}
    weekly = config.get("weekly", {}) or {}
    potion = weekly.get("potion", {}) or {}
    anni = weekly.get("annihilation", {}) or {}
    monthly = config.get("monthly", {}) or {}
    workflow = config.get("workflow", {}) or {}
    chain = workflow.get("chain", {}) or {}
    email = (config.get("notify", {}) or {}).get("email", {}) or {}
    hotkeys = app.get("hotkeys", {}) or {}
    return {
        "语言": LANGUAGES.get(
            app.get("language", DEFAULT_LANGUAGE), str(app.get("language", ""))
        ),
        "主题": "深色" if app.get("theme") == "dark" else "浅色",
        "开机自启动": _onoff(app.get("autostart", False)),
        "关闭时最小化到托盘": _onoff(app.get("minimize_to_tray", True)),
        "体力药刷取": (
            f"{_onoff(potion.get('enabled', False))}"
            f"（使用日：{_fmt_days(potion.get('days'))}）"
        ),
        "剿灭刷取": (
            f"{_onoff(anni.get('enabled', False))}"
            f"（刷取日：{_fmt_days([anni.get('day')] if anni.get('day') is not None else [])}）"
        ),
        "绿票商店": _onoff(monthly.get("green", {}).get("enabled", False)),
        "黄票商店": _onoff(monthly.get("yellow", {}).get("enabled", False)),
        "任务完成后自动关闭": _onoff(workflow.get("auto_close", False)),
        "发送任务日志到邮箱": _onoff(email.get("send_log", False)),
        "开始任务快捷键": str(hotkeys.get("start", "F8")),
        "强制结束快捷键": str(hotkeys.get("stop", "F9")),
        "工作流定时": _onoff(chain.get("enabled", False)),
        "定时方式": "整链定时" if chain.get("mode") == "sequential" else "逐条定时",
        "工作流编排": json.dumps(
            {
                "start_slots": chain.get("start_slots"),
                "sequential_tasks": chain.get("sequential_tasks"),
                "scheduled_tasks": chain.get("scheduled_tasks"),
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
    }


def log_config_changes(before: dict[str, str], after: dict[str, str]) -> None:
    """Log only the fields that changed since the last save."""
    changes: list[str] = []
    for key, old in before.items():
        new = after.get(key)
        if old == new:
            continue
        if key == "工作流编排":
            changes.append("  工作流编排：已更新")
        else:
            changes.append(f"  {key}：{old} → {new}")
    if changes:
        logger.info("配置已保存：\n{}", "\n".join(changes))
