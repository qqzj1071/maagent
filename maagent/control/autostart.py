from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger

try:
    import winreg
except ImportError:  # non-Windows fallback
    winreg = None  # type: ignore[assignment]

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "maagent"


def _command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    python = Path(sys.executable)
    pythonw = python.with_name("pythonw.exe")
    exe = pythonw if pythonw.exists() else python
    return f'"{exe}" -m maagent.gui.app'


def is_enabled() -> bool:
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, VALUE_NAME)
            return True
    except OSError:
        return False


def set_enabled(enabled: bool) -> bool:
    if winreg is None:
        logger.warning("当前平台不支持开机自启动设置")
        return False
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            if enabled:
                winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, _command())
                logger.info("已设置开机自启动: {}", _command())
            else:
                try:
                    winreg.DeleteValue(key, VALUE_NAME)
                    logger.info("已取消开机自启动")
                except FileNotFoundError:
                    pass
        return True
    except OSError as e:
        logger.warning("设置开机自启动失败: {}", e)
        return False
