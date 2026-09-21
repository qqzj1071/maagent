from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Callable

import win32gui
from loguru import logger

from maagent.control.game_time import game_datetime
from maagent.control.logmonitor import MaaLogMonitor
from maagent.control.popup import (
    _click_screen,
    _force_foreground,
    capture_window,
    crop_region,
    ensure_visible,
    find_text,
    main_window,
    recognize,
)
from maagent.control.state import JsonState

TAB_TOOL = "小工具"
TOOL_NIUZA = "牛杂"
GREEN_STORE = "绿票商店"
YELLOW_STORE = "黄票商店"
LINK_START = "Link Start"
LINK_START_OCR = ("Link Start", "LinkStart", "Link")
TAB_DAILY = "一键长草"
TAB_DAILY_OCR = ("一键长草", "键长草", "长草")

TASK_START = "开始任务"
TASK_DONE = "完成任务"

STORE_LABELS = {"green": GREEN_STORE, "yellow": YELLOW_STORE}
STORE_KEYS = ("green", "yellow")

# fractional regions of the MAA main window (captured image includes the title bar)
TAB_REGION = (0.0, 0.035, 1.0, 0.115)
SUBTOOL_REGION = (0.15, 0.115, 0.85, 0.18)
LIST_REGION = (0.0, 0.17, 0.36, 0.78)
BUTTON_REGION = (0.05, 0.82, 0.32, 0.98)

DEFAULT_STATE_FILE = "logs/monthly_state.json"
DEFAULT_TIMEOUT = 900
DEFAULT_START_TIMEOUT = 90


class MaaToolNavigator:
    """Drives MAA's 小工具 → 牛杂 tools via OCR of the main window."""

    def __init__(self, process_name: str = "MAA.exe") -> None:
        self.process_name = process_name

    def _hwnd(self) -> int | None:
        hwnd = main_window(self.process_name)
        if hwnd is None:
            return None
        ensure_visible(hwnd)
        _force_foreground(hwnd)
        time.sleep(0.6)
        return hwnd

    def _click_label(
        self,
        hwnd: int,
        region: tuple[float, float, float, float],
        text: str | tuple[str, ...],
    ) -> bool:
        _force_foreground(hwnd)
        time.sleep(0.4)
        image = capture_window(hwnd)
        if image is None:
            logger.warning("月常：无法截取 MAA 窗口")
            return False
        l, t, r, b = crop_region(image, region)
        items = recognize(image.crop((l, t, r, b)))
        item = None
        for candidate in (text,) if isinstance(text, str) else text:
            item = find_text(items, candidate)
            if item is not None:
                break
        if item is None:
            logger.warning("月常：未识别到「{}」", text)
            return False
        cx, cy = item.center
        wx, wy, _, _ = win32gui.GetWindowRect(hwnd)
        _click_screen(wx + l + cx, wy + t + cy)
        return True

    def open_tool(self) -> int | None:
        hwnd = self._hwnd()
        if hwnd is None:
            logger.warning("月常：未找到 MAA 主窗口")
            return None
        if not self._click_label(hwnd, TAB_REGION, TAB_TOOL):
            return None
        time.sleep(1.0)
        hwnd = self._hwnd() or hwnd
        if not self._click_label(hwnd, SUBTOOL_REGION, TOOL_NIUZA):
            return None
        time.sleep(1.0)
        return hwnd

    def return_to_daily(self) -> None:
        hwnd = self._hwnd()
        if hwnd is not None:
            self._click_label(hwnd, TAB_REGION, TAB_DAILY_OCR)
            time.sleep(0.8)

    def run_store(
        self,
        hwnd: int,
        store_name: str,
        timeout: float = DEFAULT_TIMEOUT,
        start_timeout: float = DEFAULT_START_TIMEOUT,
        interval: float = 5.0,
        on_poll: Callable[[], None] | None = None,
    ) -> str:
        """Select a store, press its Link Start and wait until it finishes.

        The 牛杂 tool runs as a custom task and leaves the bottom button untouched,
        so completion is detected from the panel log instead of the button state.
        """
        if not self._click_label(hwnd, LIST_REGION, store_name):
            return "failed"
        time.sleep(1.5)
        hwnd = self._hwnd() or hwnd

        logmon = MaaLogMonitor(hwnd)
        logmon.collect_logs()
        baseline = len(logmon.logs)

        for attempt in range(1, 4):
            if not self._click_label(hwnd, BUTTON_REGION, LINK_START_OCR):
                return "failed"
            logger.info("月常：已启动「{}」购买任务（第 {} 次）", store_name, attempt)
            if self._wait_started(logmon, start_timeout, baseline, on_poll):
                return self._wait_done(logmon, timeout, interval, baseline, on_poll, store_name)
            logger.warning("月常：「{}」点击 Link Start 后任务未启动，重试", store_name)
            hwnd = self._hwnd() or hwnd
            time.sleep(2.0)
        logger.warning("月常：「{}」任务未能启动", store_name)
        return "failed"

    @staticmethod
    def _new_lines(logmon: MaaLogMonitor, baseline: int) -> list[str]:
        logmon.collect_logs()
        return logmon.logs[baseline:]

    @classmethod
    def _wait_started(
        cls,
        logmon: MaaLogMonitor,
        timeout: float,
        baseline: int,
        on_poll: Callable[[], None] | None,
    ) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if on_poll is not None:
                on_poll()
            new = cls._new_lines(logmon, baseline)
            if any(TASK_START in ln or TASK_DONE in ln for ln in new):
                return True
            time.sleep(2.0)
        return False

    @classmethod
    def _wait_done(
        cls,
        logmon: MaaLogMonitor,
        timeout: float,
        interval: float,
        baseline: int,
        on_poll: Callable[[], None] | None,
        store_name: str,
    ) -> str:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if on_poll is not None:
                on_poll()
            new = cls._new_lines(logmon, baseline)
            if any("任务失败" in ln or "任务出错" in ln for ln in new):
                logger.warning("月常：「{}」任务失败", store_name)
                return "failed"
            if any(TASK_DONE in ln for ln in new):
                logger.info("月常：「{}」购买任务已完成", store_name)
                return "done"
            time.sleep(interval)
        logger.warning("月常：「{}」任务超时", store_name)
        return "timeout"


