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

# 仓库物品网格：列/行中心坐标（实测），数量在格子右下角
WAREHOUSE_COLS = (185, 430, 675, 920, 1160, 1400, 1645, 1890)
WAREHOUSE_ROWS = (260, 543, 827)

# 博士常用的口语/简称 → 仓库中的正式名称（模糊识别兜底）
ITEM_ALIASES = {
    "理智药": "应急理智加强剂",
    "理智剂": "应急理智加强剂",
    "理智液": "应急理智加强剂",
    "体力药": "应急理智加强剂",
    "理智": "应急理智加强剂",
    "剿灭卡": "常态事务代理卡",
    "代理卡": "常态事务代理卡",
    "剿灭": "常态事务代理卡",
    "钱": "龙门币",
    "金币": "龙门币",
    "龙门币": "龙门币",
    "源石": "至纯源石",
    "源石玉": "合成玉",
    "合成石": "合成玉",
    "寻访券": "寻访凭证",
    "十连券": "十连寻访凭证",
    "招聘": "招聘许可",
    "加急": "加急许可",
    "黄票": "高级凭证",
    "绿票": "资质凭证",
    "红票": "采购凭证",
    "基建材料": "碳素",
    "作战记录": "中级作战记录",
    "技巧概要": "技巧概要·卷3",
    "芯片": "先锋芯片组",
}


def _is_start_button(text: str) -> bool:
    t = text.strip()
    return t in START_TEXTS or "开始唤醒" in t


