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

PACKAGE_OFFICIAL = "com.hypergryph.arknights"
PACKAGE_BILIBILI = "com.hypergryph.arknights.bilibili"

_FOCUS_RE = re.compile(r"(?:mCurrentFocus|mFocusedApp)=.*?\s([\w.]+)/[\w.$]+")

START_TEXTS = ("START", "开始唤醒", "开始", "点击开始", "触摸开始")
MAIN_TEXTS = ("终端", "作战", "基建", "干员", "档案", "商店", "寻访", "招募", "任务", "好友")
LIST_TEXTS = ("等级", "稀有度", "信赖值")
DETAIL_TEXTS = ("属性", "技能", "潜能", "精英化", "天赋", "模组", "专精")

# 干员列表右侧职业筛选图标（x≈1857）的 y 坐标，实测得出
PROFESSION_FILTER_X = 1857
PROFESSION_Y = {
    "先锋": 230,
    "近卫": 343,
    "重装": 455,
    "狙击": 568,
    "术师": 680,
    "医疗": 793,
    "辅助": 930,
    "特种": 1045,
}
ALL_FILTER_Y = 136


def _is_start_button(text: str) -> bool:
    t = text.strip()
    return t in START_TEXTS or "开始唤醒" in t


class GameService:
    def __init__(self, config: dict[str, Any], llm: Any = None, knowledge: Any = None) -> None:
        maa = (config.get("adapters") or {}).get("maa") or {}
        emu = maa.get("emulator") or {}
        game = (config.get("agent") or {}).get("game") or {}
        self.manager = game.get("manager_path") or emu.get("manager_path") or DEFAULT_MANAGER
        self.adb = game.get("adb_path") or emu.get("adb_path") or DEFAULT_ADB
        self.vmindex = int(game.get("vmindex", emu.get("vmindex", 0)) or 0)
        self.package = game.get("package") or DEFAULT_PACKAGE
        self.window_process = game.get("window_process") or "MuMuNxDevice.exe"
        self.focus_before_capture = bool(game.get("focus_before_capture", True))
        self.screenshot_dir = Path(game.get("screenshot_dir") or "logs/agent_game")
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        self.max_screenshots = int(game.get("max_screenshots", 50))
        self.llm = llm
        self.knowledge = knowledge
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

    def _prune_screenshots(self) -> None:
        try:
            files = sorted(self.screenshot_dir.glob("screen_*.png"))
            for old in files[: max(0, len(files) - self.max_screenshots)]:
                old.unlink(missing_ok=True)
        except Exception as e:
            logger.debug("清理旧截图失败: {}", e)

    def screenshot(self):
        image = self._ensure_game().screenshot(save=True)
        self._prune_screenshots()
        return image

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

    def _go_home(self) -> None:
        self._adb("shell", "input", "keyevent", "3")
        time.sleep(2.0)

    def _wait_foreground(self, timeout: float = 30.0, package: str | None = None) -> bool:
        target = package or self.package
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.foreground_package() == target:
                return True
            time.sleep(3.0)
        return False

    def _resolve_package(self, version: str | None) -> tuple[str, str]:
        """Return (package, label) for a version request like 官服 / B服."""
        v = (version or "").strip().lower()
        if "b" in v or "哔" in v or "bili" in v:
            return PACKAGE_BILIBILI, "B服"
        if "官" in v or "official" in v:
            return PACKAGE_OFFICIAL, "官服"
        return self.package, "明日方舟"

    def _start_app_pkg(self, package: str) -> None:
        self._adb(
            "shell", "monkey", "-p", package,
            "-c", "android.intent.category.LAUNCHER", "1",
        )

    def enter_game(self, timeout: float = 150.0, max_clicks: int = 4) -> str:
        """Click the START / 开始唤醒 button until the main interface appears."""
        deadline = time.time() + timeout
        clicks = 0
        while time.time() < deadline:
            items = self.ocr()
            texts = [i.text for i in items]
            if sum(1 for kw in MAIN_TEXTS if any(kw in t for t in texts)) >= 2:
                return "已进入明日方舟主界面。"
            if clicks < max_clicks:
                hit = next((i for i in items if _is_start_button(i.text)), None)
                if hit is not None:
                    self.click(*hit.center)
                    clicks += 1
                    logger.info("点击开始按钮「{}」@{}", hit.text.strip(), hit.center)
                    time.sleep(5.0)
                    continue
            time.sleep(3.0)
        return "已尝试进入主界面，但未能确认（可能仍在加载、需要登录或更新）。"

    def _finish_open(self, label: str, enter: bool) -> str:
        base = f"已打开明日方舟{label}。" if label else "已打开明日方舟。"
        if not enter:
            return base
        return base + " " + self.enter_game()

    # ---------- high-level actions ----------
    def open_arknights(self, version: str | None = None, enter: bool = True) -> str:
        package, label = self._resolve_package(version)
        if not self.start_emulator():
            return "启动 MuMu 模拟器失败。"
        time.sleep(3.0)
        try:
            self._ensure_game()
        except Exception as e:
            return f"连接模拟器失败：{e}"

        if self.foreground_package() == package:
            if enter:
                return f"明日方舟{label}已经在运行。" + " " + self.enter_game()
            return f"明日方舟{label}已经在运行了。"

        # 指定了版本：图标无法用 OCR 区分官服/B服，直接按包名启动
        if version:
            logger.info("按版本「{}」用包名 {} 启动", label, package)
            try:
                self._start_app_pkg(package)
            except Exception as e:
                return f"打开明日方舟{label}失败：{e}"
            if self._wait_foreground(180, package):
                return self._finish_open(label, enter)
            return f"已尝试打开明日方舟{label}，但未能确认进入（可能未安装或需要登录）。"

        # 未指定版本：先靠视觉识别点击「明日方舟」图标（可能有多个，逐个尝试）
        self._go_home()
        for item in self.find_all_text("明日方舟"):
            self.click(*item.center)
            if self._wait_foreground(15, package):
                logger.info("通过视觉识别打开明日方舟 @{}", item.center)
                return self._finish_open("", enter)
            self._go_home()

        # 兜底：回桌面后用默认包名启动
        logger.info("视觉识别未成功，改用包名启动")
        self._go_home()
        try:
            self._start_app_pkg(package)
        except Exception as e:
            return f"打开明日方舟失败：{e}"
        if not self._wait_foreground(180, package):
            return "已尝试打开明日方舟，但未能确认进入（可能仍在加载或需要登录）。"
        return self._finish_open("", enter)

    def focus_emulator(self) -> bool:
        """Bring the MuMu window to the foreground (for visual inspection)."""
        try:
            from maagent.control.popup import _force_foreground, ensure_visible, main_window

            hwnd = main_window(self.window_process)
            if not hwnd:
                return False
            ensure_visible(hwnd)
            _force_foreground(hwnd)
            time.sleep(0.8)
            return True
        except Exception as e:
            logger.warning("置前模拟器窗口失败: {}", e)
            return False

    # ---------- operator list / detail ----------
    def _texts(self) -> list[str]:
        return [i.text.strip() for i in self.ocr()]

    def _is_main_ui(self, texts: list[str]) -> bool:
        return any(t == "编队" for t in texts) or any(t == "终端" for t in texts)

    def _is_operator_list(self, texts: list[str]) -> bool:
        # 列表顶部有「等级」「稀有度」排序标签；详情页只有「信赖值」，不会误判
        return "等级" in texts and "稀有度" in texts

    def _confirm_dialog(self) -> bool:
        items = self.ocr()
        hit = next((i for i in items if i.text.strip() in ("确认", "确定", "是")), None)
        if hit is not None:
            self.click(*hit.center)
            time.sleep(1.5)
            return True
        return False

    def goto_operator_list(self, tries: int = 10) -> bool:
        for _ in range(tries):
            items = self.ocr()
            texts = [i.text.strip() for i in items]
            if self._is_operator_list(texts):
                return True
            if self._confirm_dialog():
                continue
            if self._is_main_ui(texts):
                cand = next(
                    (i for i in items
                     if i.text.strip() == "干员" and i.center[0] > 1300 and 430 < i.center[1] < 620),
                    None,
                )
                if cand is not None:
                    self.click(*cand.center)
                    time.sleep(3.0)
                    continue
            self.click(75, 55)  # 左上角返回
            time.sleep(1.8)
        return self._is_operator_list(self._texts())

    def _is_operator_detail(self, name: str) -> bool:
        texts = self._texts()
        if sum(1 for k in DETAIL_TEXTS if k in texts) >= 2:
            return True
        return any(name in t for t in texts) and sum(1 for k in DETAIL_TEXTS if k in texts) >= 1

    def lookup_profession(self, name: str) -> str | None:
        """Look up an operator's 职业 from the knowledge base."""
        if self.knowledge is None:
            return None
        doc = f"干员：{name}"
        for chunk in getattr(self.knowledge, "chunks", []):
            if chunk.get("doc") == doc:
                m = re.search(r"职业：([^\s；]+)", chunk.get("text", ""))
                if m:
                    return m.group(1).strip()
                break
        return None

    def filter_by_profession(self, profession: str) -> bool:
        y = PROFESSION_Y.get(profession)
        if y is None:
            return False
        for _ in range(2):
            self.click(PROFESSION_FILTER_X, y)
            time.sleep(1.5)
            if self._is_operator_list(self._texts()):
                return True
            # 误触卡片进了详情页 → 返回列表再试
            self.click(75, 55)
            time.sleep(1.8)
        return False

    def open_operator(self, name: str, scrolls: int = 6) -> str:
        name = (name or "").strip()
        if not name:
            return "请提供干员名称。"
        if not self.start_emulator():
            return "启动 MuMu 模拟器失败。"
        try:
            self._ensure_game()
        except Exception as e:
            return f"连接模拟器失败：{e}"
        if self.focus_before_capture:
            self.focus_emulator()
        if not self.goto_operator_list():
            return "未能进入干员列表。"

        profession = self.lookup_profession(name)
        if profession:
            if self.filter_by_profession(profession):
                logger.info("按职业「{}」筛选干员列表", profession)
            else:
                self.goto_operator_list()

        # 先滚回列表顶部，再向下搜索
        for _ in range(8):
            self.swipe(960, 320, 960, 820, 300)
            time.sleep(0.3)
        time.sleep(1.0)

        for _ in range(scrolls + 1):
            items = self.ocr()
            hit = next(
                (i for i in items
                 if i.text.strip() == name
                 or (len(name) >= 2 and name in i.text and len(i.text.strip()) <= len(name) + 4)),
                None,
            )
            if hit is not None:
                x, y = hit.center
                self.click(x, max(120, y - 220))  # 点卡片立绘区域
                time.sleep(3.0)
                if self._is_operator_detail(name):
                    return f"已打开干员「{name}」的详情页。"
                if not self._is_operator_list(self._texts()):
                    self.click(75, 55)  # 点错进了别的页面，返回列表
                    time.sleep(1.8)
            self.swipe(960, 820, 960, 320, 450)
            time.sleep(1.2)
        return f"在干员列表中未找到「{name}」（可能需要先筛选或该干员未拥有）。"

    DETAIL_PROMPT = (
        "这是《明日方舟》干员详情页截图。请仔细读取并逐项用中文回答：\n"
        "1. 等级（例如 90/90）\n"
        "2. 精英化等级（精英化0/1/2 或 MAX）\n"
        "3. 潜能数（1~6，看潜能区域点亮的钻石数量）\n"
        "4. 信赖值（百分比）\n"
        "5. 技能：共几个技能，每个技能的专精等级（专精0/1/2/3）与技能等级（RANK）\n"
        "看不清的项写「看不清」。"
    )
    MODULE_PROMPT = (
        "这是《明日方舟》干员的模组界面截图。请逐项用中文回答：\n"
        "1. 当前装备的模组名称与阶段（STAGE）\n"
        "2. 模组提供的特性\n"
        "3. 模组相关的天赋及数值\n"
        "看不清的项写「看不清」。"
    )

    def _vision_ask(self, image, prompt: str) -> str:
        if self.llm is None:
            return "视觉功能未启用。"
        from maagent.agent.llm import image_data_url

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

    @staticmethod
    def _crop(image, box, scale: int = 2):
        region = image.crop(box)
        if scale != 1:
            region = region.resize((region.width * scale, region.height * scale))
        return region

    def _open_module_view(self) -> bool:
        hit = next(
            (i for i in self.ocr() if i.text.strip() == "管理" or "模组详情" in i.text),
            None,
        )
        self.click(*hit.center) if hit is not None else self.click(1474, 853)
        time.sleep(2.5)
        texts = self._texts()
        return any("模组" in t for t in texts) or any("STAGE" in t for t in texts)

    @staticmethod
    def _count_dots(badge, thr: int = 190, min_size: int = 4) -> int:
        import numpy as np

        gray = np.array(badge.convert("L"))
        mask = gray > thr
        h, w = mask.shape
        visited = np.zeros_like(mask, dtype=bool)
        count = 0
        for y in range(h):
            for x in range(w):
                if mask[y, x] and not visited[y, x]:
                    stack = [(y, x)]
                    visited[y, x] = True
                    size = 0
                    while stack:
                        cy, cx = stack.pop()
                        size += 1
                        for dy in (-1, 0, 1):
                            for dx in (-1, 0, 1):
                                ny, nx = cy + dy, cx + dx
                                if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not visited[ny, nx]:
                                    visited[ny, nx] = True
                                    stack.append((ny, nx))
                    if size >= min_size:
                        count += 1
        return count

    def _skill_mastery(self, image) -> list[int]:
        """每个技能专精等级 = 技能图标左上角徽章的亮点数（0~3）。"""
        import numpy as np

        mastery: list[int] = []
        for x0 in (1329, 1460, 1591):
            badge = image.crop((x0, 499, x0 + 30, 533))
            if np.array(badge.convert("L")).max() < 90:  # 该位置没有专精徽章 → 没有更多技能
                break
            mastery.append(self._count_dots(badge, thr=170))
        return mastery

    def _read_potential(self) -> str:
        self.click(1816, 400)
        time.sleep(2.5)
        items = self.ocr()
        number = next(
            (i.text.strip() for i in items
             if re.fullmatch(r"\d", i.text.strip()) and 1250 < i.center[0] < 1600 and 500 < i.center[1] < 700),
            None,
        )
        self.click(1413, 945)  # 取消，绝不消耗信物
        time.sleep(1.8)
        return number or "?"

    def get_operator_training(self, name: str) -> str:
        result = self.open_operator(name)
        if "详情页" not in result:
            return result

        image = self.screenshot()
        items = self.ocr()
        level = next(
            (i.text.strip() for i in items
             if re.fullmatch(r"\d{1,3}", i.text.strip()) and 1300 < i.center[0] < 1500 and 180 < i.center[1] < 260),
            "?",
        )
        trust = next(
            (i.text.strip() for i in items
             if "%" in i.text and 400 < i.center[0] < 720 and 580 < i.center[1] < 660),
            "?",
        )
        elite = next(
            (i.text.strip() for i in items
             if 1480 < i.center[0] < 1650 and 405 < i.center[1] < 465),
            "?",
        )
        mastery = self._skill_mastery(image)
        potential = self._read_potential()

        module = ""
        if self._open_module_view():
            module = self.analyze_screen(self.MODULE_PROMPT)
            self.click(75, 55)
            time.sleep(1.5)

        skills = "、".join(f"技能{i+1}=专精{m}" for i, m in enumerate(mastery)) or "看不清"
        return (
            f"【{name} 练度】\n"
            f"- 等级：{level}\n"
            f"- 精英化：{elite}\n"
            f"- 潜能：{potential}\n"
            f"- 信赖值：{trust}\n"
            f"- 技能专精：{skills}\n\n"
            f"【模组】\n{module or '未找到模组界面（该干员可能无模组）'}"
        )

    def analyze_screen(self, question: str) -> str:
        if self.llm is None:
            return "视觉功能未启用。"
        from maagent.agent.llm import image_data_url

        if self.focus_before_capture:
            self.focus_emulator()
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
