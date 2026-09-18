from __future__ import annotations

import ctypes
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import psutil
import win32api
import win32con
import win32gui
import win32process
import win32ui
from loguru import logger
from PIL import Image

try:
    ctypes.windll.user32.SetProcessDPIAware()
except Exception:
    pass

VK_ESCAPE = 0x1B
PW_RENDERFULLCONTENT = 0x00000002

POPUP_KEYWORDS: dict[str, list[str]] = {
    "dll_warning": ["悲报", "注入", "DLL"],
    "update": ["更新", "新版本"],
    "announcement": ["公告"],
    "achievement": ["成就", "达成"],
    "error": ["错误", "失败", "异常", "崩溃"],
}

ACTION_BUTTONS = [
    "确定", "确认", "知道了", "我知道了", "我已知晓", "好的", "好",
    "关闭", "继续", "是", "跳过", "取消",
]

CHECKBOX_KEYWORDS = ["不再显示", "不再提示", "不再询问", "今日不再", "不再提醒"]


@dataclass
class OcrItem:
    text: str
    score: float
    center: tuple[int, int]
    box: list[list[int]]


@dataclass
class Popup:
    hwnd: int
    title: str
    rect: tuple[int, int, int, int]
    image: Image.Image | None = None
    items: list[OcrItem] = field(default_factory=list)
    kind: str = "unknown"
    text: str = ""
    button: OcrItem | None = None
    checkbox: OcrItem | None = None


def _pid_to_name(pid: int) -> str:
    try:
        return psutil.Process(pid).name()
    except Exception:
        return ""


def _window_title(hwnd: int) -> str:
    try:
        return win32gui.GetWindowText(hwnd)
    except Exception:
        return ""


def _class_name(hwnd: int) -> str:
    try:
        return win32gui.GetClassName(hwnd)
    except Exception:
        return ""


def _owner(hwnd: int) -> int:
    try:
        return win32gui.GetWindow(hwnd, win32con.GW_OWNER)
    except Exception:
        return 0


def _area(hwnd: int) -> int:
    try:
        l, t, r, b = win32gui.GetWindowRect(hwnd)
        return (r - l) * (b - t)
    except Exception:
        return 0


def _exists(hwnd: int) -> bool:
    return bool(win32gui.IsWindow(hwnd) and win32gui.IsWindowVisible(hwnd))


def find_maa_windows(process_name: str = "MAA.exe") -> list[int]:
    result: list[int] = []

    def cb(hwnd: int, _: Any) -> bool:
        if not win32gui.IsWindowVisible(hwnd):
            return True
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        if _pid_to_name(pid) == process_name:
            result.append(hwnd)
        return True

    win32gui.EnumWindows(cb, None)
    return result


def main_window(process_name: str = "MAA.exe") -> int | None:
    hwnds = find_maa_windows(process_name)
    if not hwnds:
        return None
    return max(hwnds, key=_area)


def popup_windows(process_name: str = "MAA.exe") -> list[int]:
    hwnds = find_maa_windows(process_name)
    if len(hwnds) <= 1:
        return []
    main = max(hwnds, key=_area)
    popups: list[int] = []
    for h in hwnds:
        if h == main:
            continue
        if _class_name(h) == "#32770" or _owner(h) == main:
            popups.append(h)
    return popups


def capture_window(hwnd: int) -> Image.Image | None:
    try:
        l, t, r, b = win32gui.GetWindowRect(hwnd)
        w, h = r - l, b - t
        if w <= 0 or h <= 0:
            return None
        hwnd_dc = win32gui.GetWindowDC(hwnd)
        mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
        save_dc = mfc_dc.CreateCompatibleDC()
        bmp = win32ui.CreateBitmap()
        bmp.CreateCompatibleBitmap(mfc_dc, w, h)
        save_dc.SelectObject(bmp)
        res = ctypes.windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), PW_RENDERFULLCONTENT)
        info = bmp.GetInfo()
        bits = bmp.GetBitmapBits(True)
        img = Image.frombuffer("RGB", (info["bmWidth"], info["bmHeight"]), bits, "raw", "BGRX", 0, 1)
        win32gui.DeleteObject(bmp.GetHandle())
        save_dc.DeleteDC()
        mfc_dc.DeleteDC()
        win32gui.ReleaseDC(hwnd, hwnd_dc)
        if res != 1:
            logger.debug("PrintWindow 返回 {}", res)
        return img
    except Exception as e:
        logger.warning("截屏失败 hwnd={}: {}", hwnd, e)
        return None


