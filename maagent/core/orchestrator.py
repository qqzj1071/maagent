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
from maagent.control.weekly import apply_weekly
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

    def run_daily(self) -> RunReport:
        maa_cfg = self.config["adapters"]["maa"]
        pm_cfg = maa_cfg.get("popup_monitor", {})
        emu_cfg = maa_cfg.get("emulator", {})
        wf = self.config.get("workflow", {})
        report = RunReport(game=wf.get("game", "明日方舟"), started_at=_now())
        started = time.time()

        logger.info("=== 步骤 1/6: 清理残留 MAA 与模拟器 ===")
        if wf.get("clean_start"):
            close_all(emu_cfg)

        logger.info("=== 步骤 2/6: 启动 MAA（模拟器由 MAA 启动）===")
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

        logger.info("=== 步骤 4/7: 周常 - 检查/设置「使用药剂」 ===")
        try:
            apply_weekly(self.config.get("weekly", {}))
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
        result = logmon.wait_idle(
            timeout=wf.get("daily_timeout_seconds", 3600),
            interval=wf.get("poll_interval", 10),
            on_poll=monitor.scan_once,
        )
        run_seconds = time.time() - run_start
        logger.info("监控结束: {}（运行 {} 秒）", result, int(run_seconds))

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
        return report
