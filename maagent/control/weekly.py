from __future__ import annotations

import re
import time
from datetime import datetime
from typing import Any

import win32gui
from loguru import logger

from maagent.control.state import JsonState
from maagent.control.popup import (
    _click_screen,
    _force_foreground,
    capture_window,
    ensure_visible,
    find_text,
    main_window,
    recognize,
)

FIGHT_TASK = "理智作战"
POTION_LABEL = "使用药剂"
ANNIHILATION_TASK = "剿灭刷取"

GEAR_X = 332
CHECKBOX_DX = 22
TASK_CHECKBOX_X = 100
TASK_LIST_MAX_X = 230
PANEL_MIN_X = 300
PANEL_MAX_X = 800
CHECKED_MEAN = 80

DEFAULT_ANNIHILATION_CAP = 1800
DEFAULT_STATE_FILE = "logs/weekly_state.json"

WEEKDAY_NAMES = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


# --------------------------------------------------------------------------- #
# low level helpers
# --------------------------------------------------------------------------- #
def _checkbox_state(image, cx: int, cy: int) -> bool:
    region = image.crop((cx - 9, cy - 9, cx + 9, cy + 9)).convert("L")
    data = region.tobytes()
    return (sum(data) / len(data)) > CHECKED_MEAN


def _potion_checkbox(image) -> tuple[bool | None, tuple[int, int] | None]:
    left_bound = max(0, PANEL_MIN_X - 20)
    crop = image.crop((left_bound, 0, PANEL_MAX_X + 20, image.height))
    label = find_text(recognize(crop), POTION_LABEL)
    if label is None:
        return None, None
    left = min(point[0] for point in label.box) + left_bound
    cy = sum(point[1] for point in label.box) // 4
    cx = left - CHECKBOX_DX
    return _checkbox_state(image, cx, cy), (cx, cy)


def _task_checkbox(image, task_name: str) -> tuple[bool | None, tuple[int, int] | None]:
    crop = image.crop((0, 0, TASK_LIST_MAX_X + 40, image.height))
    label = find_text(recognize(crop), task_name)
    if label is None:
        return None, None
    cy = sum(point[1] for point in label.box) // 4
    return _checkbox_state(image, TASK_CHECKBOX_X, cy), (TASK_CHECKBOX_X, cy)


def _toggle_checkbox(
    hwnd: int,
    probe,
    enabled: bool,
    name: str,
    action_on: str,
    action_off: str,
    retries: int = 3,
    settle: float = 1.0,
) -> tuple[bool, bool | None, bool | None]:
    """Click a checkbox until it matches ``enabled``; ``probe(image)`` -> (state, point)."""
    before: bool | None = None
    after: bool | None = None
    for attempt in range(1, retries + 1):
        image = capture_window(hwnd)
        if image is None:
            time.sleep(1.0)
            continue
        state, point = probe(image)
        if before is None:
            before = state
        if state is None or point is None:
            logger.warning("周常：未识别到{}", name)
            time.sleep(1.0)
            continue
        if state == enabled:
            logger.info("周常：{}已是{}状态", name, action_on if enabled else action_off)
            return True, before, state
        l, t, _, _ = win32gui.GetWindowRect(hwnd)
        _click_screen(l + point[0], t + point[1])
        time.sleep(settle)
        after = probe(capture_window(hwnd))[0]
        if after == enabled:
            logger.info("周常：已{}{}", action_on if enabled else action_off, name)
            return True, before, after
        logger.warning("周常：第 {} 次切换{}未生效（当前 {}）", attempt, name, after)
    return False, before, after


