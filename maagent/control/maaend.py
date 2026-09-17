from __future__ import annotations

import time
from typing import Any

import psutil
import win32api
import win32con
import win32gui
from loguru import logger
from PIL import Image, ImageGrab

from maagent.control.popup import (
    _click_screen,
    ensure_visible,
    find_text,
    main_window,
    recognize,
)

PROCESS_NAME = "MaaEnd.exe"
GAME_PROCESS = "Endfield.exe"

# MaaEnd global hotkeys (config/mxu-MaaEnd.json -> settings.hotkeys)
START_HOTKEY = "F10"
STOP_HOTKEY = "F11"

# fractional regions of the MaaEnd main window
START_BUTTON_REGION = (0.52, 0.86, 1.0, 1.0)
START_LABELS = ("开始任务", "开始")
STOP_LABELS = ("停止任务", "停止")


def process_running(name: str) -> bool:
    target = name.lower()
    for proc in psutil.process_iter(["name"]):
        try:
            if (proc.info.get("name") or "").lower() == target:
                return True
        except Exception:
            continue
    return False


def vk_for(name: str) -> int | None:
    name = (name or "").strip().upper()
    if len(name) >= 2 and name[0] == "F" and name[1:].isdigit():
        n = int(name[1:])
        if 1 <= n <= 24:
            return 0x70 + n - 1
    if len(name) == 1 and name.isalnum():
        return ord(name)
    return None


def press_hotkey(name: str) -> bool:
    """Send a global hotkey (works regardless of which window has focus)."""
    vk = vk_for(name)
    if vk is None:
        logger.warning("MaaEnd：无法解析热键 {}", name)
        return False
    win32api.keybd_event(vk, 0, 0, 0)
    time.sleep(0.05)
    win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)
    return True


def _region(image: Image.Image, frac: tuple[float, float, float, float]):
    w, h = image.size
    l, t, r, b = frac
    return int(l * w), int(t * h), int(r * w), int(b * h)


def capture_window_screen(hwnd: int) -> Image.Image | None:
    """Screen-grab the window region (PrintWindow fails on MaaEnd's Tauri/WebView)."""
    try:
        l, t, r, b = win32gui.GetWindowRect(hwnd)
        if r - l <= 0 or b - t <= 0:
            return None
        return ImageGrab.grab(bbox=(l, t, r, b), all_screens=True)
    except Exception as e:
        logger.warning("MaaEnd：屏幕截图失败: {}", e)
        return None


class MaaEndUI:
    def __init__(self, process_name: str = PROCESS_NAME) -> None:
        self.process_name = process_name

    def hwnd(self) -> int | None:
        return main_window(self.process_name)

    def is_running(self) -> bool:
        return self.hwnd() is not None

    def foreground(self, hwnd: int) -> None:
        ensure_visible(hwnd)
        if win32gui.GetForegroundWindow() == hwnd:
            time.sleep(0.3)
            return
        # a plain SetForegroundWindow is denied by the Windows foreground lock;
        # tapping ALT first releases it (standard workaround).
        try:
            win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
            win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
            win32gui.SetForegroundWindow(hwnd)
        except Exception as e:
            logger.debug("MaaEnd：置前失败 {}", e)
        time.sleep(0.8)

    def press_start(self) -> bool:
        """Click MaaEnd's 开始任务 button (it then runs preActions + tasks)."""
        hwnd = self.hwnd()
        if hwnd is None:
            return False
        self.foreground(hwnd)
        try:
            img = capture_window_screen(hwnd)
            if img is not None:
                l, t, r, b = _region(img, START_BUTTON_REGION)
                items = recognize(img.crop((l, t, r, b)))
                item = None
                for name in START_LABELS:
                    item = find_text(items, name)
                    if item is not None:
                        break
                if item is not None:
                    wx, wy, _, _ = win32gui.GetWindowRect(hwnd)
                    _click_screen(wx + l + item.center[0], wy + t + item.center[1])
                    logger.info("MaaEnd：已点击「开始任务」")
                    return True
                logger.warning("MaaEnd：未识别到「开始任务」按钮")
        except Exception as e:
            logger.warning("MaaEnd：点击「开始任务」失败: {}", e)
        return False
