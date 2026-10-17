from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from maagent.control.game_time import game_weekday

WEEKDAY_NAMES = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

# 原神一条龙的周常配置组（名字需与 BetterGI 一条龙配置的 TaskDefinitions 一致）
DEFAULT_GROUPS: list[dict[str, Any]] = [
    {"name": "大伟丘讨伐", "enabled": False, "days": []},
    {"name": "自动钓鱼", "enabled": False, "days": []},
    {"name": "虹滴晶采集", "enabled": False, "days": []},
    {"name": "狗粮购买分解", "enabled": False, "days": []},
]


def groups_config(weekly_cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize the group list, merging in any configured-but-unknown groups."""
    configured = weekly_cfg.get("groups") or []
    names = {g.get("name") for g in configured if isinstance(g, dict) and g.get("name")}
    merged = [
        {
            "name": g.get("name"),
            "enabled": bool(g.get("enabled", False)),
            "days": [int(d) for d in (g.get("days") or [])],
        }
        for g in configured
        if isinstance(g, dict) and g.get("name")
    ]
    for default in DEFAULT_GROUPS:
        if default["name"] not in names:
            merged.append(dict(default))
    return merged


def _read_config(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return json.loads(raw.decode(enc))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    return json.loads(raw.decode("utf-8", errors="replace"))


def _write_config(path: Path, data: dict[str, Any]) -> None:
    path.write_bytes(json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8-sig"))


def desired_for_group(group: dict[str, Any], now: datetime | None = None) -> bool:
    """Whether a config group should be checked on the current game-day."""
    if not group.get("enabled"):
        return False
    weekday = game_weekday(now)
    days = group.get("days") or []
    return weekday in days


def apply_weekly(weekly_cfg: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    """按周常日程勾选/取消勾选 BetterGI 一条龙里的配置组。

    直接修改 BetterGI 的一条龙配置文件（``TaskEnabledList``），随后 maagent 再点
    「一条龙」时 BetterGI 会按更新后的勾选状态运行。日期按游戏内凌晨 4 点切换计算。
    """
    result: dict[str, Any] = {
        "enabled": bool(weekly_cfg.get("enabled")),
        "weekday": game_weekday(now),
        "groups": {},
    }
    config_path = weekly_cfg.get("one_dragon_config")
    if not config_path:
        logger.warning("原神周常：未配置 one_dragon_config")
        return result
    if not result["enabled"]:
        logger.info("原神周常功能未启用，跳过")
        return result
    path = Path(config_path)
    if not path.exists():
        logger.warning("原神周常：找不到一条龙配置 {}", config_path)
        return result

    data = _read_config(path)
    definitions = data.get("TaskDefinitions") or {}
    enabled_map = data.setdefault("TaskEnabledList", {})
    name_to_guid = {name: guid for guid, name in definitions.items()}
    weekday = game_weekday(now)

    for group in groups_config(weekly_cfg):
        name = group.get("name")
        guid = name_to_guid.get(name) if name else None
        if guid is None:
            logger.warning("原神周常：一条龙配置中未找到配置组「{}」", name)
            result["groups"][name] = {"found": False}
            continue
        item_enabled = bool(group.get("enabled", False))
        days = group.get("days") or []
        desired = bool(item_enabled and weekday in days)
        if not item_enabled:
            action = "始终取消勾选（未启用）"
        elif not days:
            action = "始终取消勾选（未选择执行日）"
        else:
            action = ("勾选" if desired else "取消勾选") + f"（今天 {WEEKDAY_NAMES[weekday]}）"
        enabled_map[guid] = desired
        logger.info("原神周常：配置组「{}」{}", name, action)
        result["groups"][name] = {"desired": desired, "found": True}

    _write_config(path, data)
    return result
