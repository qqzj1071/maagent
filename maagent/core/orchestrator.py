from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import win32gui
from loguru import logger

from maagent.adapters.maa import MaaAdapter
from maagent.control.logmonitor import (
    BUTTON_REGION,
    MaaLogMonitor,
    compute_next_deadline,
    parse_sanity,
    parse_times,
)
from maagent.control.monthly import MonthlyState, apply_monthly, format_monthly
from maagent.control.popup import (
    MaaPopupMonitor,
    _click_screen,
    _force_foreground,
    capture_window,
    ensure_visible,
    find_text,
    main_window,
    recognize,
)
from maagent.control.process import close_all
from maagent.control.weekly import (
    ANNIHILATION_TASK,
    DEFAULT_ANNIHILATION_CAP,
    MaaTaskToggle,
    WeeklyState,
    annihilation_config,
    apply_weekly,
    parse_annihilation,
    today_runs_annihilation,
)
from maagent.core.base import BaseWorkflow
from maagent.report.generator import RunReport, format_duration


def _hms_to_sec(hms: str) -> int:
    h, m, s = (int(x) for x in hms.split(":"))
    return h * 3600 + m * 60 + s


class Orchestrator(BaseWorkflow):
    def __init__(
        self, config: dict[str, Any], stop_event: threading.Event | None = None
    ) -> None:
        super().__init__(config, stop_event)
        self._monitor: MaaPopupMonitor | None = None
        self._logmon: MaaLogMonitor | None = None
        weekly_cfg = config.get("weekly", {}) or {}
        self.weekly_state = WeeklyState(weekly_cfg.get("state_file", "logs/weekly_state.json"))
        monthly_cfg = config.get("monthly", {}) or {}
        self.monthly_state = MonthlyState(monthly_cfg.get("state_file", "logs/monthly_state.json"))

    def _on_stop(self) -> None:
        self._stop_maa_task()

    def _stop_maa_task(self) -> None:
        """Emergency stop: click MAA's 停止 button to abort the running daily."""
        try:
            hwnd = main_window()
            if hwnd is None:
                logger.warning("急停：未找到 MAA 主窗口，无法停止其任务")
                return
            ensure_visible(hwnd)
            _force_foreground(hwnd)
            time.sleep(0.6)
            image = capture_window(hwnd)
            if image is None:
                logger.warning("急停：无法截取 MAA 窗口")
                return
            w, h = image.size
            l, t, r, b = (
                int(BUTTON_REGION[0] * w), int(BUTTON_REGION[1] * h),
                int(BUTTON_REGION[2] * w), int(BUTTON_REGION[3] * h),
            )
            items = recognize(image.crop((l, t, r, b)))
            item = None
            for name in ("停止", "中止"):
                item = find_text(items, name)
                if item is not None:
                    break
            if item is None:
                logger.info("急停：MAA 未显示「停止」按钮，任务可能已结束")
                return
            wx, wy, _, _ = win32gui.GetWindowRect(hwnd)
            _click_screen(wx + l + item.center[0], wy + t + item.center[1])
            logger.info("急停：已点击 MAA 的「停止」按钮，日常任务已终止")
            time.sleep(1.5)
            if self._monitor is not None:
                self._monitor.scan_once()
        except Exception as e:
            logger.warning("急停：停止 MAA 任务失败: {}", e)

    def _run(self, report: RunReport) -> None:
        maa_cfg = self.config["adapters"]["maa"]
        pm_cfg = maa_cfg.get("popup_monitor", {})
        emu_cfg = maa_cfg.get("emulator", {})
        wf = self.config.get("workflow", {})

        logger.info("=== 步骤 1/8: 清理残留 MAA 与模拟器 ===")
        if wf.get("clean_start"):
            close_all(emu_cfg)

        logger.info("=== 步骤 2/8: 启动 MAA（模拟器由 MAA 启动）===")
        adapter = MaaAdapter()
        adapter.start(maa_cfg)
        if not adapter.launch():
            report.status = "failed"
            report.errors.append("启动 MAA 失败")
            return

        hwnd = self._wait_main_window(wf.get("window_timeout", 40))
        if hwnd is None:
            report.status = "failed"
            report.errors.append("未找到 MAA 主窗口")
            return

        monitor = MaaPopupMonitor(
            debug_dir=pm_cfg.get("debug_dir"),
            dismiss_checkbox=pm_cfg.get("dismiss_checkbox", False),
        )
        gui_log = Path(maa_cfg.get("path", "")) / "debug" / "gui.log"
        logmon = MaaLogMonitor(hwnd, gui_log=gui_log)
        logmon.mark_gui_log()
        self._monitor = monitor
        self._logmon = logmon

        logger.info("=== 步骤 3/8: 等待就绪并清理弹窗 ===")
        self._settle(monitor, wf.get("startup_settle_seconds", 25))

        logger.info("=== 步骤 4/8: 周常 - 体力药 / 剿灭刷取 ===")
        try:
            apply_weekly(self.config.get("weekly", {}), state=self.weekly_state)
        except Exception as e:
            logger.warning("周常处理异常: {}", e)

        logger.info("=== 步骤 5/8: 触发 Link Start ===")
        if not self._ensure_daily_started(adapter, logmon, monitor, wf):
            report.status = "failed"
            report.errors.append("Link Start 后日常任务未成功开始")
            report.popups_closed = monitor.closed
            report.logs = logmon.logs
            return
        logger.info("日常任务已开始运行")

        logger.info("=== 步骤 6/8: 监控日志与弹窗，等待日常完成 ===")
        run_start = time.time()

        def _poll() -> None:
            self._check_stop()
            monitor.scan_once()
            self._check_annihilation(logmon, hwnd)

        result = logmon.wait_idle(
            timeout=wf.get("daily_timeout_seconds", 3600),
            interval=wf.get("poll_interval", 10),
            on_poll=_poll,
        )
        run_seconds = time.time() - run_start
        logger.info("监控结束: {}（运行 {} 秒）", result, int(run_seconds))
        self._finalize_annihilation(report, logmon, hwnd)

        report.logs = logmon.logs
        report.popups_closed = monitor.closed
        report.errors, report.warnings = logmon.classify()
        self._populate_from_logs(report)
        min_runtime = wf.get("min_runtime_seconds", 90)
        if result == "timeout":
            report.status = "timeout"
        elif report.errors:
            report.status = "failed"
        elif result in ("idle", "exited") and run_seconds < min_runtime:
            report.status = "failed"
            report.errors.append(
                f"任务在 {int(run_seconds)} 秒内结束，疑似未成功连接模拟器或立即失败"
            )
        elif report.warnings:
            report.status = "warning"
        else:
            report.status = "success"

        logger.info("=== 步骤 7/8: 月常 - 绿票 / 黄票商店（模拟器保持开启）===")
        try:
            monthly_result = apply_monthly(
                self.config.get("monthly", {}),
                state=self.monthly_state,
                on_poll=monitor.scan_once,
            )
            report.monthly = format_monthly(monthly_result)
        except Exception as e:
            logger.warning("月常处理异常: {}", e)

    def _anni_cap(self) -> int:
        cfg = annihilation_config(self.config.get("weekly", {}))
        return int(cfg.get("cap", DEFAULT_ANNIHILATION_CAP))

    def _check_annihilation(self, logmon: MaaLogMonitor, hwnd: int) -> None:
        """Watch the panel log and, once 剿灭模式 hits the weekly cap, uncheck it."""
        weekly_cfg = self.config.get("weekly", {})
        if not annihilation_config(weekly_cfg).get("enabled"):
            return
        cap = self._anni_cap()
        if self.weekly_state.annihilation_done(cap):
            return
        progress = parse_annihilation(logmon.logs)
        if progress <= 0:
            return
        self.weekly_state.set_annihilation(progress, cap)
        if self.weekly_state.annihilation_done(cap):
            logger.info(
                "周常：剿灭刷取已完成（剿灭模式 {}/{}），取消勾选「{}」",
                progress, cap, ANNIHILATION_TASK,
            )
            try:
                MaaTaskToggle().set_state(hwnd, ANNIHILATION_TASK, False)
            except Exception as e:
                logger.warning("周常：取消勾选剿灭刷取失败: {}", e)

    def _finalize_annihilation(self, report: RunReport, logmon: MaaLogMonitor, hwnd: int) -> None:
        weekly_cfg = self.config.get("weekly", {})
        if not annihilation_config(weekly_cfg).get("enabled"):
            return
        cap = self._anni_cap()
        self._check_annihilation(logmon, hwnd)
        progress = int(self.weekly_state.annihilation().get("progress", 0))
        if self.weekly_state.annihilation_done(cap):
            report.annihilation = f"已完成剿灭作战（剿灭模式 {progress}/{cap}）"
        elif today_runs_annihilation(weekly_cfg):
            report.annihilation = (
                f"⚠️ 剿灭作战未完成（剿灭模式 {progress}/{cap}），请再次运行日常继续剿灭"
            )
        elif progress:
            report.annihilation = f"剿灭作战进行中（剿灭模式 {progress}/{cap}）"

    def _ensure_daily_started(
        self,
        adapter: MaaAdapter,
        logmon: MaaLogMonitor,
        monitor: MaaPopupMonitor,
        wf: dict[str, Any],
    ) -> bool:
        logmon.collect_logs()
        if logmon.detect_started():
            logger.info("检测到日常任务已在运行，跳过 Link Start")
            return True

        if logmon.button_state() == "running":
            logger.info("MAA 正在启动模拟器阶段，等待其结束...")
            logmon.wait_idle(timeout=wf.get("emulator_wait_timeout", 180), interval=3.0)

        hwnd = logmon.hwnd
        start_timeout = wf.get("start_timeout", 90)
        hotkey_timeout = min(20, start_timeout)
        for attempt in range(1, 4):
            self._check_stop()
            monitor.scan_once()
            # The start hotkey goes to the focused window, so bring MAA to the front.
            if hwnd:
                ensure_visible(hwnd)
                _force_foreground(hwnd)
                time.sleep(0.6)
            logger.info("触发 Link Start（第 {} 次）", attempt)
            adapter.press_link_start()
            if self._wait_started(logmon, monitor, hotkey_timeout):
                return True
            # The hotkey may be unset/disabled in MAA; fall back to clicking the button.
            logger.info("Link Start 热键未生效，改为点击按钮")
            if self._click_link_start(hwnd) and self._wait_started(
                logmon, monitor, start_timeout
            ):
                return True
        return False

    def _wait_started(
        self, logmon: MaaLogMonitor, monitor: MaaPopupMonitor, timeout: float
    ) -> bool:
        """Poll until the daily actually starts (or the button returns to idle)."""
        pressed = time.time()
        deadline = pressed + timeout
        while time.time() < deadline:
            self._check_stop()
            monitor.scan_once()
            logmon.collect_logs()
            if logmon.detect_started():
                return True
            # A short grace period avoids reading the stale button right after the click.
            if time.time() - pressed > 6.0 and logmon.button_state() == "idle":
                logger.warning("任务未开始即回到空闲，准备重试")
                return False
            time.sleep(3.0)
        return logmon.detect_started()

    def _click_link_start(self, hwnd: int | None) -> bool:
        """OCR-locate and click MAA's Link Start button (fallback for the hotkey)."""
        if not hwnd:
            return False
        try:
            image = capture_window(hwnd)
            if image is None:
                return False
            w, h = image.size
            l, t, r, b = (
                int(BUTTON_REGION[0] * w), int(BUTTON_REGION[1] * h),
                int(BUTTON_REGION[2] * w), int(BUTTON_REGION[3] * h),
            )
            items = recognize(image.crop((l, t, r, b)))
            item = None
            for name in ("Link", "Start", "开始"):
                item = find_text(items, name)
                if item is not None:
                    break
            if item is None:
                return False
            wx, wy, _, _ = win32gui.GetWindowRect(hwnd)
            _click_screen(wx + l + item.center[0], wy + t + item.center[1])
            logger.info("已点击 MAA 的 Link Start 按钮")
            time.sleep(1.0)
            return True
        except Exception as e:
            logger.warning("点击 Link Start 按钮失败: {}", e)
            return False

    def _settle(self, monitor: MaaPopupMonitor, seconds: float) -> None:
        logger.info("等待 MAA 就绪并清理弹窗（约 {} 秒）...", seconds)
        deadline = time.time() + seconds
        while time.time() < deadline:
            self._check_stop()
            monitor.scan_once()
            time.sleep(2.0)

    def _wait_main_window(self, timeout: float) -> int | None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            hwnd = main_window()
            if hwnd:
                return hwnd
            time.sleep(2.0)
        return None

    @staticmethod
    def _populate_from_logs(report: RunReport) -> None:
        start_t, end_t = parse_times(report.logs)
        if start_t:
            report.started_at = start_t
        if end_t:
            report.finished_at = end_t
        if start_t and end_t:
            report.duration = format_duration((_hms_to_sec(end_t) - _hms_to_sec(start_t)) % 86400)
        sanity = parse_sanity(report.logs)
        if sanity:
            report.sanity = f"{sanity[0]}/{sanity[1]}"
            report.next_deadline = compute_next_deadline(end_t, sanity[0], sanity[1])

    def _auto_close(self) -> None:
        logger.info("任务完成，自动关闭 MAA 与模拟器")
        close_all(self.config.get("adapters", {}).get("maa", {}).get("emulator", {}))
