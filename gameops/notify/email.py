from __future__ import annotations

import smtplib
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr
from typing import Any

from loguru import logger


class EmailNotifier:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    def send(self, subject: str, html: str) -> bool:
        cfg = self.config
        if not cfg.get("enabled"):
            logger.info("邮件通知未启用，跳过发送")
            return False
        to = cfg.get("to") or []
        if not to:
            logger.warning("邮件收件人为空，跳过发送")
            return False

        msg = MIMEText(html, "html", "utf-8")
        msg["Subject"] = Header(subject, "utf-8")
        msg["From"] = formataddr(("GameOps", cfg["username"]))
        msg["To"] = ", ".join(to)

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