# --------------------------------------------------------------------------- #
# MAA controls
# --------------------------------------------------------------------------- #
class MaaWeeklyPotion:
    """Read/toggle the 使用药剂 option under MAA's 理智作战 task settings."""

    def __init__(self, gear_x: int = GEAR_X) -> None:
        self.gear_x = gear_x

    def open_fight_settings(self, hwnd: int) -> bool:
        ensure_visible(hwnd)
        image = capture_window(hwnd)
        if image is None:
            logger.warning("周常：无法截取 MAA 窗口")
            return False
        fight = find_text(recognize(image.crop((0, 0, TASK_LIST_MAX_X + 40, image.height))), FIGHT_TASK)
        if fight is None:
            logger.warning("周常：未找到「{}」任务行", FIGHT_TASK)
            return False
        _force_foreground(hwnd)
        time.sleep(1.0)
        l, t, _, _ = win32gui.GetWindowRect(hwnd)
        _click_screen(l + self.gear_x, t + fight.center[1])
        time.sleep(1.2)
        return True

    def set_state(
        self, hwnd: int, enabled: bool, retries: int = 3
    ) -> tuple[bool, bool | None, bool | None]:
        return _toggle_checkbox(
            hwnd, _potion_checkbox, enabled, f"「{POTION_LABEL}」", "开启", "关闭", retries, 1.2
        )


class MaaTaskToggle:
    """Read/toggle a task row's enable checkbox in MAA's task list."""

    def set_state(
        self, hwnd: int, task_name: str, enabled: bool, retries: int = 3
    ) -> tuple[bool, bool | None, bool | None]:
        ensure_visible(hwnd)
        _force_foreground(hwnd)
        time.sleep(0.8)
        return _toggle_checkbox(
            hwnd,
            lambda image: _task_checkbox(image, task_name),
            enabled,
            f"任务「{task_name}」",
            "勾选",
            "取消勾选",
            retries,
        )


# --------------------------------------------------------------------------- #
# weekly state (per ISO week)
# --------------------------------------------------------------------------- #
def current_week_key(now: datetime | None = None) -> str:
    now = now or datetime.now()
    iso = now.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


class WeeklyState(JsonState):
    """Tracks weekly progress (currently: annihilation) on disk."""

    def _anni(self) -> dict[str, Any]:
        bucket = self._period_bucket(
            current_week_key(), {"annihilation": {"progress": 0, "done": False}}
        )
        return bucket["annihilation"]

    def annihilation(self) -> dict[str, Any]:
        return self._anni()

    def annihilation_done(self, cap: int = DEFAULT_ANNIHILATION_CAP) -> bool:
        anni = self._anni()
        return bool(anni.get("done")) or anni.get("progress", 0) >= cap

    def set_annihilation(self, progress: int, cap: int = DEFAULT_ANNIHILATION_CAP) -> dict[str, Any]:
        anni = self._anni()
        anni["progress"] = max(anni.get("progress", 0), int(progress))
        if anni["progress"] >= cap:
            anni["done"] = True
        self.save()
        return anni


# --------------------------------------------------------------------------- #
# log parsing
# --------------------------------------------------------------------------- #
def parse_annihilation(logs: list[str]) -> int:
    """Return the 剿灭模式 progress (out of the weekly cap) seen in MAA's panel log.

    MAA prints a line like ``剿灭模式: 1800 / 1800`` once the weekly cap is reached.
    """
    progress = 0
    for line in logs:
        m = re.search(r"剿灭\D{0,8}(\d+)\s*[／/]\s*(\d+)", line)
        if m:
            progress = max(progress, int(m.group(1)))
    return progress


# --------------------------------------------------------------------------- #
# config helpers
# --------------------------------------------------------------------------- #
def potion_config(weekly_cfg: dict[str, Any]) -> dict[str, Any]:
    return weekly_cfg.get("potion") or {}


def annihilation_config(weekly_cfg: dict[str, Any]) -> dict[str, Any]:
    return weekly_cfg.get("annihilation") or {}


def today_uses_potion(weekly_cfg: dict[str, Any], now: datetime | None = None) -> bool:
    potion = potion_config(weekly_cfg)
    if not potion.get("enabled", False):
        return False
    days = potion.get("days") or []
    weekday = (now or datetime.now()).weekday()
    return weekday in days


