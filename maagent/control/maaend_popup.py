"""MaaEnd WebView 内嵌更新完成提示，不使用 WM_CLOSE 关闭主窗口。"""
from __future__ import annotations

import time
from pathlib import Path

import win32gui
from loguru import logger

from maagent.control.maaend import MaaEndUI, capture_window_screen
from maagent.control.popup import OcrItem, _click_screen, recognize


def update_acknowledgement(
    items: list[OcrItem], width: int, height: int,
) -> OcrItem | None:
    """要求独立标题和其下方的唯一确认按钮，拒绝普通任务日志中的关键词。"""
    titles = [
        i for i in items if i.text.strip() == "更新完成" and i.score >= 0.85
        and width * .15 < i.center[0] < width * .85
        and height * .10 < i.center[1] < height * .65
    ]
    if len(titles) != 1:
        return None
    buttons = [
        i for i in items if i.text.strip() in ("知道了", "我知道了")
        and i.score >= .85
        and width * .15 < i.center[0] < width * .85
        and titles[0].center[1] + 30 < i.center[1] < height * .95
    ]
    return buttons[0] if len(buttons) == 1 else None


class MaaEndPopupMonitor:
    def __init__(self, debug_dir: str | None = None) -> None:
        self.ui = MaaEndUI()
        self.debug_dir = Path(debug_dir) if debug_dir else None
        self.closed: list[dict] = []
        self._next_scan = 0.0

    def scan_once(self, *, foreground: bool = False) -> bool:
        if not foreground and time.monotonic() < self._next_scan:
            return False
        self._next_scan = time.monotonic() + 5
        hwnd = self.ui.hwnd()
        if hwnd is None:
            return False
        if foreground:
            self.ui.foreground(hwnd)
        # 屏幕截图必须属于目标窗口；运行中不能抢走游戏焦点。
        if win32gui.GetForegroundWindow() != hwnd:
            return False
        rect = win32gui.GetWindowRect(hwnd)
        image = capture_window_screen(hwnd)
        if image is None:
            return False
        button = update_acknowledgement(recognize(image), *image.size)
        if button is None:
            return False
        if self.debug_dir:
            self.debug_dir.mkdir(parents=True, exist_ok=True)
            image.save(self.debug_dir / f"maaend_update_{time.time_ns()}.png")
        # OCR 期间用户可能切换窗口或移动窗口，旧坐标必须作废。
        if win32gui.GetForegroundWindow() != hwnd or win32gui.GetWindowRect(hwnd) != rect:
            return False
        _click_screen(rect[0] + button.center[0], rect[1] + button.center[1])
        time.sleep(.8)
        if not win32gui.IsWindow(hwnd) or win32gui.GetForegroundWindow() != hwnd:
            logger.warning("终末地：更新提示点击后窗口发生变化，暂不能确认关闭")
            return False
        after = capture_window_screen(hwnd)
        if after is None:
            return False
        items = recognize(after)
        if not items or any(i.text.strip() == "更新完成" for i in items):
            logger.warning("终末地：更新完成提示仍未确认消失，下次重新识别")
            return False
        self.closed.append({"title": "更新完成", "kind": "update_complete",
                            "button": button.text})
        logger.info("终末地：已关闭并确认「更新完成」提示")
        return True