_ocr_engine = None


def _get_ocr():
    global _ocr_engine
    if _ocr_engine is None:
        from rapidocr_onnxruntime import RapidOCR
        _ocr_engine = RapidOCR()
    return _ocr_engine


def recognize(image: Image.Image) -> list[OcrItem]:
    try:
        import numpy as np
        result, _ = _get_ocr()(np.array(image))
    except Exception as e:
        logger.warning("OCR 失败: {}", e)
        return []
    items: list[OcrItem] = []
    if not result:
        return items
    for box, text, score in result:
        xs = [int(p[0]) for p in box]
        ys = [int(p[1]) for p in box]
        items.append(OcrItem(
            text=str(text),
            score=float(score),
            center=(sum(xs) // 4, sum(ys) // 4),
            box=[[int(p[0]), int(p[1])] for p in box],
        ))
    return items


def _classify(items: list[OcrItem]) -> str:
    joined = " ".join(i.text for i in items)
    for kind, kws in POPUP_KEYWORDS.items():
        if any(kw in joined for kw in kws):
            return kind
    return "unknown"


def _find_button(items: list[OcrItem]) -> OcrItem | None:
    for name in ACTION_BUTTONS:
        for item in items:
            if item.text.strip() == name:
                return item
    for name in ACTION_BUTTONS:
        for item in items:
            if name in item.text:
                return item
    return None


def _find_checkbox(items: list[OcrItem]) -> OcrItem | None:
    for kw in CHECKBOX_KEYWORDS:
        for item in items:
            if kw in item.text:
                return item
    return None


def find_text(items: list[OcrItem], text: str) -> OcrItem | None:
    for item in items:
        if text in item.text:
            return item
    return None


def crop_region(
    image: Image.Image, region: tuple[float, float, float, float]
) -> tuple[int, int, int, int]:
    """Turn a fractional region into pixel bounds (l, t, r, b) for ``image``."""
    w, h = image.size
    l, t, r, b = region
    return int(l * w), int(t * h), int(r * w), int(b * h)


def ensure_visible(hwnd: int) -> None:
    """Restore a minimized window so capture works (some apps ignore SW_RESTORE)."""
    try:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            if win32gui.IsIconic(hwnd):
                win32gui.SendMessage(hwnd, win32con.WM_SYSCOMMAND, win32con.SC_RESTORE, 0)
            time.sleep(1.0)
    except Exception:
        pass


def _force_foreground(hwnd: int) -> None:
    try:
        if win32gui.GetForegroundWindow() == hwnd:
            return
        fg = win32gui.GetForegroundWindow()
        cur = win32api.GetCurrentThreadId()
        fg_thread = win32process.GetWindowThreadProcessId(fg)[0] if fg else cur
        tgt_thread = win32process.GetWindowThreadProcessId(hwnd)[0]
        ctypes.windll.user32.AttachThreadInput(fg_thread, cur, True)
        ctypes.windll.user32.AttachThreadInput(tgt_thread, cur, True)
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.BringWindowToTop(hwnd)
        win32gui.SetForegroundWindow(hwnd)
        ctypes.windll.user32.AttachThreadInput(tgt_thread, cur, False)
        ctypes.windll.user32.AttachThreadInput(fg_thread, cur, False)
    except Exception as e:
        logger.warning("窗口置前失败: {}", e)


def _click_screen(x: int, y: int, retries: int = 5) -> None:
    x, y = int(x), int(y)
    for attempt in range(1, retries + 1):
        try:
            win32api.SetCursorPos((x, y))
            break
        except Exception as e:
            # can fail transiently while another process seizes the mouse
            logger.debug("SetCursorPos 失败（第 {} 次）: {}", attempt, e)
            time.sleep(0.3)
    else:
        raise RuntimeError(f"无法移动鼠标到 ({x}, {y})，可能有程序抢占了鼠标")
    time.sleep(0.1)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)


