from __future__ import annotations

import time

import win32gui
from loguru import logger
from PIL import Image, ImageGrab

from maagent.control.popup import (
    _click_screen,
    _force_foreground,
    crop_region,
    ensure_visible,
    main_window,
    recognize,
)

PROCESS_NAME = "BetterGI.exe"
# 原神（国服 / 国际服）进程；任务完成后由 BetterGI 自动关闭。
GAME_PROCESSES = ("YuanShen.exe", "GenshinImpact.exe")

# 主界面「启动」按钮（首页右侧的大按钮，精确匹配以免误点左侧边栏的同名导航项）
START_LABELS = ("启动",)
# 左侧边栏「一条龙」栏目（OCR 定位）
ONEDRAGON_LABELS = ("一条龙",)
# 一条龙任务列表右侧的「开始」三角按钮。图标没有文字，先尝试 OCR 这些标签，
# 找不到则直接点区域中心（区域可用 config 的 adapters.bgi.regions 覆盖）。
TASK_START_LABELS = ("一键执行", "开始", "执行")

DEFAULT_REGIONS = {
    # 主界面「启动」按钮搜索区域（首页右侧；排除左侧边栏的「启动」导航项）
    "start": (0.45, 0.0, 1.0, 1.0),
    # 左侧边栏「一条龙」栏目搜索区域
    "one_dragon": (0.0, 0.0, 0.40, 1.0),
    # 任务列表右侧「开始」三角按钮区域
    "task_start": (0.34, 0.09, 0.43, 0.17),
}


def _find_item(items, labels: tuple[str, ...], exact: bool = False):
    for name in labels:
        for item in items:
            text = item.text.strip()
            matched = (text == name) if exact else (name in text)
            if matched:
                return item
    return None


def capture_window_screen(hwnd: int) -> Image.Image | None:
    """Screen-grab the window region (PrintWindow 对 WPF 硬件加速窗口返回黑屏)."""
    try:
        l, t, r, b = win32gui.GetWindowRect(hwnd)
        if r - l <= 0 or b - t <= 0:
            return None
        return ImageGrab.grab(bbox=(l, t, r, b), all_screens=True)
    except Exception as e:
        logger.warning("BetterGI：屏幕截图失败: {}", e)
        return None


def region_for(cfg: dict, key: str) -> tuple[float, float, float, float]:
    regions = cfg.get("regions") or {}
    value = regions.get(key)
    if value is None:
        value = DEFAULT_REGIONS.get(key)
    if not value:
        value = DEFAULT_REGIONS.get(key, (0.0, 0.0, 1.0, 1.0))
    return tuple(float(x) for x in value[:4])


class BetterGiUI:
    """Window / OCR / click helpers for BetterGI (更好的原神).

    BetterGI 是 WPF 应用：PrintWindow 会返回黑屏，所以截图一律用屏幕抓取，
    且抓取前必须把窗口置前（与 MaaEnd 相同的处理方式）。
    """

    def __init__(self, process_name: str = PROCESS_NAME, cfg: dict | None = None) -> None:
        self.process_name = process_name
        self.cfg = cfg or {}
        debug_dir = self.cfg.get("debug_dir") or "logs/popups"
        from pathlib import Path

        self.debug_dir = Path(debug_dir)

    def _save_debug(self, img, tag: str) -> None:
        """OCR 失败时保存截图，便于排查窗口内容/置前问题。"""
        try:
            self.debug_dir.mkdir(parents=True, exist_ok=True)
            img.save(self.debug_dir / f"bgi_{tag}_{int(time.time())}.png")
        except Exception:
            pass

    def hwnd(self) -> int | None:
        return main_window(self.process_name)

    def is_running(self) -> bool:
        return self.hwnd() is not None

    def foreground(self, hwnd: int) -> None:
        """把 BetterGI 窗口可靠地置前。

        点击「启动」后原神会抢占前台，简单的 SetForegroundWindow 会被前台锁
        拒绝，导致后续点「一条龙」/「开始」落空。这里用 AttachThreadInput 强制
        置前并重试，直到确认 BetterGI 成为前台窗口。
        """
        ensure_visible(hwnd)
        for _ in range(6):
            if win32gui.GetForegroundWindow() == hwnd:
                return
            try:
                _force_foreground(hwnd)
            except Exception as e:
                logger.debug("BetterGI：置前失败 {}", e)
            time.sleep(0.4)
        if win32gui.GetForegroundWindow() != hwnd:
            logger.warning("BetterGI：多次尝试后仍未置前，本次点击可能落空")

    def capture(self, hwnd: int) -> Image.Image | None:
        self.foreground(hwnd)
        # 置前后稍作停留，确保窗口完成绘制，避免抓到未渲染完成的画面
        time.sleep(0.5)
        return capture_window_screen(hwnd)

    def click_label(self, labels: tuple[str, ...], region_key: str, exact: bool = False) -> bool:
        """OCR 在指定区域内寻找 label 并点击其中心。"""
        hwnd = self.hwnd()
        if hwnd is None:
            return False
        try:
            img = self.capture(hwnd)
            if img is None:
                return False
            region = region_for(self.cfg, region_key)
            l, t, r, b = crop_region(img, region)
            # 关键：OCR 整张窗口再按区域筛选。rapidocr 对裁剪后的子图会漏检大号按钮
            # 文字（如首页的「启动」按钮），整图识别则稳定得多。
            items = recognize(img)
            items = [it for it in items if l <= it.center[0] <= r and t <= it.center[1] <= b]
            item = _find_item(items, labels, exact=exact)
            if item is None:
                logger.debug("BetterGI：区域内未识别到标签 {} ({})", labels, region_key)
                self._save_debug(img, region_key)
                return False
            # 点击前再次置前，避免截图后到点击之间原神抢回前台导致点空
            self.foreground(hwnd)
            wx, wy, _, _ = win32gui.GetWindowRect(hwnd)
            _click_screen(wx + item.center[0], wy + item.center[1])
            logger.info("BetterGI：已点击「{}」", item.text)
            return True
        except Exception as e:
            logger.warning("BetterGI：点击标签失败: {}", e)
            return False

    def click_task_start(self) -> bool:
        """点击任务列表右侧的「开始」三角按钮（图标无文字，兜底点区域中心）。"""
        hwnd = self.hwnd()
        if hwnd is None:
            return False
        try:
            img = self.capture(hwnd)
            if img is None:
                return False
            region = region_for(self.cfg, "task_start")
            l, t, r, b = crop_region(img, region)
            items = recognize(img)
            items = [it for it in items if l <= it.center[0] <= r and t <= it.center[1] <= b]
            item = _find_item(items, TASK_START_LABELS)
            # 点击前再次置前，确保点落在 BetterGI 上
            self.foreground(hwnd)
            wx, wy, _, _ = win32gui.GetWindowRect(hwnd)
            if item is not None:
                _click_screen(wx + item.center[0], wy + item.center[1])
                logger.info("BetterGI：已点击任务开始按钮「{}」", item.text)
            else:
                cx = wx + l + (r - l) // 2
                cy = wy + t + (b - t) // 2
                _click_screen(cx, cy)
                logger.info("BetterGI：已点击任务开始三角按钮（区域中心 {},{})", cx, cy)
            return True
        except Exception as e:
            logger.warning("BetterGI：点击任务开始按钮失败: {}", e)
            return False

    def click_start_game(self) -> bool:
        return self.click_label(START_LABELS, "start", exact=True)

    def click_one_dragon(self) -> bool:
        return self.click_label(ONEDRAGON_LABELS, "one_dragon")
