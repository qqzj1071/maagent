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
from maagent.agent.budget import remaining

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

# User-confirmed aliases; category terms must never select one arbitrary item.
ITEM_ALIASES = {
    "源石": "至纯源石", "玉": "合成玉", "钱": "龙门币",
    "绿票": "资质凭证", "红票": "采购凭证", "蓝票": "通用凭证",
    "黄票": "高级凭证", "紫票": "情报凭证",
    "剿灭代理卡": "常态事务代理卡", "代理卡": "常态事务代理卡",
    "剿灭卡": "常态事务代理卡", "公招券": "招聘许可", "公招加急": "加急许可",
    "十连券": "十连寻访凭证", "寻访券": "寻访凭证",
}
ITEM_GROUPS = {
    "理智药": ["应急理智浓缩液", "应急理智加强剂"],
    "体力药": ["应急理智浓缩液", "应急理智加强剂"],
}
AMBIGUOUS_ITEM_TERMS = {"理智剂", "理智液", "理智", "剿灭", "基建材料", "作战记录", "技巧概要", "芯片"}



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
        remaining()
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
        from maagent.agent.framework import AdbGameController, resolve_mumu_serial

        if not self.address:
            self.address = resolve_mumu_serial(self.manager, self.adb, self.vmindex)
        if not self.address:
            raise RuntimeError("未发现模拟器设备，请确认模拟器已启动")
        game = AdbGameController(self.adb, self.address, screenshot_dir=str(self.screenshot_dir))
        if not game.connect():
            raise RuntimeError(f"连接模拟器失败: {self.address}")
        self._game = game
        return game

    def _adb(self, *args: str, timeout: float = 20.0) -> str:
        if not self.address:
            self._ensure_game()
        return subprocess.run(
            [str(self.adb), "-s", str(self.address), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=remaining(timeout),
        ).stdout

    def _prune_screenshots(self) -> None:
        try:
            files = sorted(self.screenshot_dir.glob("screen_*.png"))
            for old in files[: max(0, len(files) - self.max_screenshots)]:
                old.unlink(missing_ok=True)
        except Exception as e:
            logger.debug("清理旧截图失败: {}", e)

    def screenshot(self, save=True):
        remaining()
        image = self._ensure_game().screenshot(save=save)
        if save:
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
        remaining()
        self._ensure_game().click(int(x), int(y))

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration: int = 300) -> None:
        remaining()
        self._ensure_game().swipe(int(x1), int(y1), int(x2), int(y2), int(duration))

    def press_key(self, key: int) -> None:
        remaining()
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
            if self._is_main_ui(texts):
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

        self._go_home()
        return self._open_from_icon(package, label, enter)

    def _open_from_icon(self, package: str, label: str, enter: bool) -> str:
        from maagent.control.popup import recognize
        from PIL import Image, ImageDraw

        shot = self.screenshot()
        candidates = [item for item in recognize(shot) if "明日方舟" in item.text]
        if not candidates:
            return "模拟器桌面未识别到明日方舟图标，已停止；请确认图标所在桌面页。"
        # OCR finds labels only. Inspect the icon above each label to distinguish B服.
        w, h = shot.size
        tiles, centers = [], []
        for item in candidates:
            x, y = item.center
            top = min(p[1] for p in item.box)
            radius = max(40, round(w * 0.043))
            height = max(90, round(h * 0.145))
            box = (max(0, x-radius), max(0, top-height), min(w, x+radius), min(h, y+20))
            tiles.append(shot.crop(box).resize((200, 210)))
            centers.append((x, max(1, top-round(h * 0.063))))
        sheet = Image.new('RGB', (220*len(tiles), 250), 'white')
        draw = ImageDraw.Draw(sheet)
        for index, tile in enumerate(tiles):
            sheet.paste(tile, (index*220, 30))
            draw.text((index*220+10, 8), str(index+1), fill='black')
        prompt = (
            f"这些是模拟器桌面上明日方舟的图标候选，上方数字是编号。目标是{label}。"
            "B服图标有哔哩哔哩/bilibili的小电视渠道角标，通常为粉色；"
            "不能把橙色可更新角标当作B服标识。官服是没有B站渠道角标的图标。"
            '只返回JSON：{"index":整数编号或0,"reason":"可见标识依据"}。看不清则index=0，不猜测。'
        )
        answer = self._vision_ask(sheet, prompt)
        match = re.search(r'\{.*\}', answer, re.S)
        try:
            choice = json.loads(match.group(0)) if match else {}
            index = choice.get('index', 0)
            if type(index) is not int or not 1 <= index <= len(centers):
                return f"无法可靠区分{label}图标，已停止，未点击。"
        except (ValueError, TypeError):
            return "图标识别结果无效，已停止，未点击。"
        logger.info("图标识别：{}，候选{}，依据{}", label, index, choice.get('reason'))
        self.click(*centers[index-1])
        # Package identity is used only for verification, never for launching.
        if not self._wait_foreground(25, package):
            self._go_home()
            return f"点击图标后未确认进入{label}，已返回桌面；不尝试其他同名图标。"
        return self._finish_open(label, enter)

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
        self.knowledge.load()
        doc = f"干员：{name}"
        for chunk in getattr(self.knowledge, "chunks", []):
            if chunk.get("doc") == doc:
                m = re.search(r"职业：([^\s；]+)", chunk.get("text", ""))
                if m:
                    profession = m.group(1).strip()
                    return profession if profession in PROFESSION_Y else None
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
        profession = self.lookup_profession(name)
        if not profession:
            return f"知识库缺少「{name}」的有效职业文档，请先用 update_prts_knowledge 更新该干员，再读取练度。"
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

        if not self.filter_by_profession(profession):
            return f"未能确认职业「{profession}」筛选成功，已停止查找。"
        logger.info("按文档职业「{}」筛选干员列表", profession)

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

    def _read_potential(self, detail_items=None) -> str:
        # 满潜时潜能右侧显示 MAX，无需点进去
        if any(
            "MAX" in i.text.strip().upper() and i.center[0] > 1750 and 380 < i.center[1] < 480
            for i in (detail_items if detail_items is not None else self.ocr())
        ):
            return "满潜"
        self.click(1816, 400)
        try:
            time.sleep(1.5)
            image = self.screenshot(save=False)
            from maagent.control.popup import recognize, recognize_line
            # 当前控制器使用 1920×1080 坐标；不对未知布局猜测。
            if image.size != (1920, 1080):
                return "?"
            labels = recognize(image.crop((1380, 695, 1520, 760)))
            if not any("潜能" in i.text for i in labels):
                return "?"
            number, confidence = recognize_line(image.crop((1400, 565, 1495, 695)))
            return number if re.fullmatch(r"[1-6]", number) and confidence >= 0.95 else "?"
        finally:
            self.click(1398, 975)  # 取消，绝不消耗信物
            time.sleep(0.5)

    def get_operator_training(self, name: str) -> str:
        result = self.open_operator(name)
        if "详情页" not in result:
            return result

        image = self.screenshot()
        from maagent.control.popup import recognize
        items = recognize(image)
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
        potential = self._read_potential(items)

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

        potential_text = "未能确认" if potential == "?" else potential

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

    # ---------- 仓库物品目录（图标 ↔ 名称） ----------
    def _load_catalog(self) -> dict[str, Any]:
        try:
            return json.loads(self.catalog_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    @classmethod
    def _resolve_item(cls, catalog: dict[str, Any], name: str) -> str | None:
        if name in catalog:
            return name
        alias = ITEM_ALIASES.get(name)
        if alias:  # 别名优先，即使目录里暂时没有该物品
            return alias
        return None  # 仅正式名称/明确别名，禁止凭相似字猜另一物品

    def check_item_icon(self, name: str) -> dict:
        name = name.strip()
        if name in ITEM_GROUPS:
            return {'known':False, 'name':name, 'candidates':ITEM_GROUPS[name],
                    'clarification':f'「{name}」是应急理智浓缩液和应急理智加强剂的统称。博士要查哪一种，还是两种都查？'}
        if name in AMBIGUOUS_ITEM_TERMS:
            return {'known':False, 'name':name, 'clarification':f'「{name}」指代不够明确，博士具体指哪种物品？'}
        catalog = self._load_catalog()
        key = self._resolve_item(catalog, name)
        entry = catalog.get(key, {})
        refs = list(entry.get('references', [])) if isinstance(entry, dict) else []
        if isinstance(entry, dict) and entry.get('icon'):
            refs.insert(0, {'icon': entry['icon'], 'style': 'legacy' if entry.get('hash') else 'maa'})
        valid = []
        seen = set()
        for ref in refs:
            path = (self.catalog_file.parent / ref['icon']).resolve()
            if path.is_relative_to(self.catalog_file.parent.resolve()) and path.is_file() and str(path) not in seen:
                valid.append(dict(ref, path=str(path)))
                seen.add(str(path))
        return {'known': bool(valid), 'name': key or name, 'references': valid,
                'category': entry.get('category') if isinstance(entry, dict) else None}

    @staticmethod
    def _warehouse_motion(before, after):
        """Measure item-feature translation, excluding fixed header/category controls."""
        import cv2
        import numpy as np
        def crop(image):
            gray = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2GRAY)
            h, w = gray.shape
            return gray[int(h*.17):int(h*.87), int(w*.04):int(w*.96)]
        left, right = crop(before), crop(after)
        orb = cv2.ORB_create(nfeatures=1600)
        ka, da = orb.detectAndCompute(left, None)
        kb, db = orb.detectAndCompute(right, None)
        if da is None or db is None:
            return None
        pairs = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(da, db, k=2)
        good = [m for pair in pairs if len(pair)==2 for m,n in [pair] if m.distance < .7*n.distance]
        if len(good) < 12:
            return None
        delta = np.array([np.subtract(kb[m.trainIdx].pt, ka[m.queryIdx].pt) for m in good])
        center = np.median(delta, axis=0)
        keep = np.linalg.norm(delta-center, axis=1) < 3
        points = np.array([ka[m.queryIdx].pt for m,k in zip(good,keep) if k])
        if keep.sum() < 12 or keep.mean() < .6 or np.ptp(points[:,0]) < left.shape[1]*.25 or np.ptp(points[:,1]) < left.shape[0]*.3:
            return None
        return tuple(np.median(delta[keep], axis=0))

    def _match_item_icon(self, shot, info):
        import cv2
        import numpy as np
        # MAA templates use a fixed icon canvas; match at 960px for bounded cost.
        ratio = min(1.0, 960 / shot.width)
        screen = cv2.cvtColor(np.asarray(shot), cv2.COLOR_RGB2BGR)
        screen = cv2.resize(screen, None, fx=ratio, fy=ratio)
        arr = cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY)
        best = (0.0, None)
        for ref in info['references']:
            raw = cv2.imread(ref['path'], cv2.IMREAD_UNCHANGED)
            if raw is None:
                continue
            gray = cv2.cvtColor(raw[:, :, :3], cv2.COLOR_BGR2GRAY) if raw.ndim == 3 else raw
            mask = raw[:, :, 3] if raw.ndim == 3 and raw.shape[2] == 4 else (gray > 12).astype('uint8')*255
            # Quantity text obscures this corner in warehouse screenshots.
            mask[int(mask.shape[0]*.68):, int(mask.shape[1]*.60):] = 0
            base = ratio if ref.get('style') == 'legacy' else shot.width / 1280 * ratio
            for factor in (.9, 1.0, 1.1, 1.16, 1.18, 1.2, 1.22, 1.24, 1.3):
                scale = base * factor
                remaining()
                template = cv2.resize(gray, None, fx=scale, fy=scale)
                if template.shape[0] > arr.shape[0] or template.shape[1] > arr.shape[1]:
                    continue
                resized_mask = cv2.resize(mask, (template.shape[1], template.shape[0]))
                result = cv2.matchTemplate(arr, template, cv2.TM_CCORR_NORMED, mask=resized_mask)
                result[~np.isfinite(result)] = 0
                _, score, _, loc = cv2.minMaxLoc(result)
                if score >= .96 and raw.ndim == 3:
                    color = cv2.resize(raw[:, :, :3], (template.shape[1], template.shape[0]))
                    crop = screen[loc[1]:loc[1]+template.shape[0], loc[0]:loc[0]+template.shape[1]]
                    color_score = float(cv2.matchTemplate(crop, color, cv2.TM_CCOEFF_NORMED, mask=resized_mask)[0,0])
                    if not np.isfinite(color_score) or color_score < .80:
                        continue
                if score > best[0]:
                    best = (score, (round((loc[0]+template.shape[1]/2)/ratio), round((loc[1]+template.shape[0]/2)/ratio)))
        return best

    def _scan_warehouse_icon(self, info, visit=None, with_image=False):
        # Fixed slow horizontal drag; verify actual overlap instead of assuming it.
        started = time.monotonic()
        shot = self.screenshot()
        stationary = 0
        offset = 0.0
        visited = []
        direction = 1  # Establish left boundary even if a previous query left us midway.
        for step in range(48):
            remaining()
            if time.monotonic() - started > 480:
                return None, '图标查找达到8分钟上限，尚未完成遍历'
            best = self._match_item_icon(shot, info)
            if best[0] >= .96:
                if visit is None:
                    return best[1], ''
                from PIL import ImageDraw
                masked = shot.copy()
                for _ in range(24):
                    x, y = best[1]
                    world = (x-offset, y)
                    tolerance = shot.width * .008
                    if not any(abs(world[0]-px)<tolerance and abs(world[1]-py)<tolerance for px,py in visited):
                        if with_image:
                            visit(best[1], shot)
                        else:
                            visit(best[1])
                        visited.append(world)
                    radius = round(shot.width * .065)
                    ImageDraw.Draw(masked).rectangle((x-radius,y-radius,x+radius,y+radius), fill='black')
                    best = self._match_item_icon(masked, info)
                    if best[0] < .96:
                        break
                else:
                    return None, '同屏候选过多，无法确认查找完整性，已停止'
            w, h = shot.size
            x1, x2 = ((.375,.625) if direction == 1 else (.625,.375))
            self.swipe(int(w*x1), int(h*.53), int(w*x2), int(h*.53), 1500)
            time.sleep(1.8)
            after = self.screenshot(save=False)
            time.sleep(.25)
            settled = self.screenshot(save=False)
            stable = self._warehouse_motion(after, settled)
            if stable is None or abs(stable[0]) > 2 or abs(stable[1]) > 2:
                return None, '无法确认拖动后画面已稳定，已停止，尚未完成遍历'
            motion = self._warehouse_motion(shot, settled)
            if motion is None:
                return None, '无法确认相邻画面图标重叠，已停止以免漏查'
            dx, dy = motion
            if abs(dy) > 3 or abs(dx) > w*.45 or dx*direction < -3:
                return None, '实际拖动距离或方向异常，无法保证没有漏查，已停止'
            offset += dx
            stationary = stationary+1 if abs(dx) <= 2 else 0
            shot = settled
            if stationary == 2:
                if direction == -1:
                    return None, 'complete_no_match'
                direction, stationary = -1, 0
        return None, '达到48次拖动上限，尚未完成遍历'

    def get_item_quantity(self, name: str) -> str:
        import cv2
        import numpy as np
        info = self.check_item_icon(name)
        if info.get('clarification'):
            return info['clarification']
        if not info['known']:
            return f"我还不认识「{name}」的图标，请博士指导或让我先寻找并学习图标。未操作仓库。"
        name = info['name']
        if not self._is_warehouse(self._texts()) and not self._open_warehouse():
            return "未能打开仓库，已停止。"
        if not self.find_and_click('全部', wait=0.8):
            return '无法确认仓库全部物品范围，已停止。'
        position, reason = self._scan_warehouse_icon(info)
        if position is None and reason == 'complete_no_match':
            return f"「{name}」当前数量：0（已在全部范围完整查找至末尾，未发现匹配图标）。"
        if position is None:
            return f"我认识「{name}」的图标；{reason}。这不代表库存为0。"
        try:
            result = self._read_target_at(position, name)
        except ValueError as exc:
            return str(exc)
        return self._format_item_detail(name, result)

    def _read_target_at(self, position, name, image=None):
        from maagent.control.popup import recognize
        shot = image if image is not None else self.screenshot()
        x, y = position
        scale = shot.width / 1920
        box = (max(0,round(x)), max(0,round(y+30*scale)),
               min(shot.width,round(x+115*scale)), min(shot.height,round(y+100*scale)))
        grid = [i.text.strip() for i in recognize(shot.crop(box))]
        numbers = [t for t in grid if re.fullmatch(r'[0-9]+',t)]
        initial = numbers[0] if len(numbers)==1 else None
        self.click(*position)
        time.sleep(1.2)
        try:
            panel_name, qty, expiry = self._read_item_panel(self.ocr())
        finally:
            self.press_key(4)
            time.sleep(.3)
        expected_names = name if isinstance(name, (list, tuple)) else [name]
        if panel_name not in expected_names:
            raise ValueError(f"图标候选详情名称为「{panel_name or '未读清'}」，与「{name}」不一致，已停止，未返回数量。")
        if not qty or not re.fullmatch(r'[0-9]+',qty):
            raise ValueError(f"已确认「{name}」，但详情库存数字未读清，已停止。")
        if initial is not None and int(initial) != int(qty):
            raise ValueError(f"「{name}」图标数量{initial}与详情库存{qty}不一致，已停止，数量待确认。")
        return {'name':panel_name, 'quantity':int(qty), 'expiry':expiry, 'grid_quantity':initial,
                'verified':initial is not None}

    @staticmethod
    def _format_item_detail(name, result):
        verification = '图标数量与详情库存一致' if result['verified'] else '图标数量未可靠读清，数量仅由详情确认'
        return f"「{name}」当前数量：{result['quantity']}（{verification}）" + (f"；{result['expiry']} 到期" if result['expiry'] else '')

    def get_item_latest_expiry(self, name):
        return self._get_item_expiry(name)

    def get_item_earliest_expiry(self, name):
        return self._get_item_expiry(name, earliest=True)

    def _get_item_expiry(self, name, earliest=False):
        names = ITEM_GROUPS.get(name, [name]) if earliest else [name]
        infos = [self.check_item_icon(n) for n in names]
        for info in infos:
            if info.get('clarification'):
                return info['clarification']
            if not info['known']:
                return f"我还不认识「{info['name']}」的图标，请博士授权学习后再查。"
        canonical = [info['name'] for info in infos]
        info = {'references':[ref for item in infos for ref in item['references']]}
        target = canonical if len(canonical)>1 else canonical[0]
        if not self._is_warehouse(self._texts()) and not self._open_warehouse():
            return '未能打开仓库，已停止。'
        if not self.find_and_click('全部',wait=.8):
            return '无法确认全部物品范围，已停止。'
        batches = {}
        def visit(position, image=None):
            result = self._read_target_at(position,target,image=image)
            if not result['expiry']:
                raise ValueError('发现目标物品但到期时间未读清，无法确认目标批次。')
            batches[(result.get('name',canonical[0]),result['expiry'],result['quantity'])] = result
        try:
            _, reason = self._scan_warehouse_icon(info,visit=visit,with_image=True)
        except ValueError as exc:
            return str(exc)
        if reason != 'complete_no_match':
            return f'未完成各批次核对：{reason}；不能确认目标到期批次。'
        if not batches:
            return f'「{name}」当前数量：0（全部范围完整查找无匹配）。'
        from datetime import datetime
        now = datetime.now()
        def expiry_key(result):
            parsed = datetime.strptime(result['expiry'],'%m/%d %H:%M')
            year = now.year + (1 if now.month-parsed.month > 6 else 0)
            return parsed.replace(year=year)
        chosen = (min if earliest else max)(batches.values(),key=expiry_key)
        selected = [r for r in batches.values() if expiry_key(r)==expiry_key(chosen)]
        direction = '最早' if earliest else '最晚'
        details = '；'.join(self._format_item_detail(r.get('name',canonical[0]),r) for r in selected)
        return f'已完整核对{len(batches)}种批次记录；到期{direction}的批次：' + details

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