class GameService:
    def __init__(
        self,
        config: dict[str, Any],
        llm: Any = None,
        knowledge: Any = None,
        catalog_file: str | None = None,
    ) -> None:
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
        self.catalog_file = Path(catalog_file or "config/agent_item_catalog.json")
        self.address: str | None = None
        self._game = None
        self._wh_first = False  # 仓库是否已确认在第一页（缓存，省去重复回退）

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
        return PACKAGE_BILIBILI, "B服"  # 未明确说明时默认 B 服

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
        self.click(PROFESSION_FILTER_X, y)
        time.sleep(2.5)  # 筛选有加载动画，等久一点再判断
        return self._is_operator_list(self._texts())

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
    def _count_dots(badge, thr: int = 190, min_size: int = 40) -> int:
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

    def _elite_level(self, image) -> int:
        """精英化等级：数图标里实心白条的道数（精0/1/2），用白色像素面积区分。"""
        import numpy as np

        area = int((np.array(image.crop((1370, 375, 1470, 465)).convert("L")) > 200).sum())
        if area >= 1900:
            return 2
        if area >= 900:
            return 1
        return 0

    def _read_modules(self) -> list[dict[str, Any]]:
        """读取模组详情界面里的全部模组：类型（名字末尾字母）+ 状态（MAX/解锁）。"""
        items = self.ocr()
        badges: list[tuple[Any, str]] = []
        seen_y: list[int] = []
        for it in items:
            tu = it.text.strip().upper().replace(" ", "")
            if tu == "ORIGINAL":
                badges.append((it, "ORIGINAL"))
            elif re.fullmatch(r"[A-Z]{3,6}[XYΔΑ△Α]", tu):
                if any(abs(it.center[1] - y) < 60 for y in seen_y):
                    continue
                seen_y.append(it.center[1])
                badges.append((it, tu[-1]))
        modules: list[dict[str, Any]] = []
        for badge, mtype in badges:
            status = None
            for it in items:
                if abs(it.center[0] - badge.center[0]) < 100 and 30 < it.center[1] - badge.center[1] < 120:
                    text = it.text.strip()
                    if text in ("MAX", "解锁"):
                        status = text
                        break
            modules.append({
                "type": mtype, "status": status,
                "x": badge.center[0], "y": badge.center[1],
            })
        return modules

    def _read_module_level(self, badge_x: int, badge_y: int) -> int | None:
        """点击徽章下方的升级箭头，从弹窗「确认将…升至N级?」推算当前等级 N-1，然后取消。"""
        self.click(badge_x, badge_y + 68)
        time.sleep(2.5)
        joined = " ".join(self._texts())
        m = re.search(r"升至\s*(\d+)\s*级", joined)
        self.click(1385, 945)  # 取消，不消耗材料
        time.sleep(1.5)
        return int(m.group(1)) - 1 if m else None

    def _read_potential(self) -> str:
        # 满潜时潜能右侧显示 MAX，无需点进去
        if any(
            "MAX" in i.text.strip().upper() and i.center[0] > 1750 and 380 < i.center[1] < 480
            for i in self.ocr()
        ):
            return "满潜"
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
        level_cands = [
            i for i in items
            if re.fullmatch(r"\d{1,3}", i.text.strip()) and 1250 < i.center[0] < 1620 and 170 < i.center[1] < 300
        ]
        level = (
            min(level_cands, key=lambda i: abs(i.center[0] - 1396) + abs(i.center[1] - 214)).text.strip()
            if level_cands else "?"
        )
        trust = next(
            (i.text.strip() for i in items
             if "%" in i.text and 400 < i.center[0] < 720 and 580 < i.center[1] < 660),
            "?",
        )
        elite = f"精{self._elite_level(image)}"
        mastery = self._skill_mastery(image)
        potential = self._read_potential()

        modules: list[dict[str, Any]] = []
        if self._open_module_view():
            hit = next((i for i in self.ocr() if "模组详情" in i.text), None)
            if hit is not None:
                self.click(*hit.center)
                time.sleep(2.5)
                modules = self._read_modules()
                for m in modules:  # 未满级的模组：点进去读具体等级
                    if m["status"] is None and m["type"] != "ORIGINAL":
                        module_level = self._read_module_level(m["x"], m["y"])
                        if module_level is not None:
                            m["level"] = module_level
                self.click(75, 55)
                time.sleep(1.2)
            self.click(75, 55)
            time.sleep(1.2)

        def _module_line(m: dict[str, Any]) -> str:
            if m["type"] == "ORIGINAL":
                return "- ORIGINAL：默认模组（无等级）"
            if m["status"] == "MAX":
                return f"- {m['type']}：已解锁，3级（满级）"
            if m["status"] == "解锁":
                return f"- {m['type']}：未解锁"
            if m.get("level") is not None:
                return f"- {m['type']}：已解锁，{m['level']}级"
            return f"- {m['type']}：已解锁，等级未满（未能读取）"

        module_text = "\n".join(_module_line(m) for m in modules) if modules else \
            "未找到模组界面（该干员可能无模组）"

        potential_text = potential if potential == "满潜" else f"{potential}（未满，满潜为6）"

        skills = "、".join(f"技能{i+1}=专精{m}" for i, m in enumerate(mastery)) or "看不清"
        return (
            f"【{name} 练度】\n"
            f"- 等级：{level}\n"
            f"- 精英化：{elite}\n"
            f"- 潜能：{potential_text}\n"
            f"- 信赖值：{trust}\n"
            f"- 技能专精：{skills}\n\n"
            f"【模组】\n{module_text}"
        )

    def _is_warehouse(self, texts: list[str]) -> bool:
        return ("全部" in texts or "消耗物品" in texts or "养成材料" in texts) and "保险库" in texts

    def _open_warehouse(self) -> bool:
        self._wh_first = False
        for _ in range(6):
            items = self.ocr()
            texts = [i.text.strip() for i in items]
            if self._is_warehouse(texts):
                return True
            if self._confirm_dialog():
                continue
            if self._is_main_ui(texts):
                cand = next(
                    (i for i in items
                     if i.text.strip() == "仓库" and i.center[0] > 1600 and i.center[1] > 800),
                    None,
                )
                if cand is not None:
                    self.click(*cand.center)
                    time.sleep(3.0)
                    continue
            self.click(75, 55)
            time.sleep(1.8)
        return self._is_warehouse(self._texts())

    @staticmethod
    def _parse_expiry(text: str) -> str | None:
        m = re.search(r"(\d{1,2})/(\d{1,2})\D*(\d{1,2}):(\d{2})", text)
        if m:
            return f"{int(m.group(1)):02d}/{int(m.group(2)):02d} {int(m.group(3)):02d}:{m.group(4)}"
        return None

    def _read_item_panel(self, items=None) -> tuple[str | None, str | None, str | None]:
        if items is None:
            items = self.ocr()
        names = [
            i for i in items
            if 250 < i.center[0] < 1150 and 185 < i.center[1] < 320
            and i.text.strip() and "库存" not in i.text and "到期" not in i.text
        ]
        name = min(names, key=lambda i: i.center[0]).text.strip() if names else None
        qty = next(
            (i.text.strip() for i in items
             if 1430 < i.center[0] < 1670 and 200 < i.center[1] < 300 and i.text.strip()),
            None,
        )
        expiry = next(
            (e for i in items
             if (e := self._parse_expiry(i.text)) or "到期" in i.text),
            None,
        )
        return name, qty, expiry

    def _screen_sig(self, image=None):
        import numpy as np

        img = (image or self.screenshot()).convert("L").resize((64, 36))
        return np.asarray(img, dtype=np.int16)

    def get_warehouse_inventory(self, max_items: int = 600) -> str:
        import numpy as np

        if not self._is_warehouse(self._texts()) and not self._open_warehouse():
            return "未能打开仓库界面。"
        self.click(185, 260)  # 第一个物品
        time.sleep(1.6)
        records: list[tuple[str, str]] = []
        misses = 0
        for _ in range(max_items):
            name, qty = self._read_item_panel()
            if name is None:
                misses += 1
                if misses >= 3:
                    break
            else:
                misses = 0
                records.append((name, qty or "?"))
            before = self._screen_sig()
            self.click(1888, 512)  # 右侧「>」下一个
            changed = False
            for _ in range(10):
                time.sleep(0.5)
                if float(np.abs(self._screen_sig() - before).mean()) >= 0.5:
                    changed = True
                    break
            if not changed:
                break  # 画面不再变化，说明已到末尾
        self.press_key(4)
        time.sleep(1.2)
        if not records:
            return "仓库里没有读到物品。"
        lines = [f"- {n}：{q}" for n, q in records]
        return f"【仓库物品（共 {len(records)} 种）】\n" + "\n".join(lines)

    # ---------- 仓库物品目录（图标 ↔ 名称） ----------
    def _load_catalog(self) -> dict[str, Any]:
        try:
            return json.loads(self.catalog_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save_catalog(self, catalog: dict[str, Any]) -> None:
        self.catalog_file.parent.mkdir(parents=True, exist_ok=True)
        self.catalog_file.write_text(
            json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    @staticmethod
    def _icon_hash(image, cx: int, cy: int) -> str:
        import numpy as np

        crop = image.crop((cx - 58, cy - 58, cx + 58, cy + 58)).convert("L").resize((9, 8))
        arr = np.asarray(crop, dtype=np.int16)
        return "".join("1" if v else "0" for v in (arr[:, 1:] > arr[:, :-1]).flatten())

    @staticmethod
    def _grid_cols(image) -> list[int]:
        """用列亮度信号的相位检测物品列中心（借鉴 MAA DepotImageAnalyzer）。"""
        import numpy as np

        small = image.convert("L").resize((image.width // 2, image.height // 2))
        hist = np.asarray(small, dtype=np.float32).mean(axis=0)
        spacing = (WAREHOUSE_COLS[1] - WAREHOUSE_COLS[0]) / 2  # 半分辨率下的列间距
        x = np.arange(len(hist), dtype=np.float32)
        s = float((hist * np.sin(2 * np.pi * x / spacing)).sum())
        c = float((hist * np.cos(2 * np.pi * x / spacing)).sum())
        phase = float(np.arctan2(s, c))
        first = phase / (2 * np.pi) * spacing + spacing / 2
        if phase < 0:
            first += spacing
        cols: list[int] = []
        xx = first * 2
        while xx < image.width:
            cols.append(int(round(xx)))
            xx += spacing * 2
        return cols

    @staticmethod
    def _cell_has_item(image, cx: int, cy: int) -> bool:
        import numpy as np

        crop = image.crop((cx - 58, cy - 58, cx + 58, cy + 58)).convert("L")
        return float(np.asarray(crop, dtype=np.int16).std()) > 14.0

    @staticmethod
    def _hamming(a: str, b: str) -> int:
        return sum(1 for x, y in zip(a, b) if x != y)

    @staticmethod
    def _cell_quantity(image, cx: int, cy: int) -> str | None:
        import cv2
        import numpy as np
        from PIL import Image as _Image

        from maagent.control.popup import recognize

        box = (
            max(0, cx - 45),
            max(0, cy + 50),
            min(image.width, cx + 95),
            min(image.height, cy + 112),
        )
        arr = np.asarray(image.crop(box).convert("L"))
        up = cv2.resize(arr, (arr.shape[1] * 4, arr.shape[0] * 4), interpolation=cv2.INTER_CUBIC)
        texts = [i.text.strip() for i in recognize(_Image.fromarray(up)) if re.search(r"\d", i.text)]
        if not texts:
            return None
        return "".join(texts)

    def _warehouse_next_page(self, before=None) -> bool:
        import numpy as np

        if before is None:
            before = self._screen_sig()
        self.swipe(1700, 500, 200, 500, 400)
        time.sleep(1.4)
        for _ in range(5):
            if float(np.abs(self._screen_sig() - before).mean()) >= 0.5:
                self._wh_first = False  # 已经翻页，不再是首页
                return True
            time.sleep(0.5)
        return False

    def _warehouse_first_page(self) -> None:
        import numpy as np

        if self._wh_first:  # 上次已确认在首页，直接复用
            return
        stable = 0
        prev = self._screen_sig()
        for _ in range(30):
            self.swipe(200, 500, 1700, 500, 400)
            time.sleep(1.1)
            cur = self._screen_sig()
            if float(np.abs(cur - prev).mean()) < 0.5:
                stable += 1
                if stable >= 2:  # 连续两次都没变化，确认已在首页
                    self._wh_first = True
                    return
            else:
                stable = 0
            prev = cur

    def build_item_catalog(self) -> str:
        import hashlib

        import numpy as np

        if not self._is_warehouse(self._texts()) and not self._open_warehouse():
            return "未能打开仓库界面。"
        self._warehouse_first_page()
        icon_dir = self.catalog_file.parent / "item_icons"
        icon_dir.mkdir(parents=True, exist_ok=True)
        catalog = self._load_catalog()
        known_hashes = [
            e["hash"] for e in catalog.values() if isinstance(e, dict) and e.get("hash")
        ]
        added = skipped = 0
        for _ in range(30):
            image = self.screenshot()
            cols = self._grid_cols(image) or list(WAREHOUSE_COLS)
            for cy in WAREHOUSE_ROWS:
                for cx in cols:
                    shot = self.screenshot()  # 每格现截，避免中途滚动导致裁剪错位
                    h = self._icon_hash(shot, cx, cy)
                    if any(self._hamming(h, kh) <= 4 for kh in known_hashes):
                        skipped += 1
                        continue  # 已认识，跳过
                    crop = shot.crop((cx - 58, cy - 58, cx + 58, cy + 58)).convert("L")
                    for _ in range(3):  # 详情面板没弹出就重试
                        self.click(cx, cy)
                        time.sleep(1.6)
                        if any("库存" in i.text for i in self.ocr()):
                            break
                    else:
                        continue  # 空格子或点不中，跳过
                    name = self._read_item_panel()[0]
                    self.press_key(4)
                    for _ in range(6):  # 等详情面板完全关闭再点下一格
                        time.sleep(0.5)
                        if not any("库存" in i.text for i in self.ocr()):
                            break
                    if name and name not in catalog:
                        fname = hashlib.md5(name.encode("utf-8")).hexdigest()[:12] + ".png"
                        crop.save(icon_dir / fname)
                        center = np.asarray(shot.crop((cx - 30, cy - 30, cx + 30, cy + 30)))
                        mean = center.reshape(-1, 3).mean(axis=0)
                        catalog[name] = {
                            "icon": f"item_icons/{fname}",
                            "hash": h,
                            "color": [round(float(v), 1) for v in mean],
                        }
                        known_hashes.append(h)
                        added += 1
            if not self._warehouse_next_page():
                break
        self._save_catalog(catalog)
        return f"图标目录共 {len(catalog)} 种（本次新增 {added}，跳过已识别 {skipped}）。"

    @staticmethod
    def _name_score(a: str, b: str) -> float:
        if not a or not b:
            return 0.0
        if a == b:
            return 1.0
        if a in b or b in a:
            return 0.9
        la, lb = len(a), len(b)
        dp = [0] * (lb + 1)
        best = 0
        for i in range(1, la + 1):
            prev = 0
            for j in range(1, lb + 1):
                cur = dp[j]
                if a[i - 1] == b[j - 1]:
                    dp[j] = prev + 1
                    if dp[j] > best:
                        best = dp[j]
                else:
                    dp[j] = 0
                prev = cur
        lcs = best / min(la, lb)  # 最长公共子串占比
        sa, sb = set(a), set(b)
        overlap = len(sa & sb) / max(1, len(sa | sb))
        return max(lcs, overlap)

    @classmethod
    def _name_close(cls, a: str, b: str) -> bool:
        return cls._name_score(a, b) >= 0.5

    @classmethod
    def _traverse_match(cls, panel_name: str, query: str) -> bool:
        """遍历时的匹配要严格些，避免误认（如「固源岩」误配「提纯源岩」）。"""
        if panel_name == query or query in panel_name or panel_name in query:
            return True
        alias = ITEM_ALIASES.get(query)
        if alias and (panel_name == alias or alias in panel_name):
            return True
        return cls._name_score(panel_name, query) >= 0.75

    def _traverse_find(self, name: str, max_items: int = 200) -> str:
        import numpy as np

        self._warehouse_first_page()
        for _ in range(3):  # 从第一件开始，确保点开
            self.click(WAREHOUSE_COLS[0], WAREHOUSE_ROWS[0])
            time.sleep(1.6)
            if any("库存" in i.text for i in self.ocr()):
                break
        else:
            return f"在仓库里没找到「{name}」。"
        prev_text = None
        for _ in range(max_items):
            items = self.ocr()
            panel_name, qty, expiry = self._read_item_panel(items)
            if panel_name and self._traverse_match(panel_name, name):
                self.press_key(4)
                time.sleep(0.8)
                info = f"「{panel_name}」当前数量：{qty or '读取失败'}"
                if expiry:
                    info += f"（{expiry} 到期）"
                return info
            text = " ".join(i.text for i in items)
            if text == prev_text:
                break  # 内容不再变化，已到末尾
            prev_text = text
            self.click(1888, 512)  # 右侧「>」下一件
            time.sleep(1.0)
        self.press_key(4)
        time.sleep(0.8)
        return f"在仓库里没找到「{name}」。"

    @classmethod
    def _resolve_item(cls, catalog: dict[str, Any], name: str) -> str | None:
        if name in catalog:
            return name
        alias = ITEM_ALIASES.get(name)
        if alias:  # 别名优先，即使目录里暂时没有该物品
            return alias
        best: str | None = None
        best_score = 0.0
        for k in catalog:
            score = cls._name_score(name, k)
            if score > best_score:
                best, best_score = k, score
        return best if best_score >= 0.5 else None

    def get_item_quantity(self, name: str) -> str:
        import cv2
        import numpy as np

        catalog = self._load_catalog()
        query = name  # 保留博士的原话，兜底时用它去找
        key = self._resolve_item(catalog, name)
        if key is None or key not in catalog:
            if not self._is_warehouse(self._texts()) and not self._open_warehouse():
                return "未能打开仓库界面。"
            return self._traverse_find(query)  # 目录里没有，退化为遍历查找
        name = key
        entry = catalog.get(name)
        icon_rel = entry.get("icon") if isinstance(entry, dict) else None
        if not icon_rel:
            return f"我还没记住「{name}」的图标，请让我先清点一次仓库。"
        tmpl = cv2.imread(str(self.catalog_file.parent / icon_rel), cv2.IMREAD_GRAYSCALE)
        if tmpl is None:
            return f"「{name}」的图标文件缺失，请让我重新清点一次仓库。"
        if not self._is_warehouse(self._texts()) and not self._open_warehouse():
            return "未能打开仓库界面。"
        self._warehouse_first_page()
        target_color = entry.get("color") if isinstance(entry, dict) else None
        for _ in range(8):
            shot = self.screenshot()
            arr = cv2.cvtColor(np.asarray(shot), cv2.COLOR_RGB2GRAY)
            res = cv2.matchTemplate(arr, tmpl, cv2.TM_CCOEFF_NORMED)
            _, maxv, _, maxloc = cv2.minMaxLoc(res)
            if maxv >= 0.9 and (
                target_color is None or self._color_ok(shot, maxloc, tmpl, target_color)
            ):
                cx = maxloc[0] + tmpl.shape[1] // 2
                cy = maxloc[1] + tmpl.shape[0] // 2
                for _ in range(3):  # 定位后点开详情，读清晰的大号「库存」数字
                    self.click(cx, cy)
                    time.sleep(1.4)
                    items = self.ocr()
                    if any("库存" in i.text for i in items):
                        _, qty, expiry = self._read_item_panel(items)
                        self.press_key(4)
                        time.sleep(0.8)
                        info = f"「{name}」当前数量：{qty or '读取失败'}"
                        if expiry:
                            info += f"（{expiry} 到期）"
                        return info
                if not self._warehouse_next_page(self._screen_sig(shot)):
                    break
                continue
            if not self._warehouse_next_page(self._screen_sig(shot)):
                break
        return self._traverse_find(query)  # 图标匹配不到，退化为遍历查找

    @staticmethod
    def _color_ok(image, loc, tmpl, target_color) -> bool:
        import numpy as np

        x0 = loc[0] + tmpl.shape[1] // 2 - 30
        y0 = loc[1] + tmpl.shape[0] // 2 - 30
        crop = np.asarray(image.crop((x0, y0, x0 + 60, y0 + 60)))
        mean = crop.reshape(-1, 3).mean(axis=0)
        diff = float(np.sum((mean - np.asarray(target_color, dtype=float)) ** 2))
        return diff < 2000.0

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
