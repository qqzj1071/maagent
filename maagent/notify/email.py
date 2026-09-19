from __future__ import annotations

import smtplib
from datetime import date
from email.header import Header
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from pathlib import Path
from typing import Any

from loguru import logger

# Scenes in which the task log can be attached to the report email. These map
# 1:1 to the report statuses set by the workflows (`RunReport.status`).
LOG_STATUSES = ("success", "warning", "failed", "stopped")
_LOG_STATUS_ALIASES = {"timeout": "failed"}


def normalize_log_statuses(value: Any) -> list[str]:
    """Normalize a ``send_log_on`` value (list / str / legacy bool) to keys."""
    if value is None:
        return []
    if isinstance(value, bool):
        return list(LOG_STATUSES) if value else []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple, set)):
        return []
    return [str(item).strip().lower() for item in value if str(item).strip()]


def should_attach_log(config: dict[str, Any], status: str | None) -> bool:
    """Whether the log should be attached for this report status.

    Prefers the new ``send_log_on`` list; falls back to the legacy ``send_log``
    boolean for configs written before this option existed.
    """
    if "send_log_on" in config:
        when = normalize_log_statuses(config.get("send_log_on"))
    else:
        when = normalize_log_statuses(config.get("send_log", False))
    key = _LOG_STATUS_ALIASES.get((status or "").lower(), (status or "").lower())
    return key in when


class EmailNotifier:
    def __init__(self, config: dict[str, Any], log_dir: str | Path | None = None) -> None:
        self.config = config
        self.log_dir = Path(log_dir) if log_dir else None

    def _log_attachment(self) -> Path | None:
        if self.log_dir is None:
            return None
        path = self.log_dir / f"maagent_{date.today():%Y-%m-%d}.log"
        return path if path.exists() else None

    def send(self, subject: str, html: str, status: str | None = None) -> bool:
        cfg = self.config
        if not cfg.get("enabled"):
            logger.info("邮件通知未启用，跳过发送")
            return False
        to = cfg.get("to") or []
        if not to:
            logger.warning("邮件收件人为空，跳过发送")
            return False

        log_path = self._log_attachment() if should_attach_log(cfg, status) else None
        if log_path is not None:
            msg = MIMEMultipart()
            msg.attach(MIMEText(html, "html", "utf-8"))
            try:
                part = MIMEApplication(log_path.read_bytes())
                part.add_header(
                    "Content-Disposition", "attachment", filename=log_path.name
                )
                msg.attach(part)
                logger.info("已附加任务日志: {}", log_path.name)
            except OSError as e:
                logger.warning("读取日志附件失败: {}", e)
        else:
            msg = MIMEText(html, "html", "utf-8")
        msg["Subject"] = Header(subject, "utf-8")
        msg["From"] = formataddr(("Maagent", cfg["username"]))
        msg["To"] = ", ".join(to)
        return self._deliver(msg, to)

    def send_to(
        self, to: str | list[str], subject: str, html: str, from_name: str = "Maagent"
    ) -> bool:
        """Send an ad-hoc email to explicit recipients, ignoring ``enabled``."""
        cfg = self.config
        recipients = [to] if isinstance(to, str) else list(to)
        if not recipients:
            logger.warning("邮件收件人为空，跳过发送")
            return False
        if not cfg.get("username") or not cfg.get("password"):
            logger.warning("SMTP 账号或授权码未配置，跳过发送")
            return False
        msg = MIMEText(html, "html", "utf-8")
        msg["Subject"] = Header(subject, "utf-8")
        msg["From"] = formataddr((from_name, cfg["username"]))
        msg["To"] = ", ".join(recipients)
        return self._deliver(msg, recipients)

    def _deliver(self, msg: Any, to: list[str]) -> bool:
        cfg = self.config
        host = cfg["smtp_host"]
        port = int(cfg.get("smtp_port", 465))
        try:
            if port == 587:
                server = smtplib.SMTP(host, port, timeout=30)
                server.starttls()
            else:
                server = smtplib.SMTP_SSL(host, port, timeout=30)
            with server:
                server.login(cfg["username"], cfg["password"])
                server.sendmail(cfg["username"], to, msg.as_string())
            logger.info("邮件已发送至 {}", to)
            return True
        except Exception as e:
            logger.error("邮件发送失败: {}", e)
            return False
