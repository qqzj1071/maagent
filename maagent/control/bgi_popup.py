"""只确认截图中已知的 Multiple games (3,0,0) 前台错误框。"""
from __future__ import annotations

import re
import time

import win32gui
from loguru import logger

from maagent.control.maaend import capture_window_screen
from maagent.control.popup import _click_screen, recognize


def is_multiple_games(text: str) -> bool:
    text = re.sub(r"\s+", "", text).casefold()
    return text == (
        "multiplegamesaredetected.pleaseexittheexistingclient.errorcode:(3,0,0)"
    )


class BgiPopupMonitor:
    def __init__(self) -> None:
        self.closed: list[dict] = []
        self._next_scan = 0.0
        self._attempts = 0

    def scan_once(self) -> bool:
        if time.monotonic() < self._next_scan or self._attempts >= 3:
            return False
        self._next_scan = time.monotonic() + 2
        try:
            return self._scan()
        except Exception as exc:
            # 窗口随时可能被用户关闭；检测失败不应中断日常。
            logger.debug("原神：弹窗检查暂不可用: {}", exc)
            return False

    def _scan(self) -> bool:
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd or win32gui.GetClassName(hwnd) != "#32770":
            return False
        if win32gui.GetWindowText(hwnd).strip() not in ("错误", "Error"):
            return False
        rect = win32gui.GetWindowRect(hwnd)
        children = []
        win32gui.EnumChildWindows(hwnd, lambda child, _: children.append(child) or True, None)
        buttons = [h for h in children if win32gui.GetClassName(h) == "Button"
                   and win32gui.GetWindowText(h).replace("&", "").strip() in ("确定", "OK")
                   and win32gui.IsWindowVisible(h) and win32gui.IsWindowEnabled(h)]
        if len(buttons) != 1:
            return False
        button = buttons[0]
        body = "".join(win32gui.GetWindowText(h) for h in children
                       if win32gui.GetClassName(h) == "Static")
        if not is_multiple_games(body):
            # 系统硬错误框可能无法读取 Static 文本，用前台窗口截图补充识别。
            img = capture_window_screen(hwnd)
            if img is None:
                return False
            items = recognize(img)
            body = "".join(i.text for i in items if i.score >= .85
                           and i.text.strip() not in ("错误", "Error", "确定", "OK", "X", "×"))
            if not is_multiple_games(body):
                return False
        button_rect = win32gui.GetWindowRect(button)
        left, top, right, bottom = button_rect
        if not (rect[0] <= left < right <= rect[2] and rect[1] <= top < bottom <= rect[3]):
            return False
        if (win32gui.GetForegroundWindow() != hwnd or win32gui.GetWindowRect(hwnd) != rect
                or not win32gui.IsChild(hwnd, button)
                or win32gui.GetWindowRect(button) != button_rect):
            return False
        self._attempts += 1
        logger.warning("原神：检测到 Multiple games (3,0,0)，点击「确定」（第 {} 次）", self._attempts)
        _click_screen((left + right) // 2, (top + bottom) // 2)
        time.sleep(.5)
        if win32gui.IsWindow(hwnd) and win32gui.IsWindowVisible(hwnd):
            logger.warning("原神：错误弹窗尚未消失，将重新识别；最多尝试 3 次")
            return False
        self.closed.append({"title": "Multiple games (3,0,0)",
                            "kind": "multiple_games", "button": "确定"})
        logger.info("原神：Multiple games 弹窗已关闭，继续监测 BGI 任务日志")
        return True
