from __future__ import annotations

import re
import time
from datetime import datetime, timedelta
from typing import Any, Callable

import win32gui
from loguru import logger
from PIL import Image

from maagent.control.popup import capture_window, recognize

SANITY_RECOVER_MINUTES = 6


def parse_times(logs: list[str]) -> tuple[str, str]:
    stamps: list[str] = []
    for ln in logs:
        m = re.match(r"\s*(\d{1,2}:\d{2}:\d{2})", ln)
        if m:
            stamps.append(m.group(1))
    if not stamps:
        return "", ""
    return stamps[0], stamps[-1]


def parse_sanity(logs: list[str]) -> tuple[int, int] | None:
    for ln in reversed(logs):
        m = re.search(r"理智[：:]\s*(\d+)\s*/\s*(\d+)", ln)
        if m:
            return int(m.group(1)), int(m.group(2))
    return None


def compute_next_deadline(
    end_hms: str,
    current: int,
    maximum: int,
    rate_minutes: int = SANITY_RECOVER_MINUTES,
    base: datetime | None = None,
) -> str:
    if not end_hms:
        return ""
    try:
        h, mi, s = (int(x) for x in end_hms.split(":"))
    except ValueError:
        return ""
    base = base or datetime.now()
    end_dt = base.replace(hour=h, minute=mi, second=s, microsecond=0)
    deadline = end_dt + timedelta(minutes=max(0, maximum - current) * rate_minutes)
    return deadline.strftime("%m-%d %H:%M")

LOG_REGION = (0.655, 0.15, 0.995, 0.98)
BUTTON_REGION = (0.10, 0.83, 0.29, 0.97)

RUNNING_KEYWORDS = ["停止", "中止"]
IDLE_KEYWORDS = ["Link", "Start", "开始"]
START_KEYWORDS = ["连接成功", "开始任务", "开始唤醒", "理智作战", "基建换班"]
COMPLETE_KEYWORDS = ["全部任务已完成", "任务已完成", "任务完成", "全部完成", "已完成"]
ERROR_KEYWORDS = ["错误", "失败", "异常", "出错", "无法"]


def _group_lines(items: list[Any]) -> list[str]:
    lines: list[dict[str, Any]] = []
    for it in sorted(items, key=lambda i: (i.center[1], i.center[0])):
        for line in lines:
            if abs(line["y"] - it.center[1]) <= 14:
                line["items"].append(it)
                line["y"] = (line["y"] + it.center[1]) / 2
                break
        else:
            lines.append({"y": it.center[1], "items": [it]})
    result = []
    for line in sorted(lines, key=lambda x: x["y"]):
        line["items"].sort(key=lambda i: i.center[0])
        result.append(" ".join(i.text for i in line["items"]))
    return result


class MaaLogMonitor:
    def __init__(self, hwnd: int, debug_dir: str | None = None) -> None:
        self.hwnd = hwnd
        self.debug_dir = debug_dir
        self.logs: list[str] = []
        self._seen: set[str] = set()

    def _crop(self, region: tuple[float, float, float, float]) -> Image.Image | None:
        img = capture_window(self.hwnd)
        if img is None:
            return None
        w, h = img.size
        l, t, r, b = region
        return img.crop((int(l * w), int(t * h), int(r * w), int(b * h)))

    def read_logs(self) -> list[str]:
        img = self._crop(LOG_REGION)
        if img is None:
            return []
        return _group_lines(recognize(img))

    def read_button(self) -> str:
        img = self._crop(BUTTON_REGION)
        if img is None:
            return ""
        return " ".join(i.text for i in recognize(img))

    def button_state(self) -> str:
        txt = self.read_button()
        if any(k in txt for k in RUNNING_KEYWORDS):
            return "running"
        if any(k in txt for k in IDLE_KEYWORDS):
            return "idle"
        return "unknown"

    def collect_logs(self) -> list[str]:
        lines = self.read_logs()
        for line in lines:
            if line and line not in self._seen:
                self._seen.add(line)
                self.logs.append(line)
        return lines

    def detect_complete(self) -> bool:
        return any(any(k in ln for k in COMPLETE_KEYWORDS) for ln in self.logs)

    def detect_started(self) -> bool:
        return any(any(k in ln for k in START_KEYWORDS) for ln in self.logs)

    def detect_errors(self) -> list[str]:
        ignore = ["FPS", "补帧", "画面"]
        return [
            ln for ln in self.logs
            if any(k in ln for k in ERROR_KEYWORDS) and not any(ig in ln for ig in ignore)
        ]

    def _alive(self) -> bool:
        return bool(win32gui.IsWindow(self.hwnd))

    def wait_idle(
        self,
        timeout: float,
        interval: float = 5.0,
        on_poll: Callable[[], None] | None = None,
    ) -> str:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not self._alive():
                logger.info("MAA 窗口已关闭（可能已自行退出）")
                return "exited"
            self.collect_logs()
            if on_poll is not None:
                on_poll()
            if self.detect_complete():
                return "complete"
            if self.button_state() == "idle":
                return "idle"
            time.sleep(interval)
        return "timeout"
