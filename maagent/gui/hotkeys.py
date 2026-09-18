from __future__ import annotations

import ctypes
from ctypes import wintypes

from loguru import logger
from PySide6.QtCore import QAbstractNativeEventFilter, QObject, Signal
from PySide6.QtWidgets import QApplication

WM_HOTKEY = 0x0312
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

user32 = ctypes.windll.user32

_VK: dict[str, int] = {}
for _i in range(1, 13):
    _VK[f"F{_i}"] = 0x6F + _i
for _c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
    _VK[_c] = ord(_c)
for _d in "0123456789":
    _VK[_d] = ord(_d)
_VK.update({"SPACE": 0x20, "ENTER": 0x0D, "TAB": 0x09, "ESC": 0x1B})


def parse_hotkey(text: str) -> tuple[int, int] | None:
    """Parse "F8" / "Ctrl+Shift+A" into (modifiers, virtual-key) or None."""
    if not text:
        return None
    parts = [p.strip().upper() for p in str(text).split("+") if p.strip()]
    if not parts:
        return None
    modifiers = 0
    for part in parts[:-1]:
        if part in ("CTRL", "CONTROL"):
            modifiers |= MOD_CONTROL
        elif part == "ALT":
            modifiers |= MOD_ALT
        elif part == "SHIFT":
            modifiers |= MOD_SHIFT
        elif part in ("WIN", "META", "SUPER"):
            modifiers |= MOD_WIN
        else:
            return None
    vk = _VK.get(parts[-1])
    if vk is None:
        return None
    return modifiers, vk


class _HotkeyFilter(QAbstractNativeEventFilter):
    def __init__(self, callback) -> None:
        super().__init__()
        self._callback = callback

    def nativeEventFilter(self, event_type, message):  # noqa: N802
        try:
            msg = ctypes.cast(int(message), ctypes.POINTER(wintypes.MSG)).contents
        except Exception:
            return False
        if msg.message == WM_HOTKEY:
            self._callback(int(msg.wParam))
        return False


class GlobalHotkeys(QObject):
    """System-wide hotkeys on Windows via RegisterHotKey + WM_HOTKEY."""

    triggered = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._ids: dict[int, str] = {}
        self._next_id = 0x4D41
        self._filter = _HotkeyFilter(self._on_hotkey)
        app = QApplication.instance()
        if app is not None:
            app.installNativeEventFilter(self._filter)

    def register(self, name: str, hotkey: str) -> bool:
        self.unregister(name)
        parsed = parse_hotkey(hotkey)
        if parsed is None:
            logger.warning("快捷键无法解析: {} = {}", name, hotkey)
            return False
        modifiers, vk = parsed
        hotkey_id = self._next_id
        self._next_id += 1
        if not user32.RegisterHotKey(None, hotkey_id, modifiers | MOD_NOREPEAT, vk):
            logger.warning("快捷键注册失败（可能被占用）: {} = {}", name, hotkey)
            return False
        self._ids[hotkey_id] = name
        logger.info("已注册全局快捷键: {} = {}", name, hotkey)
        return True

    def unregister(self, name: str) -> None:
        for hotkey_id, existing in list(self._ids.items()):
            if existing == name:
                user32.UnregisterHotKey(None, hotkey_id)
                self._ids.pop(hotkey_id, None)

    def unregister_all(self) -> None:
        for hotkey_id in list(self._ids):
            user32.UnregisterHotKey(None, hotkey_id)
        self._ids.clear()

    def _on_hotkey(self, hotkey_id: int) -> None:
        name = self._ids.get(hotkey_id)
        if name:
            self.triggered.emit(name)