def today_runs_annihilation(weekly_cfg: dict[str, Any], now: datetime | None = None) -> bool:
    cfg = annihilation_config(weekly_cfg)
    if not cfg.get("enabled", False):
        return False
    day = cfg.get("day")
    if day is None:
        return False
    return (now or datetime.now()).weekday() == int(day)


# --------------------------------------------------------------------------- #
# applying
# --------------------------------------------------------------------------- #
def _apply_potion(hwnd: int, weekly_cfg: dict[str, Any]) -> dict[str, Any]:
    potion = potion_config(weekly_cfg)
    item_enabled = bool(potion.get("enabled", False))
    desired = today_uses_potion(weekly_cfg)
    now = datetime.now()
    if not item_enabled:
        logger.info("周常：体力药刷取未启用 → 始终关闭「{}」", POTION_LABEL)
    else:
        logger.info(
            "周常：今天 {}，应{}「{}」",
            WEEKDAY_NAMES[now.weekday()],
            "开启" if desired else "关闭",
            POTION_LABEL,
        )
    ctl = MaaWeeklyPotion()
    if not ctl.open_fight_settings(hwnd):
        return {"item_enabled": item_enabled, "desired": desired, "before": None, "after": None, "applied": False}
    ok, before, after = ctl.set_state(hwnd, desired)
    return {
        "item_enabled": item_enabled,
        "desired": desired,
        "before": before,
        "after": after,
        "applied": bool(ok and after == desired),
    }


def _apply_annihilation(
    hwnd: int,
    weekly_cfg: dict[str, Any],
    state: WeeklyState | None = None,
) -> dict[str, Any]:
    cfg = annihilation_config(weekly_cfg)
    item_enabled = bool(cfg.get("enabled", False))
    day = cfg.get("day")
    cap = int(cfg.get("cap", DEFAULT_ANNIHILATION_CAP))
    now = datetime.now()

    done = state.annihilation_done(cap) if state else False
    is_day = today_runs_annihilation(weekly_cfg, now)
    desired = bool(item_enabled and day is not None and is_day and not done)

    if not item_enabled:
        logger.info("周常：剿灭刷取未启用 → 始终取消勾选「{}」", ANNIHILATION_TASK)
    elif day is None:
        logger.info("周常：剿灭刷取未选择刷取日 → 始终取消勾选「{}」", ANNIHILATION_TASK)
    elif done:
        logger.info("周常：本周剿灭已完成（{}），取消勾选「{}」", state.annihilation() if state else "", ANNIHILATION_TASK)
    elif not is_day:
        logger.info("周常：今天 {} 不是剿灭刷取日 → 取消勾选「{}」", WEEKDAY_NAMES[now.weekday()], ANNIHILATION_TASK)
    else:
        logger.info(
            "周常：剿灭刷取日（{}）→ 勾选「{}」并监控日志",
            WEEKDAY_NAMES[int(day)],
            ANNIHILATION_TASK,
        )

    ctl = MaaTaskToggle()
    ok, before, after = ctl.set_state(hwnd, ANNIHILATION_TASK, desired)
    return {
        "item_enabled": item_enabled,
        "day": day,
        "cap": cap,
        "done_before": done,
        "desired": desired,
        "before": before,
        "after": after,
        "applied": bool(ok and after == desired),
    }


def apply_weekly(
    weekly_cfg: dict[str, Any],
    process_name: str = "MAA.exe",
    state: WeeklyState | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "enabled": bool(weekly_cfg.get("enabled")),
        "weekday": datetime.now().weekday(),
        "potion": None,
        "annihilation": None,
    }
    if not result["enabled"]:
        logger.info("周常功能未启用，跳过")
        return result

    hwnd = main_window(process_name)
    if hwnd is None:
        logger.warning("周常：未找到 MAA 主窗口，跳过")
        return result

    result["potion"] = _apply_potion(hwnd, weekly_cfg)
    result["annihilation"] = _apply_annihilation(hwnd, weekly_cfg, state)
    return result
