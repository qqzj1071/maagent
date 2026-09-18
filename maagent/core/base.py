from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Any

from loguru import logger

from maagent.notify.email import EmailNotifier
from maagent.report.generator import RunReport, format_duration


class WorkflowStopped(Exception):
    """Raised when the user requests a stop."""


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def send_report(config: dict[str, Any], report: RunReport) -> bool:
    email_cfg = dict((config.get("notify", {}) or {}).get("email", {}) or {})
    account_email = str(((config.get("server", {}) or {}).get("account_email") or "")).strip()
    if account_email:
        # 登录账号后，任务报告只发给该账号邮箱
        email_cfg["to"] = [account_email]
        email_cfg["enabled"] = True
    log_dir = (config.get("app", {}) or {}).get("log_dir", "logs")
    subject = f"[Maagent] {report.game}日常 - {report.status_label}"
    return EmailNotifier(email_cfg, log_dir=log_dir).send(subject, report.to_html())


class BaseWorkflow:
    """Shared scaffolding for the MAA / MaaEnd daily workflows.

    Subclasses implement ``_run(report)`` and may override the ``_on_stop`` and
    ``_auto_close`` hooks. ``run_daily`` owns the stop/exception handling and
    the final report + email.
    """

    steps = 8  # only used for the final step log

    def __init__(
        self, config: dict[str, Any], stop_event: threading.Event | None = None
    ) -> None:
        self.config = config
        self._stop = stop_event

    # -- hooks ---------------------------------------------------------- #
    def _on_stop(self) -> None:
        """Called when the user stops the workflow (before the report)."""

    def _auto_close(self) -> None:
        """Called after the report when auto-close is enabled."""

    def _auto_close_enabled(self) -> bool:
        return bool((self.config.get("workflow", {}) or {}).get("auto_close", False))

    def _game_name(self) -> str:
        return (self.config.get("workflow", {}) or {}).get("game", "明日方舟")

    def _run(self, report: RunReport) -> None:
        raise NotImplementedError

    # -- template ------------------------------------------------------- #
    def _check_stop(self) -> None:
        if self._stop is not None and self._stop.is_set():
            raise WorkflowStopped()

    def run_daily(self) -> RunReport:
        started = time.time()
        report = RunReport(game=self._game_name(), started_at=now_str())
        stopped = False
        try:
            self._run(report)
        except WorkflowStopped:
            stopped = True
            logger.warning("收到停止请求，已中止工作流")
            self._on_stop()
            report.status = "stopped"
            report.errors.append("用户停止，工作流已中止")
        except Exception as e:
            stopped = True
            logger.exception("工作流异常: {}", e)
            report.status = "failed"
            report.errors.append(f"异常: {e}")
        if not report.finished_at:
            report.finished_at = now_str()
        if not report.duration:
            report.duration = format_duration(time.time() - started)
        return self._finish(report, auto_close=not stopped)

    def _finish(self, report: RunReport, auto_close: bool = True) -> RunReport:
        logger.info("=== 步骤 {}/{}: 生成报告并发送邮件 ===", self.steps, self.steps)
        send_report(self.config, report)
        logger.info("报告:\n{}", report.to_text())
        if auto_close and self._auto_close_enabled():
            try:
                self._auto_close()
            except Exception as e:
                logger.warning("自动关闭失败: {}", e)
        return report
