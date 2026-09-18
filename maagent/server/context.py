from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from maagent.core.controller import WorkflowController
from maagent.notify.email import EmailNotifier
from maagent.server.store import AccountStore

DEFAULT_SETTINGS: dict[str, Any] = {
    "enabled": False,
    "host": "0.0.0.0",
    "port": 8765,
    "db_path": "config/accounts.db",
    "report_file": "logs/reports.jsonl",
    "account_email": "",
    "public_url": "",
    "last_notified_email": "",
    "debug": False,
    "allow_register": True,
    "token_ttl_hours": 720,
    "code_ttl_minutes": 10,
    "code_length": 6,
    "code_cooldown_seconds": 60,
    "code_hourly_limit": 10,
    "max_code_attempts": 5,
    "min_password_length": 8,
    "tls": {"certfile": "", "keyfile": ""},
}


@dataclass
class ServerContext:
    settings: dict[str, Any]
    store: AccountStore
    mailer: EmailNotifier
    controller: WorkflowController
    debug: bool


def server_settings(config: dict[str, Any]) -> dict[str, Any]:
    settings = dict(DEFAULT_SETTINGS)
    incoming = config.get("server") or {}
    settings.update({k: v for k, v in incoming.items() if k != "tls"})
    tls = dict(DEFAULT_SETTINGS["tls"])
    tls.update(incoming.get("tls") or {})
    settings["tls"] = tls
    return settings


def build_context(
    config: dict[str, Any], controller: WorkflowController | None = None
) -> ServerContext:
    settings = server_settings(config)
    email_cfg = ((config.get("notify") or {}).get("email")) or {}
    return ServerContext(
        settings=settings,
        store=AccountStore(settings["db_path"]),
        mailer=EmailNotifier(email_cfg),
        controller=controller
        or WorkflowController(config, settings.get("report_file", "logs/reports.jsonl")),
        debug=bool(settings.get("debug")),
    )
