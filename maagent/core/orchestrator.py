from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from loguru import logger

from maagent.adapters.maa import MaaAdapter
from maagent.control.logmonitor import (
    MaaLogMonitor,
    compute_next_deadline,
    parse_sanity,
    parse_times,
)
from maagent.control.popup import MaaPopupMonitor, main_window
from maagent.control.process import close_all
from maagent.control.weekly import (
    ANNIHILATION_TASK,
    MaaTaskToggle,
    WeeklyState,
    annihilation_config,
    apply_weekly,
    parse_annihilation,
)
from maagent.notify.email import EmailNotifier
from maagent.report.generator import RunReport


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _hms_to_sec(hms: str) -> int:
    h, m, s = (int(x) for x in hms.split(":"))
    return h * 3600 + m * 60 + s


def _fmt_duration(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h} 小时 {m} 分 {s} 秒"
    if m:
        return f"{m} 分 {s} 秒"
    return f"{s} 秒"


class Orchestrator:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        weekly_cfg = config.get("weekly", {}) or {}
        self.weekly_state = WeeklyState(weekly_cfg.get("state_file", "logs/weekly_state.json"))
        self._anni_base_runs = 0
        self._anni_base_sanity = 0

    def run_daily(self) -> RunReport:
        maa_cfg = self.config["adapters"]["maa"]
        pm_cfg = maa_cfg.get("popup_monitor", {})
        emu_cfg = maa_cfg.get("emulator", {})
        wf = self.config.get("workflow", {})
        report = RunReport(game=wf.get("game", "明日方舟"), started_at=_now())
        started = time.time()

        logger.info("=== 步骤 1/7: 清理残留 MAA 与模拟器 ===")
        if wf.get("clean_start"):
            close_all(emu_cfg)

        logger.info("=== 步骤 2/7: 启动 MAA（模拟器由 MAA 启动）===")
        adapter = MaaAdapter()
        adapter.start(maa_cfg)
        if not adapter.launch():
            report.status = "failed"
            report.errors.append("启动 MAA 失败")
            return self._finish(report, started)

        hwnd = self._wait_main_window(wf.get("window_timeout", 40))
        if hwnd is None:
            report.status = "failed"
            report.errors.append("未找到 MAA 主窗口")
            return self._finish(report, started)

        monitor = MaaPopupMonitor(
            debug_dir=pm_cfg.get("debug_dir"),
            dismiss_checkbox=pm_cfg.get("dismiss_checkbox", False),
        )
        logmon = MaaLogMonitor(hwnd)

        logger.info("=== 步骤 3/7: 等待就绪并清理弹窗 ===")
        self._settle(monitor, wf.get("startup_settle_seconds", 25))

        logger.info("=== 步骤 4/7: 周常 - 体力药 / 剿灭刷取 ===")
        try:
            apply_weekly(self.config.get("weekly", {}), state=self.weekly_state)
            anni = self.weekly_state.annihilation()
            self._anni_base_runs = int(anni.get("runs", 0))
            self._anni_base_sanity = int(anni.get("sanity", 0))
        except Exception as e:
            logger.warning("周常处理异常: {}", e)

        logger.info("=== 步骤 5/7: 触发 Link Start ===")
        if not self._ensure_daily_started(adapter, logmon, monitor, wf):
            report.status = "failed"
            report.errors.append("Link Start 后日常任务未成功开始")
            report.popups_closed = monitor.closed
            report.logs = logmon.logs
            return self._finish(report, started)
        logger.info("日常任务已开始运行")

        logger.info("=== 步骤 6/7: 监控日志与弹窗，等待日常完成 ===")
        run_start = time.time()

        def _poll() -> None:
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
        report.errors = logmon.detect_errors()
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
        else:
            report.status = "success"

        return self._finish(report, started)

    def _anni_limits(self) -> tuple[int, int]:
        cfg = annihilation_config(self.config.get("weekly", {}))
        return (
            int(cfg.get("weekly_limit", 5)),
            int(cfg.get("sanity_threshold", 124)),
        )

    def _check_annihilation(self, logmon: MaaLogMonitor, hwnd: int) -> None:
        """Watch the panel log and, once the weekly annihilation is done, uncheck it."""
        weekly_cfg = self.config.get("weekly", {})
        if not annihilation_config(weekly_cfg).get("enabled"):
            return
        limit, threshold = self._anni_limits()
        if self.weekly_state.annihilation_done(limit, threshold):
            return
        run_runs, run_sanity = parse_annihilation(logmon.logs)
        if run_runs == 0 and run_sanity == 0:
            return
        total_runs = self._anni_base_runs + run_runs
        total_sanity = self._anni_base_sanity + run_sanity
        self.weekly_state.set_annihilation(total_runs, total_sanity, limit, threshold)
        if self.weekly_state.annihilation_done(limit, threshold):
            logger.info(
                "周常：剿灭刷取已完成（{}/{} 次，{} 理智），取消勾选「{}」",
                total_runs, limit, total_sanity, ANNIHILATION_TASK,
            )
            try:
                MaaTaskToggle().set_state(hwnd, ANNIHILATION_TASK, False)
            except Exception as e:
                logger.warning("周常：取消勾选剿灭刷取失败: {}", e)

    def _finalize_annihilation(self, report: RunReport, logmon: MaaLogMonitor, hwnd: int) -> None:
        weekly_cfg = self.config.get("weekly", {})
        if not annihilation_config(weekly_cfg).get("enabled"):
            return
        limit, threshold = self._anni_limits()
        self._check_annihilation(logmon, hwnd)
        anni = self.weekly_state.annihilation()
        runs = int(anni.get("runs", 0))
        sanity = int(anni.get("sanity", 0))
        if anni.get("done"):
            report.annihilation = f"已完成剿灭作战（本周 {runs}/{limit} 次，消耗理智 {sanity}）"
        elif runs or sanity:
            report.annihilation = f"剿灭进行中（本周 {runs}/{limit} 次，消耗理智 {sanity}）"

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

        for attempt in range(1, 4):
            monitor.scan_once()
            logger.info("触发 Link Start（第 {} 次）", attempt)
            adapter.press_link_start()
            deadline = time.time() + wf.get("start_timeout", 90)
            while time.time() < deadline:
                monitor.scan_once()
                logmon.collect_logs()
                if logmon.detect_started():
                    return True
                if logmon.button_state() == "idle":
                    logger.warning("任务未开始即回到空闲，准备重试")
                    break
                time.sleep(3.0)
        return False

    def _settle(self, monitor: MaaPopupMonitor, seconds: float) -> None:
        logger.info("等待 MAA 就绪并清理弹窗（约 {} 秒）...", seconds)
        deadline = time.time() + seconds
        while time.time() < deadline:
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
            report.duration = _fmt_duration((_hms_to_sec(end_t) - _hms_to_sec(start_t)) % 86400)
        sanity = parse_sanity(report.logs)
        if sanity:
            report.sanity = f"{sanity[0]}/{sanity[1]}"
            report.next_deadline = compute_next_deadline(end_t, sanity[0], sanity[1])

    def _finish(self, report: RunReport, started: float) -> RunReport:
        logger.info("=== 步骤 7/7: 生成报告并发送邮件 ===")
        if not report.finished_at:
            report.finished_at = _now()
        if not report.duration:
            report.duration = _fmt_duration(time.time() - started)

        email_cfg = self.config.get("notify", {}).get("email", {})
        notifier = EmailNotifier(email_cfg)
        subject = f"[maagent] {report.game}日常 - {report.status_label}"
        notifier.send(subject, report.to_html())

        logger.info("报告:\n{}", report.to_text())

        if self.config.get("workflow", {}).get("auto_close"):
            logger.info("任务完成，自动关闭 MAA 与模拟器")
            try:
                close_all(self.config.get("adapters", {}).get("maa", {}).get("emulator", {}))
            except Exception as e:
                logger.warning("自动关闭 MAA 与模拟器失败: {}", e)

        return report
