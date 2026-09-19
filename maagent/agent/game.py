"""Game perception/action service: launch MuMu, OCR the screen, tap Arknights.

Heavy dependencies (MaaFw, rapidocr) are imported lazily so that merely
registering the tools never fails on a machine without an emulator.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from loguru import logger

DEFAULT_MANAGER = "D:/tools/MuMuPlayer/nx_main/MuMuManager.exe"
DEFAULT_ADB = "D:/tools/MuMuPlayer/nx_main/adb.exe"
DEFAULT_PACKAGE = "com.hypergryph.arknights"

_FOCUS_RE = re.compile(r"(?:mCurrentFocus|mFocusedApp)=.*?\s([\w.]+)/[\w.$]+")


class GameService:
    def __init__(self, config: dict[str, Any], llm: Any = None) -> None:
        maa = (config.get("adapters") or {}).get("maa") or {}
        emu = maa.get("emulator") or {}
        game = (config.get("agent") or {}).get("game") or {}
        self.manager = game.get("manager_path") or emu.get("manager_path") or DEFAULT_MANAGER
        self.adb = game.get("adb_path") or emu.get("adb_path") or DEFAULT_ADB
        self.vmindex = int(game.get("vmindex", emu.get("vmindex", 0)) or 0)
        self.package = game.get("package") or DEFAULT_PACKAGE
        self.screenshot_dir = Path(game.get("screenshot_dir") or "logs/agent_game")
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        self.llm = llm
        self.address: str | None = None
        self._game = None

    # ---------- emulator process ----------
    def _mumu_info(self) -> dict[str, Any]:
        try:
            out = subprocess.run(
                [str(self.manager), "info", "-v", str(self.vmindex)],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
            ).stdout
            return json.loads(out)
        except Exception as e:
            logger.warning("读取 MuMu 状态失败: {}", e)
            return {}

    def emulator_running(self) -> bool:
        return bool(self._mumu_info().get("is_android_started"))

    def start_emulator(self, timeout: float = 120.0) -> bool:
        if self.emulator_running():
            return True
        logger.info("启动 MuMu 实例 {} ...", self.vmindex)
        try:
            subprocess.run(
                [str(self.manager), "control", "-v", str(self.vmindex), "launch"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
            )
        except Exception as e:
            logger.error("启动模拟器失败: {}", e)
            return False
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.emulator_running():
                logger.info("模拟器已启动")
                return True
            time.sleep(3.0)
        logger.warning("等待模拟器启动超时")
        return False

    # ---------- controller / perception ----------
    def _ensure_game(self):
        if self._game is not None:
            return self._game
        from maagent.agent.framework import MaaGameController, resolve_mumu_serial

        if not self.address:
            self.address = resolve_mumu_serial(self.manager, self.adb, self.vmindex)
        if not self.address:
            raise RuntimeError("未发现模拟器设备，请确认模拟器已启动")
        game = MaaGameController(self.adb, self.address, screenshot_dir=str(self.screenshot_dir))
        if not game.connect():
            raise RuntimeError(f"连接模拟器失败: {self.address}")
        self._game = game
        return game

    def _adb(self, *args: str, timeout: float = 20.0) -> str:
        if not self.address:
            self._ensure_game()
        return subprocess.run(
            [str(self.adb), "-s", str(self.address), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
        ).stdout

    def screenshot(self):
        return self._ensure_game().screenshot(save=True)

    def ocr(self):
        from maagent.control.popup import recognize

        return recognize(self.screenshot())

    def find_text(self, text: str):
        from maagent.control.popup import find_text as _find

        return _find(self.ocr(), text)

    def find_all_text(self, text: str) -> list:
        return [i for i in self.ocr() if text in i.text]

    def click(self, x: int, y: int) -> None:
        self._ensure_game().click(int(x), int(y))

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration: int = 300) -> None:
        self._ensure_game().swipe(int(x1), int(y1), int(x2), int(y2), int(duration))

    def press_key(self, key: int) -> None:
        self._ensure_game().press_key(int(key))

    def shell(self, cmd: str) -> str:
        return self._adb("shell", cmd)

    def find_and_click(self, text: str, wait: float = 1.5) -> bool:
        item = self.find_text(text)
        if item is None:
            return False
        self.click(*item.center)
        time.sleep(wait)
        return True

    def foreground_package(self) -> str:
        out = self.shell("dumpsys window")
        match = _FOCUS_RE.search(out)
        return match.group(1) if match else ""

    def _start_app(self) -> None:
        self._adb(
            "shell", "monkey", "-p", self.package,
            "-c", "android.intent.category.LAUNCHER", "1",
        )

    def _go_home(self) -> None:
        self._adb("shell", "input", "keyevent", "3")
        time.sleep(2.0)

    def _wait_foreground(self, timeout: float = 30.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.foreground_package() == self.package:
                return True
            time.sleep(3.0)
        return False

    # ---------- high-level actions ----------
    def open_arknights(self) -> str:
        if not self.start_emulator():
            return "启动 MuMu 模拟器失败。"
        time.sleep(3.0)
        try:
            self._ensure_game()
        except Exception as e:
            return f"连接模拟器失败：{e}"

        if self.foreground_package() == self.package:
            return "明日方舟已经在运行了。"

        # 先回到桌面，再靠视觉识别点击「明日方舟」图标（可能有多个，逐个尝试）
        self._go_home()
        for item in self.find_all_text("明日方舟"):
            self.click(*item.center)
            if self._wait_foreground(15):
                logger.info("通过视觉识别打开明日方舟 @{}", item.center)
                return "已打开明日方舟（视觉识别）。"
            self._go_home()

        # 兜底：回桌面后用包名启动
        logger.info("视觉识别未成功，改用包名启动")
        self._go_home()
        try:
            self._start_app()
        except Exception as e:
            return f"打开明日方舟失败：{e}"
        if not self._wait_foreground(60):
            return "已尝试打开明日方舟，但未能确认进入（可能仍在加载或需要登录）。"
        return "已打开明日方舟（包名启动）。"

    def analyze_screen(self, question: str) -> str:
        if self.llm is None:
            return "视觉功能未启用。"
        from maagent.agent.llm import image_data_url

        image = self.screenshot()
        prompt = f"这是安卓模拟器（MuMu）的当前画面截图。{question}\n请用中文简洁、准确地回答。"
        reply = self.llm.chat(
            [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_data_url(image)}},
                ],
            }],
            model=self.llm.vision_model,
        )
        return reply.content
