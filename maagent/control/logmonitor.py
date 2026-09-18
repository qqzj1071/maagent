from __future__ import annotations

import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import win32gui
from loguru import logger
from PIL import Image

from maagent.control.popup import capture_window, crop_region, recognize

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

# MAA's gui.log completion markers (its own wording, not the panel's)
GUI_FINISH_MARKERS = ("任务已全部完成", "全部任务已完成", "全部完成")

RUNNING_KEYWORDS = ["停止", "中止"]
IDLE_KEYWORDS = ["Link", "Start", "开始"]
START_KEYWORDS = ["连接成功", "开始任务", "开始唤醒", "理智作战", "基建换班"]
COMPLETE_KEYWORDS = ["全部任务已完成", "任务已完成", "任务完成", "全部完成", "已完成"]
ERROR_KEYWORDS = ["错误", "失败", "异常", "出错", "无法"]
CONNECTION_FAIL_KEYWORDS = ["连接失败", "连接错误", "连接超时", "无法连接", "连接中断", "重连失败"]
CONNECTION_OK_KEYWORDS = ["连接成功", "已连接", "连接正常", "重新连接成功"]
SUBTASK_ERROR_KEYWORDS = ["任务出错", "任务异常", "执行异常", "子任务"]
IGNORE_ERROR_KEYWORDS = ["FPS", "补帧", "画面"]


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
    def __init__(
        self, hwnd: int, debug_dir: str | None = None, gui_log: str | Path | None = None
    ) -> None:
        self.hwnd = hwnd
        self.debug_dir = debug_dir
        self.logs: list[str] = []
        self._seen: set[str] = set()
        self._gui_log = Path(gui_log) if gui_log else None
        self._gui_offset = 0

    def mark_gui_log(self) -> None:
        """Remember the current end of MAA's gui.log so only new lines are read."""
        if self._gui_log and self._gui_log.exists():
            try:
                self._gui_offset = self._gui_log.stat().st_size
            except Exception:
                self._gui_offset = 0

    def gui_log_finished(self) -> bool:
        """Fallback completion check from MAA's own log (panel OCR can miss it).

        MAA writes gui.log in the system ANSI code page (GBK on zh-CN) and the
        completion line is 「任务已全部完成！」, so match raw bytes for both
        GBK and UTF-8 to stay encoding-agnostic.
        """
        if not self._gui_log or not self._gui_log.exists():
            return False
        try:
            size = self._gui_log.stat().st_size
            start = self._gui_offset if size >= self._gui_offset else 0
            with open(self._gui_log, "rb") as f:
                f.seek(start)
                data = f.read()
        except Exception:
            return False
        for marker in GUI_FINISH_MARKERS:
            for enc in ("gbk", "utf-8"):
                try:
                    if marker.encode(enc) in data:
                        return True
                except Exception:
                    pass
        return False

    def _crop(self, region: tuple[float, float, float, float]) -> Image.Image | None:
        img = capture_window(self.hwnd)
        if img is None:
            return None
        return img.crop(crop_region(img, region))

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
        # ignore 牛杂 custom tasks (e.g. "开始任务: (自定任务)"), they are not the daily
        return any(
            any(k in ln for k in START_KEYWORDS) and "自定任务" not in ln
            for ln in self.logs
        )

    def classify(self) -> tuple[list[str], list[str]]:
        """Split problem lines into (fatal errors, sub-task warnings).

        - A connection failure is ignored when MAA later connects successfully
          (it retried), so a transient drop is not reported as a failure.
        - Task-level lines like 「任务出错：仓库识别」 are sub-task warnings, not
          fatal errors (mirrors the MaaEnd report's 子任务报错 handling).
        """
        ok_positions = [
            i
            for i, ln in enumerate(self.logs)
            if any(k in ln for k in CONNECTION_OK_KEYWORDS)
        ]
        errors: list[str] = []
        warnings: list[str] = []
        for i, ln in enumerate(self.logs):
            if not any(k in ln for k in ERROR_KEYWORDS):
                continue
            if any(ig in ln for ig in IGNORE_ERROR_KEYWORDS):
                continue
            if any(k in ln for k in SUBTASK_ERROR_KEYWORDS):
                warnings.append(ln)
            elif any(k in ln for k in CONNECTION_FAIL_KEYWORDS):
                if not any(pos > i for pos in ok_positions):
                    errors.append(ln)
            else:
                errors.append(ln)
        return errors, warnings

    def detect_errors(self) -> list[str]:
        return self.classify()[0]

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
            if self.detect_complete() or self.gui_log_finished():
                return "complete"
            if self.button_state() == "idle":
                return "idle"
            time.sleep(interval)
        return "timeout"
