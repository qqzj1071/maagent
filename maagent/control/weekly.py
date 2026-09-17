from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import win32con
import win32gui
from loguru import logger

from maagent.control.popup import (
    _click_screen,
    _force_foreground,
    capture_window,
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

DEFAULT_WEEKLY_LIMIT = 5
DEFAULT_SANITY_THRESHOLD = 124
DEFAULT_STATE_FILE = "logs/weekly_state.json"

WEEKDAY_NAMES = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


# --------------------------------------------------------------------------- #
# low level helpers
# --------------------------------------------------------------------------- #
def _find(items: list[Any], text: str) -> Any:
    for item in items:
        if text in item.text:
            return item
    return None


def _ensure_visible(hwnd: int) -> None:
    """Restore a minimized MAA window so PrintWindow capture works."""
    try:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            time.sleep(1.0)
    except Exception:
        pass


def _checkbox_state(image, cx: int, cy: int) -> bool:
    region = image.crop((cx - 9, cy - 9, cx + 9, cy + 9)).convert("L")
    data = region.tobytes()
    return (sum(data) / len(data)) > CHECKED_MEAN


def _potion_checkbox(image) -> tuple[bool | None, tuple[int, int] | None]:
    left_bound = max(0, PANEL_MIN_X - 20)
    crop = image.crop((left_bound, 0, PANEL_MAX_X + 20, image.height))
    label = _find(recognize(crop), POTION_LABEL)
    if label is None:
        return None, None
    left = min(point[0] for point in label.box) + left_bound
    cy = sum(point[1] for point in label.box) // 4
    cx = left - CHECKBOX_DX
    return _checkbox_state(image, cx, cy), (cx, cy)


def _task_checkbox(image, task_name: str) -> tuple[bool | None, tuple[int, int] | None]:
    crop = image.crop((0, 0, TASK_LIST_MAX_X + 40, image.height))
    label = _find(recognize(crop), task_name)
    if label is None:
        return None, None
    cy = sum(point[1] for point in label.box) // 4
    return _checkbox_state(image, TASK_CHECKBOX_X, cy), (TASK_CHECKBOX_X, cy)


# --------------------------------------------------------------------------- #
# MAA controls
# --------------------------------------------------------------------------- #
class MaaWeeklyPotion:
    """Read/toggle the 使用药剂 option under MAA's 理智作战 task settings."""

    def __init__(self, gear_x: int = GEAR_X) -> None:
        self.gear_x = gear_x

    def open_fight_settings(self, hwnd: int) -> bool:
        _ensure_visible(hwnd)
        image = capture_window(hwnd)
        if image is None:
            logger.warning("周常：无法截取 MAA 窗口")
            return False
        fight = _find(recognize(image.crop((0, 0, TASK_LIST_MAX_X + 40, image.height))), FIGHT_TASK)
        if fight is None:
            logger.warning("周常：未找到「{}」任务行", FIGHT_TASK)
            return False
        _force_foreground(hwnd)
        time.sleep(1.0)
        l, t, _, _ = win32gui.GetWindowRect(hwnd)
        _click_screen(l + self.gear_x, t + fight.center[1])
        time.sleep(1.2)
        return True

    def read_state(self, hwnd: int) -> bool | None:
        image = capture_window(hwnd)
        if image is None:
            return None
        return _potion_checkbox(image)[0]

    def set_state(self, hwnd: int, enabled: bool, retries: int = 3) -> tuple[bool, bool | None, bool | None]:
        """Return (ok, before, after)."""
        before: bool | None = None
        after: bool | None = None
        for attempt in range(1, retries + 1):
            image = capture_window(hwnd)
            if image is None:
                time.sleep(1.0)
                continue
            state, point = _potion_checkbox(image)
            if before is None:
                before = state
            if state is None or point is None:
                logger.warning("周常：未识别到「{}」选项", POTION_LABEL)
                time.sleep(1.0)
                continue
            if state == enabled:
                logger.info("周常：「{}」已是{}状态", POTION_LABEL, "开启" if enabled else "关闭")
                return True, before, state
            l, t, _, _ = win32gui.GetWindowRect(hwnd)
            _click_screen(l + point[0], t + point[1])
            time.sleep(1.2)
            after = self.read_state(hwnd)
            if after == enabled:
                logger.info("周常：已{}「{}」", "开启" if enabled else "关闭", POTION_LABEL)
                return True, before, after
            logger.warning("周常：第 {} 次切换未生效（当前 {}）", attempt, after)
        return False, before, after


class MaaTaskToggle:
    """Read/toggle a task row's enable checkbox in MAA's task list."""

    def read_state(self, hwnd: int, task_name: str) -> bool | None:
        image = capture_window(hwnd)
        if image is None:
            return None
        return _task_checkbox(image, task_name)[0]

    def set_state(
        self, hwnd: int, task_name: str, enabled: bool, retries: int = 3
    ) -> tuple[bool, bool | None, bool | None]:
        """Return (ok, before, after)."""
        _ensure_visible(hwnd)
        _force_foreground(hwnd)
        time.sleep(0.8)
        before: bool | None = None
        after: bool | None = None
        for attempt in range(1, retries + 1):
            image = capture_window(hwnd)
            if image is None:
                time.sleep(1.0)
                continue
            state, point = _task_checkbox(image, task_name)
            if before is None:
                before = state
            if state is None or point is None:
                logger.warning("周常：未识别到任务「{}」", task_name)
                time.sleep(1.0)
                continue
            if state == enabled:
                logger.info("周常：任务「{}」已是{}状态", task_name, "勾选" if enabled else "未勾选")
                return True, before, state
            l, t, _, _ = win32gui.GetWindowRect(hwnd)
            _click_screen(l + point[0], t + point[1])
            time.sleep(1.0)
            after = self.read_state(hwnd, task_name)
            if after == enabled:
                logger.info("周常：已{}任务「{}」", "勾选" if enabled else "取消勾选", task_name)
                return True, before, after
            logger.warning("周常：第 {} 次切换「{}」未生效（当前 {}）", attempt, task_name, after)
        return False, before, after


# --------------------------------------------------------------------------- #
# weekly state (per ISO week)
# --------------------------------------------------------------------------- #
def current_week_key(now: datetime | None = None) -> str:
    now = now or datetime.now()
    iso = now.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


class WeeklyState:
    """Tracks weekly progress (currently: annihilation runs / sanity) on disk."""

    def __init__(self, path: str | Path = DEFAULT_STATE_FILE) -> None:
        self.path = Path(path)
        self.data: dict[str, Any] = {}
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            self.data = {}

    def _anni(self) -> dict[str, Any]:
        week = current_week_key()
        anni = self.data.get("annihilation") or {}
        if anni.get("week") != week:
            anni = {"week": week, "runs": 0, "sanity": 0, "done": False}
            self.data["annihilation"] = anni
        return anni

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:
            logger.warning("周常：保存进度失败 {}", e)

    def annihilation(self) -> dict[str, Any]:
        return self._anni()

    def annihilation_done(self, weekly_limit: int = DEFAULT_WEEKLY_LIMIT,
                          sanity_threshold: int = DEFAULT_SANITY_THRESHOLD) -> bool:
        anni = self._anni()
        return bool(anni.get("done")) or anni.get("runs", 0) >= weekly_limit \
            or anni.get("sanity", 0) >= sanity_threshold

    def set_annihilation(self, runs: int, sanity: int, weekly_limit: int = DEFAULT_WEEKLY_LIMIT,
                         sanity_threshold: int = DEFAULT_SANITY_THRESHOLD) -> dict[str, Any]:
        anni = self._anni()
        anni["runs"] = max(anni.get("runs", 0), int(runs))
        anni["sanity"] = max(anni.get("sanity", 0), int(sanity))
        if anni["runs"] >= weekly_limit or anni["sanity"] >= sanity_threshold:
            anni["done"] = True
        self._save()
        return anni


# --------------------------------------------------------------------------- #
# log parsing
# --------------------------------------------------------------------------- #
def parse_annihilation(logs: list[str]) -> tuple[int, int]:
    """Parse (runs, sanity) consumed by the 剿灭刷取 task from MAA panel log lines."""
    runs = 0
    sanity = 0
    in_anni = False
    for line in logs:
        if ANNIHILATION_TASK in line:
            if "开始任务" in line:
                in_anni = True
                continue
            if "完成任务" in line:
                in_anni = False
                continue
        if not in_anni:
            continue
        m = re.search(r"开始行动\s*(\d+)\s*[~～\-]\s*(\d+)?\s*次", line)
        if m:
            runs += int(m.group(2) or m.group(1))
            continue
        m = re.search(r"-\s*(\d+)\s*理智", line)
        if m:
            sanity += int(m.group(1))
    if sanity == 0 and runs > 0:
        sanity = runs * 25
    return runs, sanity


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
    weekly_limit = int(cfg.get("weekly_limit", DEFAULT_WEEKLY_LIMIT))
    sanity_threshold = int(cfg.get("sanity_threshold", DEFAULT_SANITY_THRESHOLD))
    now = datetime.now()

    done = state.annihilation_done(weekly_limit, sanity_threshold) if state else False
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
        "weekly_limit": weekly_limit,
        "sanity_threshold": sanity_threshold,
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