class MaaPopupMonitor:
    def __init__(
        self,
        process_name: str = "MAA.exe",
        debug_dir: str | None = None,
        dismiss_checkbox: bool = False,
    ) -> None:
        self.process_name = process_name
        self.debug_dir = Path(debug_dir) if debug_dir else None
        self.dismiss_checkbox = dismiss_checkbox
        if self.debug_dir:
            self.debug_dir.mkdir(parents=True, exist_ok=True)
        self.closed: list[dict[str, Any]] = []

    def recognize_popup(self, hwnd: int) -> Popup:
        rect = win32gui.GetWindowRect(hwnd)
        img = capture_window(hwnd)
        items = recognize(img) if img is not None else []
        popup = Popup(
            hwnd=hwnd,
            title=_window_title(hwnd),
            rect=rect,
            image=img,
            items=items,
            kind=_classify(items),
            text=" ".join(i.text for i in items),
            button=_find_button(items),
            checkbox=_find_checkbox(items),
        )
        if self.debug_dir and img is not None:
            img.save(self.debug_dir / f"popup_{hwnd}_{int(time.time())}.png")
        return popup

    def close_popup(self, popup: Popup) -> bool:
        hwnd = popup.hwnd

        if popup.button is not None or (self.dismiss_checkbox and popup.checkbox is not None):
            for attempt in range(3):
                _force_foreground(hwnd)
                time.sleep(0.6)
                l, t, _, _ = win32gui.GetWindowRect(hwnd)
                if attempt == 0 and self.dismiss_checkbox and popup.checkbox is not None:
                    cx, cy = popup.checkbox.center
                    _click_screen(l + cx, t + cy)
                    logger.info("已勾选「{}」", popup.checkbox.text)
                    time.sleep(0.4)
                if popup.button is not None:
                    cx, cy = popup.button.center
                    _click_screen(l + cx, t + cy)
                time.sleep(1.2)
                if not _exists(hwnd):
                    label = popup.button.text if popup.button else "复选框"
                    logger.info("已点击「{}」关闭弹窗 [{}]", label, popup.kind)
                    return True
                logger.warning("第 {} 次点击未生效，重试", attempt + 1)

        try:
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
        except Exception:
            pass
        time.sleep(1.2)
        if not _exists(hwnd):
            logger.info("已通过 WM_CLOSE 关闭弹窗 [{}]", popup.kind)
            return True

        try:
            win32gui.PostMessage(hwnd, win32con.WM_KEYDOWN, VK_ESCAPE, 0)
            win32gui.PostMessage(hwnd, win32con.WM_KEYUP, VK_ESCAPE, 0)
        except Exception:
            pass
        time.sleep(1.0)
        if not _exists(hwnd):
            logger.info("已通过 ESC 关闭弹窗 [{}]", popup.kind)
            return True

        logger.warning("无法自动关闭弹窗: {}", popup.title)
        return False

    def scan_once(self) -> list[Popup]:
        found: list[Popup] = []
        for hwnd in popup_windows(self.process_name):
            popup = self.recognize_popup(hwnd)
            found.append(popup)
            logger.info(
                "检测到弹窗 [{}] 类型={} 内容={}",
                popup.title or "(无标题)", popup.kind, popup.text[:150] or "(无文字)",
            )
            if popup.button is not None:
                logger.info("  识别到按钮「{}」位置 {}", popup.button.text, popup.button.center)
            if popup.checkbox is not None:
                logger.info("  识别到复选框「{}」位置 {}", popup.checkbox.text, popup.checkbox.center)
            if self.close_popup(popup):
                self.closed.append({
                    "title": popup.title,
                    "kind": popup.kind,
                    "text": popup.text,
                    "button": popup.button.text if popup.button else None,
                    "checkbox": popup.checkbox.text if popup.checkbox else None,
                })
        return found

    def monitor(self, duration: float, interval: float = 2.0) -> list[dict[str, Any]]:
        deadline = time.time() + duration
        logger.info("开始监控 MAA 弹窗，持续 {} 秒", duration)
        while time.time() < deadline:
            self.scan_once()
            time.sleep(interval)
        logger.info("弹窗监控结束，共关闭 {} 个", len(self.closed))
        return self.closed
