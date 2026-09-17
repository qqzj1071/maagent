from __future__ import annotations

import time
from datetime import datetime
from typing import Any

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

GEAR_X = 332
CHECKBOX_DX = 22
CHECKED_MEAN = 80

WEEKDAY_NAMES = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def _find(items: list[Any], text: str) -> Any:
    for item in items:
        if text in item.text:
            return item
    return None


def _potion_checkbox(image) -> tuple[bool | None, tuple[int, int] | None]:
    label = _find(recognize(image), POTION_LABEL)
    if label is None:
        return None, None
    left = min(point[0] for point in label.box)
    cy = sum(point[1] for point in label.box) // 4
    cx = left - CHECKBOX_DX
    region = image.crop((cx - 9, cy - 9, cx + 9, cy + 9)).convert("L")
    data = region.tobytes()
    mean = sum(data) / len(data)
    return mean > CHECKED_MEAN, (cx, cy)


class MaaWeeklyPotion:
    """Read/toggle the 使用药剂 option under MAA's 理智作战 task settings."""

    def __init__(self, process_name: str = "MAA.exe", gear_x: int = GEAR_X) -> None:
        self.process_name = process_name
        self.gear_x = gear_x

    def open_fight_settings(self, hwnd: int) -> bool:
        image = capture_window(hwnd)
        if image is None:
            logger.warning("周常：无法截取 MAA 窗口")
            return False
        fight = _find(recognize(image), FIGHT_TASK)
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

    def set_state(self, hwnd: int, enabled: bool, retries: int = 3) -> bool:
        for attempt in range(1, retries + 1):
            image = capture_window(hwnd)
            if image is None:
                time.sleep(1.0)
                continue
            state, point = _potion_checkbox(image)
            if state is None or point is None:
                logger.warning("周常：未识别到「{}」选项", POTION_LABEL)
                time.sleep(1.0)
                continue
            if state == enabled:
                logger.info("周常：「{}」已是{}状态", POTION_LABEL, "开启" if enabled else "关闭")
                return True
            l, t, _, _ = win32gui.GetWindowRect(hwnd)
            _click_screen(l + point[0], t + point[1])
            time.sleep(1.2)
            after = self.read_state(hwnd)
            if after == enabled:
                logger.info("周常：已{}「{}」", "开启" if enabled else "关闭", POTION_LABEL)
                return True
            logger.warning("周常：第 {} 次切换未生效（当前 {}）", attempt, after)
        return False


def today_uses_potion(weekly_cfg: dict[str, Any], now: datetime | None = None) -> bool:
    days = weekly_cfg.get("use_potion_days") or []
    weekday = (now or datetime.now()).weekday()
    return weekday in days


def apply_weekly(weekly_cfg: dict[str, Any], process_name: str = "MAA.exe") -> dict[str, Any]:
    result: dict[str, Any] = {
        "enabled": bool(weekly_cfg.get("enabled")),
        "weekday": None,
        "desired": None,
        "before": None,
        "after": None,
        "applied": False,
    }
    if not result["enabled"]:
        logger.info("周常功能未启用，跳过")
        return result

    now = datetime.now()
    desired = today_uses_potion(weekly_cfg, now)
    result["weekday"] = now.weekday()
    result["desired"] = desired
    logger.info(
        "周常：今天 {}，应{}「{}」",
        WEEKDAY_NAMES[now.weekday()],
        "开启" if desired else "关闭",
        POTION_LABEL,
    )

    hwnd = main_window(process_name)
    if hwnd is None:
        logger.warning("周常：未找到 MAA 主窗口，跳过")
        return result

    ctl = MaaWeeklyPotion(process_name)
    if not ctl.open_fight_settings(hwnd):
        return result
    result["before"] = ctl.read_state(hwnd)
    ok = ctl.set_state(hwnd, desired)
    result["after"] = ctl.read_state(hwnd)
    result["applied"] = bool(ok and result["after"] == desired)
    if not result["applied"]:
        logger.warning("周常：未能将「{}」调整为期望状态", POTION_LABEL)
    return result
