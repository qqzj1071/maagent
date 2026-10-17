from __future__ import annotations

import ctypes
import os

import win32gui
import win32process

# 单实例互斥体（同一用户会话内共享，maagent 始终提权运行，命名空间一致）。
MUTEX_NAME = "Local\\Maagent-SingleInstance-Mutex"
# 自定义窗口消息：通知已运行的实例把主窗口拉到前台。
WM_MAAGENT_SHOW = 0x8000 + 0x1234

_mutex: int | None = None


def acquire() -> bool:
    """获取单实例互斥体；返回 False 表示已有其它实例在运行。

    句柄保存在模块级变量中，进程存续期间不释放，进程退出时由系统回收。
    """
    global _mutex
    _mutex = int(ctypes.windll.kernel32.CreateMutexW(None, False, MUTEX_NAME))
    return ctypes.windll.kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS


def _area(hwnd: int) -> int:
    try:
        l, t, r, b = win32gui.GetWindowRect(hwnd)
        return (r - l) * (b - t)
    except Exception:
        return 0


def find_existing_window() -> int | None:
    """按标题中的品牌名「Maagent」找到已运行实例的主窗口（跨语言稳定）。"""
    current = os.getpid()
    found: list[int] = []

    def cb(hwnd: int, _) -> bool:
        if not win32gui.IsWindow(hwnd):
            return True
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        if pid == current:
            return True
        if "Maagent" not in win32gui.GetWindowText(hwnd):
            return True
        found.append(hwnd)
        return True

    win32gui.EnumWindows(cb, None)
    return max(found, key=_area) if found else None


def notify_existing() -> bool:
    """通知已运行的实例显示主窗口并置前；返回是否找到目标窗口。"""
    from maagent.control.popup import _force_foreground, ensure_visible

    hwnd = find_existing_window()
    if hwnd is None:
        return False
    try:
        # 让已运行实例用 Qt 恢复窗口状态（最小化到托盘时窗口是隐藏的）
        ctypes.windll.user32.PostMessageW(hwnd, WM_MAAGENT_SHOW, 0, 0)
        ensure_visible(hwnd)
        # 新实例刚由用户启动，拥有前台激活权限，直接置前
        _force_foreground(hwnd)
    except Exception:
        pass
    return True
