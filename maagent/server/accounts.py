from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

from loguru import logger

from maagent.notify.email import EmailNotifier
from maagent.server.security import (
    code_digest,
    hash_password,
    new_code,
    verify_password,
)
from maagent.server.store import AccountStore, DuplicateAccount, utcnow

PURPOSES = ("register", "reset_password", "bind_email")
PURPOSE_LABELS = {
    "register": "注册账号",
    "reset_password": "重置密码",
    "bind_email": "绑定邮箱",
}
USERNAME_RE = re.compile(r"^[a-z0-9_.-]{3,32}$")
PHONE_RE = re.compile(r"^\+?\d{6,15}$")

DEFAULT_SETTINGS: dict[str, Any] = {
    "code_ttl_minutes": 10,
    "code_length": 6,
    "code_cooldown_seconds": 60,
    "code_hourly_limit": 10,
    "max_code_attempts": 5,
}


class AccountError(Exception):
    """A user-facing account failure (``code`` maps to an HTTP status)."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


class AccountService:
    """Shared account operations for the HTTP API and the desktop settings UI."""

    def __init__(
        self,
        store: AccountStore,
        mailer: EmailNotifier,
        settings: dict[str, Any] | None = None,
    ) -> None:
        self.store = store
        self.mailer = mailer
        self.settings = {**DEFAULT_SETTINGS, **(settings or {})}

    def _int(self, key: str, default: int) -> int:
        try:
            return int(self.settings.get(key, default))
        except (TypeError, ValueError):
            return default

    # -- verification codes --------------------------------------------- #
    def _send_code_mail(self, email: str, code: str, purpose: str) -> bool:
        label = PURPOSE_LABELS.get(purpose, purpose)
        html = (
            f"<p>你正在{label}，验证码为：</p>"
            f"<p style='font-size:24px;font-weight:bold;letter-spacing:4px'>{code}</p>"
            f"<p>有效期 {self._int('code_ttl_minutes', 10)} 分钟。"
            f"如非本人操作请忽略本邮件。</p>"
        )
        if not (self.mailer.config.get("password") or ""):
            logger.warning("SMTP 未配置授权码，验证码未发送")
            return False
        return self.mailer.send_to(email, f"Maagent {label}验证码", html)

    def send_code(
        self,
        email: str,
        purpose: str = "register",
        *,
        enforce_limits: bool = False,
        debug: bool = False,
    ) -> dict[str, Any]:
        """Create and email a verification code. Returns ttl/code/sent."""
        if purpose not in PURPOSES:
            raise AccountError("bad_purpose", "验证码用途无效")

        account = self.store.get_account_by_email(email)
        if purpose == "register" and account is not None:
            raise AccountError("email_exists", "该邮箱已注册")
        if purpose == "bind_email" and account is not None:
            raise AccountError("email_exists", "该邮箱已被其它账号使用")
        if purpose == "reset_password" and account is None:
            raise AccountError("email_not_found", "该邮箱未注册")

        if enforce_limits:
            cooldown = self._int("code_cooldown_seconds", 60)
            last = self.store.last_code_time(email, purpose)
            if last is not None and (utcnow() - last).total_seconds() < cooldown:
                raise AccountError("too_frequent", f"请求过于频繁，请 {cooldown} 秒后再试")
            hourly_limit = self._int("code_hourly_limit", 10)
            if self.store.count_codes_since(email, utcnow() - timedelta(hours=1)) >= hourly_limit:
                raise AccountError("rate_limited", "验证码请求次数过多，请稍后再试")

        ttl = self._int("code_ttl_minutes", 10)
        code = new_code(self._int("code_length", 6))
        self.store.create_email_code(email, purpose, code_digest(code), ttl)
        self.store.purge_expired()

        sent = self._send_code_mail(email, code, purpose)
        if not sent and not debug:
            raise AccountError("email_unavailable", "邮件服务不可用，验证码发送失败")
        return {"ttl": ttl, "code": code, "sent": sent}

    # -- account lifecycle ---------------------------------------------- #
    def register(
        self, email: str, phone: str | None, password: str, code: str
    ) -> dict[str, Any]:
        if self.store.get_account_by_email(email) is not None:
            raise AccountError("email_exists", "该邮箱已注册")
        if phone and self.store.get_account_by_phone(phone) is not None:
            raise AccountError("phone_exists", "该手机号已绑定其它账号")
        max_attempts = self._int("max_code_attempts", 5)
        if not self.store.consume_email_code(
            email, "register", code_digest(code), max_attempts
        ):
            raise AccountError("bad_code", "验证码错误或已过期")
        try:
            return self.store.create_account(email, phone, hash_password(password))
        except DuplicateAccount as e:
            if str(e) == "phone":
                raise AccountError("phone_exists", "该手机号已绑定其它账号") from e
            raise AccountError("email_exists", "该邮箱已注册") from e

    def create_local(self, username: str, password: str) -> dict[str, Any]:
        """Create a local account that needs no email verification.

        Used on a fresh machine so the owner can sign in and start the remote
        service without configuring SMTP. The phone web can then sign in with
        the same username + password.
        """
        username = (username or "").strip().lower()
        if not USERNAME_RE.match(username):
            raise AccountError("bad_username", "用户名需为 3-32 位字母、数字或 _ . -")
        minimum = self._int("min_password_length", 8)
        if len(password) < minimum:
            raise AccountError("weak_password", f"密码至少需要 {minimum} 位")
        if self.store.get_account_by_username(username) is not None:
            raise AccountError("username_exists", "该用户名已被使用")
        try:
            return self.store.create_local_account(username, hash_password(password))
        except DuplicateAccount as e:
            raise AccountError("username_exists", "该用户名已被使用") from e

    def login(self, login_name: str, password: str) -> dict[str, Any]:
        account = self.store.get_account_by_login(login_name)
        if account is None or not verify_password(password, account["password_hash"]):
            raise AccountError("bad_credentials", "账号或密码错误")
        if account.get("status") != "active":
            raise AccountError("forbidden", "账号已被禁用")
        return account

    def bind_email(self, login: str, email: str, code: str) -> dict[str, Any]:
        """Verify a code sent to ``email`` and attach it to the logged-in account.

        Used by local accounts so they can receive task reports and sign in with
        an email, without needing a separate email-account registration.
        """
        account = self.store.get_account_by_login(login)
        if account is None:
            raise AccountError("not_found", "账号不存在")
        email = (email or "").strip().lower()
        existing = self.store.get_account_by_email(email)
        if existing is not None and int(existing["id"]) != int(account["id"]):
            raise AccountError("email_exists", "该邮箱已被其它账号使用")
        max_attempts = self._int("max_code_attempts", 5)
        if not self.store.consume_email_code(
            email, "bind_email", code_digest(code), max_attempts
        ):
            raise AccountError("bad_code", "验证码错误或已过期")
        return self.store.update_email(int(account["id"]), email)

    def bind_phone(self, login: str, phone: str) -> dict[str, Any]:
        """Bind a phone number (no verification code) so it can be used to log in."""
        account = self.store.get_account_by_login(login)
        if account is None:
            raise AccountError("not_found", "账号不存在")
        phone = (phone or "").strip()
        if not PHONE_RE.match(phone):
            raise AccountError("bad_phone", "手机号格式不正确")
        existing = self.store.get_account_by_phone(phone)
        if existing is not None and int(existing["id"]) != int(account["id"]):
            raise AccountError("phone_exists", "该手机号已绑定其它账号")
        try:
            return self.store.update_phone(int(account["id"]), phone)
        except DuplicateAccount as e:
            raise AccountError("phone_exists", "该手机号已绑定其它账号") from e

    def reset_password(self, email: str, code: str, new_password: str) -> dict[str, Any]:
        account = self.store.get_account_by_email(email)
        if account is None:
            raise AccountError("email_not_found", "该邮箱未注册")
        max_attempts = self._int("max_code_attempts", 5)
        if not self.store.consume_email_code(
            email, "reset_password", code_digest(code), max_attempts
        ):
            raise AccountError("bad_code", "验证码错误或已过期")
        self.store.update_password(int(account["id"]), hash_password(new_password))
        self.store.revoke_account_tokens(int(account["id"]))
        return account
