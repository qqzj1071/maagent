from __future__ import annotations

import ctypes
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from gameops.adapters.base import Adapter, RunResult, RunStatus

VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_L = 0x4C
KEYEVENTF_KEYUP = 0x0002


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class MaaAdapter(Adapter):
    name = "maa"

    def __init__(self) -> None:
        self._config: dict[str, Any] = {}

    def start(self, config: dict[str, Any]) -> None:
        self._config = config

    def stop(self) -> None:
        pass

    def _is_running(self) -> bool:
        try:
            out = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq MAA.exe", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=10,
            ).stdout
            return "MAA.exe" in out
        except Exception as e:
            logger.warning("检查 MAA 进程失败: {}", e)
            return False

    def _launch(self) -> bool:
        exe = self._config.get("executable") or str(Path(self._config["path"]) / "MAA.exe")
        try:
            subprocess.Popen([exe], cwd=str(self._config["path"]))
            return True
        except Exception as e:
            logger.error("启动 MAA 失败: {}", e)
            return False

    def press_link_start(self) -> None:
        user32 = ctypes.windll.user32
        for vk in (VK_MENU, VK_CONTROL, VK_SHIFT):
            user32.keybd_event(vk, 0, 0, 0)
        user32.keybd_event(VK_L, 0, 0, 0)
        user32.keybd_event(VK_L, 0, KEYEVENTF_KEYUP, 0)
        for vk in (VK_SHIFT, VK_CONTROL, VK_MENU):
            user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)

    def launch(self) -> bool:
        if self._is_running():
            logger.info("MAA 已在运行")
            return True
        if not self._launch():
            return False
        wait = int(self._config.get("wait_seconds", 10))
        logger.info("已启动 MAA，等待 {} 秒加载...", wait)
        time.sleep(wait)
        return True

    def run(self, task: dict[str, Any]) -> RunResult:
        started = _now()
        name = task.get("name", self.name)
        if not self.launch():
            return RunResult(
                self.name, name, RunStatus.FAILED, started, _now(), "启动 MAA 失败", {},
            )
        self.press_link_start()
        logger.info("已触发 Link Start 热键 (Alt+Ctrl+Shift+L)")
        return RunResult(
            self.name, name, RunStatus.SUCCESS, started, _now(),
            "已启动 MAA 并触发 Link Start", {},
        )