# --------------------------------------------------------------------------- #
# monthly state (per game month, switching at 04:00 on the 1st)
# --------------------------------------------------------------------------- #
def current_month_key(now: datetime | None = None) -> str:
    now = game_datetime(now)
    return f"{now.year}-{now.month:02d}"


class MonthlyState(JsonState):
    """Tracks whether each store has been bought this month."""

    def _bucket(self) -> dict[str, Any]:
        return self._period_bucket(
            current_month_key(), {"green": False, "yellow": False}
        )

    def is_done(self, key: str) -> bool:
        return bool(self._bucket().get(key, False))

    def mark_done(self, key: str) -> None:
        self._bucket()[key] = True
        self.save()


# --------------------------------------------------------------------------- #
# config helpers / applying
# --------------------------------------------------------------------------- #
def store_config(monthly_cfg: dict[str, Any], key: str) -> dict[str, Any]:
    return monthly_cfg.get(key) or {}


def format_monthly(result: dict[str, Any]) -> str:
    status_text = {
        "done": "已购买",
        "skipped": "本月已完成",
        "failed": "购买失败",
        "timeout": "购买超时",
    }
    parts = []
    for key in STORE_KEYS:
        info = result.get(key)
        if not info:
            continue
        parts.append(f"{STORE_LABELS[key]}：{status_text.get(info.get('status'), info.get('status'))}")
    return "；".join(parts)


def apply_monthly(
    monthly_cfg: dict[str, Any],
    process_name: str = "MAA.exe",
    state: MonthlyState | None = None,
    on_poll: Callable[[], None] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {"enabled": bool(monthly_cfg.get("enabled", True))}
    for key in STORE_KEYS:
        result[key] = None
    if not result["enabled"]:
        logger.info("月常功能未启用，跳过")
        return result

    pending = [
        key for key in STORE_KEYS
        if store_config(monthly_cfg, key).get("enabled", False)
        and not (state and state.is_done(key))
    ]
    if not pending:
        logger.info("月常：本月绿票/黄票商店均无需购买")
        return result

    navigator = MaaToolNavigator(process_name)
    hwnd = navigator.open_tool()
    if hwnd is None:
        navigator.return_to_daily()
        for key in pending:
            result[key] = {"status": "failed"}
        return result

    timeout = float(monthly_cfg.get("timeout_seconds", DEFAULT_TIMEOUT))
    for key in STORE_KEYS:
        if not store_config(monthly_cfg, key).get("enabled", False):
            continue
        if state and state.is_done(key):
            result[key] = {"status": "skipped"}
            continue
        status = navigator.run_store(
            hwnd, STORE_LABELS[key], timeout=timeout, on_poll=on_poll
        )
        if status == "done" and state is not None:
            state.mark_done(key)
        result[key] = {"status": status}

    navigator.return_to_daily()
    return result
