from __future__ import annotations

import json
import queue
import re
import urllib.parse
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler
from typing import Any, Callable

from loguru import logger

from maagent.core.scheduler import Scheduler
from maagent.server.accounts import AccountError, AccountService
from maagent.server.context import ServerContext
from maagent.server.security import (
    hash_password,
    new_token,
    token_digest,
    verify_password,
)
from maagent.server.store import DuplicateAccount
from maagent.server.web_assets import read_static

API_PREFIX = "/api/v1"
MAX_BODY_BYTES = 64 * 1024

ERROR_STATUS = {
    "bad_purpose": 400,
    "bad_code": 400,
    "email_exists": 409,
    "phone_exists": 409,
    "email_not_found": 404,
    "too_frequent": 429,
    "rate_limited": 429,
    "email_unavailable": 503,
    "bad_credentials": 401,
    "forbidden": 403,
}

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_RE = re.compile(r"^\+?\d{6,15}$")


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


@dataclass
class Request:
    method: str
    path: str
    query: dict[str, list[str]]
    body: dict[str, Any]
    headers: Any
    token: str | None = None
    account: dict[str, Any] | None = None
    extra: dict[str, Any] = field(default_factory=dict)


Handler = Callable[[ServerContext, Request], tuple[int, dict[str, Any]]]
ROUTES: dict[tuple[str, str], Handler] = {}


def route(method: str, path: str) -> Callable[[Handler], Handler]:
    def decorator(func: Handler) -> Handler:
        ROUTES[(method, path)] = func
        return func

    return decorator


# --------------------------------------------------------------------------- #
# validation helpers
# --------------------------------------------------------------------------- #
def require_email(value: Any) -> str:
    email = str(value or "").strip().lower()
    if not EMAIL_RE.match(email) or len(email) > 254:
        raise ApiError(400, "bad_email", "邮箱格式不正确")
    return email


def normalize_phone(value: Any) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    phone = re.sub(r"[\s\-]", "", raw)
    if not PHONE_RE.match(phone):
        raise ApiError(400, "bad_phone", "手机号格式不正确")
    return phone


def require_password(ctx: ServerContext, value: Any) -> str:
    password = str(value or "")
    minimum = int(ctx.settings.get("min_password_length", 8))
    if len(password) < minimum:
        raise ApiError(400, "weak_password", f"密码至少需要 {minimum} 位")
    if len(password) > 128:
        raise ApiError(400, "bad_password", "密码过长")
    return password


def require_code(value: Any) -> str:
    code = str(value or "").strip()
    if not code or len(code) > 12:
        raise ApiError(400, "bad_code", "验证码不能为空")
    return code


def public_account(account: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": account["id"],
        "email": account["email"],
        "phone": account.get("phone"),
        "email_verified": bool(account.get("email_verified")),
        "phone_verified": bool(account.get("phone_verified")),
        "status": account.get("status"),
        "created_at": account.get("created_at"),
        "last_login_at": account.get("last_login_at"),
    }


# --------------------------------------------------------------------------- #
# auth helpers
# --------------------------------------------------------------------------- #
def issue_token(ctx: ServerContext, account_id: int) -> tuple[str, str]:
    token = new_token()
    ttl = int(ctx.settings.get("token_ttl_hours", 720))
    record = ctx.store.create_token(account_id, token_digest(token), ttl)
    return token, record["expires_at"]


def authenticate(ctx: ServerContext, req: Request) -> dict[str, Any]:
    header = str(req.headers.get("Authorization") or "")
    if not header.lower().startswith("bearer "):
        raise ApiError(401, "unauthorized", "缺少访问令牌")
    token = header[7:].strip()
    record = ctx.store.find_token(token_digest(token))
    if record is None:
        raise ApiError(401, "invalid_token", "令牌无效或已过期")
    account = ctx.store.get_account_by_id(int(record["account_id"]))
    if account is None or account.get("status") != "active":
        raise ApiError(403, "forbidden", "账号不可用")
    ctx.store.touch_token(token_digest(token))
    req.token = token
    req.account = account
    return account


# --------------------------------------------------------------------------- #
# endpoints
# --------------------------------------------------------------------------- #
@route("GET", f"{API_PREFIX}/health")
def health(ctx: ServerContext, req: Request) -> tuple[int, dict[str, Any]]:
    return 200, {
        "ok": True,
        "service": "maagent-account",
        "allow_register": bool(ctx.settings.get("allow_register", True)),
    }


def _account_service(ctx: ServerContext) -> AccountService:
    return AccountService(ctx.store, ctx.mailer, ctx.settings)


def _raise_account(error: AccountError) -> None:
    raise ApiError(ERROR_STATUS.get(error.code, 400), error.code, error.message) from error


@route("POST", f"{API_PREFIX}/auth/email/code")
def send_email_code(ctx: ServerContext, req: Request) -> tuple[int, dict[str, Any]]:
    email = require_email(req.body.get("email"))
    purpose = str(req.body.get("purpose") or "register").strip()
    try:
        result = _account_service(ctx).send_code(
            email, purpose, enforce_limits=True, debug=ctx.debug
        )
    except AccountError as e:
        _raise_account(e)
    payload: dict[str, Any] = {"ok": True, "expires_in": result["ttl"] * 60}
    if not result["sent"] and ctx.debug:
        payload["debug_code"] = result["code"]
    return 200, payload


@route("POST", f"{API_PREFIX}/auth/register")
def register(ctx: ServerContext, req: Request) -> tuple[int, dict[str, Any]]:
    if not ctx.settings.get("allow_register", True):
        raise ApiError(403, "register_disabled", "当前未开放注册")
    email = require_email(req.body.get("email"))
    phone = normalize_phone(req.body.get("phone"))
    password = require_password(ctx, req.body.get("password"))
    code = require_code(req.body.get("code"))
    try:
        account = _account_service(ctx).register(email, phone, password, code)
    except AccountError as e:
        _raise_account(e)
    token, expires_at = issue_token(ctx, int(account["id"]))
    logger.info("新账号注册: {} ({})", account["email"], account["phone"])
    return 201, {
        "account": public_account(account),
        "token": token,
        "expires_at": expires_at,
    }


@route("POST", f"{API_PREFIX}/auth/login")
def login(ctx: ServerContext, req: Request) -> tuple[int, dict[str, Any]]:
    login_name = str(req.body.get("account") or req.body.get("email") or "").strip()
    password = str(req.body.get("password") or "")
    if not login_name or not password:
        raise ApiError(400, "bad_request", "请输入账号与密码")
    try:
        account = _account_service(ctx).login(
            login_name.lower() if "@" in login_name else login_name, password
        )
    except AccountError as e:
        _raise_account(e)

    ctx.store.set_last_login(int(account["id"]))
    token, expires_at = issue_token(ctx, int(account["id"]))
    return 200, {
        "account": public_account(account),
        "token": token,
        "expires_at": expires_at,
    }


@route("POST", f"{API_PREFIX}/auth/logout")
def logout(ctx: ServerContext, req: Request) -> tuple[int, dict[str, Any]]:
    authenticate(ctx, req)
    assert req.token is not None
    ctx.store.revoke_token(token_digest(req.token))
    return 200, {"ok": True}


@route("GET", f"{API_PREFIX}/account/me")
def account_me(ctx: ServerContext, req: Request) -> tuple[int, dict[str, Any]]:
    account = authenticate(ctx, req)
    return 200, {"account": public_account(account)}


@route("POST", f"{API_PREFIX}/account/bind_phone")
def bind_phone(ctx: ServerContext, req: Request) -> tuple[int, dict[str, Any]]:
    account = authenticate(ctx, req)
    password = str(req.body.get("password") or "")
    if not verify_password(password, account["password_hash"]):
        raise ApiError(401, "bad_credentials", "密码错误")
    phone = normalize_phone(req.body.get("phone"))
    if phone and ctx.store.get_account_by_phone(phone) is not None:
        raise ApiError(409, "phone_exists", "该手机号已绑定其它账号")
    try:
        updated = ctx.store.update_phone(int(account["id"]), phone)
    except DuplicateAccount as e:
        raise ApiError(409, "phone_exists", "该手机号已绑定其它账号") from e
    assert updated is not None
    return 200, {"account": public_account(updated)}


@route("POST", f"{API_PREFIX}/account/password")
def change_password(ctx: ServerContext, req: Request) -> tuple[int, dict[str, Any]]:
    account = authenticate(ctx, req)
    old_password = str(req.body.get("old_password") or "")
    if not verify_password(old_password, account["password_hash"]):
        raise ApiError(401, "bad_credentials", "原密码错误")
    new_password = require_password(ctx, req.body.get("new_password"))
    ctx.store.update_password(int(account["id"]), hash_password(new_password))
    ctx.store.revoke_account_tokens(int(account["id"]))
    token, expires_at = issue_token(ctx, int(account["id"]))
    return 200, {"ok": True, "token": token, "expires_at": expires_at}


@route("POST", f"{API_PREFIX}/auth/password/reset")
def reset_password(ctx: ServerContext, req: Request) -> tuple[int, dict[str, Any]]:
    email = require_email(req.body.get("email"))
    code = require_code(req.body.get("code"))
    new_password = require_password(ctx, req.body.get("new_password"))
    try:
        _account_service(ctx).reset_password(email, code, new_password)
    except AccountError as e:
        _raise_account(e)
    logger.info("账号重置密码: {}", email)
    return 200, {"ok": True}


# --------------------------------------------------------------------------- #
# remote control (workflow / logs / reports)
# --------------------------------------------------------------------------- #
def _scheduler(ctx: ServerContext) -> Scheduler:
    chain = (ctx.controller.config.get("workflow", {}) or {}).get("chain", {}) or {}
    return Scheduler(ctx.controller.config, chain.get("state_file", "logs/schedule_state.json"))


def _next_run_text(ctx: ServerContext) -> str | None:
    try:
        nxt = _scheduler(ctx).next_run()
    except Exception:
        return None
    return nxt.strftime("%Y-%m-%d %H:%M") if nxt else None


def _int_query(req: Request, name: str, default: int, minimum: int, maximum: int) -> int:
    raw = (req.query.get(name) or [None])[0]
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, value))


def _control_payload(ctx: ServerContext) -> dict[str, Any]:
    snapshot = ctx.controller.snapshot()
    snapshot["next_run"] = _next_run_text(ctx)
    snapshot["software"] = ctx.controller.available_software()
    return snapshot


@route("GET", f"{API_PREFIX}/status")
def status(ctx: ServerContext, req: Request) -> tuple[int, dict[str, Any]]:
    authenticate(ctx, req)
    return 200, {"status": _control_payload(ctx)}


@route("POST", f"{API_PREFIX}/workflow/start")
def workflow_start(ctx: ServerContext, req: Request) -> tuple[int, dict[str, Any]]:
    authenticate(ctx, req)
    software = req.body.get("software")
    if software is not None and not isinstance(software, list):
        raise ApiError(400, "bad_request", "software 必须是数组")
    try:
        ctx.controller.start([str(s) for s in software] if software else None)
    except RuntimeError as e:
        raise ApiError(409, "busy", str(e)) from e
    return 200, {"ok": True, "status": _control_payload(ctx)}


@route("POST", f"{API_PREFIX}/workflow/stop")
def workflow_stop(ctx: ServerContext, req: Request) -> tuple[int, dict[str, Any]]:
    authenticate(ctx, req)
    ctx.controller.stop()
    return 200, {"ok": True, "status": _control_payload(ctx)}


@route("GET", f"{API_PREFIX}/logs")
def get_logs(ctx: ServerContext, req: Request) -> tuple[int, dict[str, Any]]:
    authenticate(ctx, req)
    limit = _int_query(req, "limit", 200, 1, 500)
    return 200, {"lines": ctx.controller.logs(limit)}


@route("GET", f"{API_PREFIX}/reports")
def get_reports(ctx: ServerContext, req: Request) -> tuple[int, dict[str, Any]]:
    authenticate(ctx, req)
    limit = _int_query(req, "limit", 20, 1, 100)
    return 200, {"reports": ctx.controller.reports(limit)}


@route("GET", f"{API_PREFIX}/reports/latest")
def get_latest_report(ctx: ServerContext, req: Request) -> tuple[int, dict[str, Any]]:
    authenticate(ctx, req)
    return 200, {"report": ctx.controller.latest_report()}


@route("POST", f"{API_PREFIX}/reports/clear")
def clear_reports(ctx: ServerContext, req: Request) -> tuple[int, dict[str, Any]]:
    authenticate(ctx, req)
    ctx.controller.clear_reports()
    return 200, {"ok": True}


@route("GET", f"{API_PREFIX}/reports/detail")
def get_report_detail(ctx: ServerContext, req: Request) -> tuple[int, dict[str, Any]]:
    authenticate(ctx, req)
    report_id = str((req.query.get("id") or [""])[0]).strip()
    if not report_id:
        raise ApiError(400, "bad_request", "缺少报告 id")
    report = ctx.controller.get_report(report_id)
    if report is None:
        raise ApiError(404, "report_not_found", "报告不存在")
    return 200, {"report": report}


@route("GET", f"{API_PREFIX}/config")
def get_runtime_config(ctx: ServerContext, req: Request) -> tuple[int, dict[str, Any]]:
    authenticate(ctx, req)
    config = ctx.controller.config
    workflow = config.get("workflow", {}) or {}
    chain = workflow.get("chain", {}) or {}
    return 200, {
        "game": workflow.get("game", ""),
        "auto_close": bool(workflow.get("auto_close")),
        "chain_enabled": bool(chain.get("enabled", False)),
        "chain_mode": chain.get("mode", "sequential"),
        "software": ctx.controller.available_software(),
        "next_run": _next_run_text(ctx),
    }


# --------------------------------------------------------------------------- #
# HTTP handler
# --------------------------------------------------------------------------- #
class ApiHandler(BaseHTTPRequestHandler):
    server_version = "Maagent/0.1"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        logger.debug("HTTP {} {}", self.address_string(), fmt % args)

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch("DELETE")

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > MAX_BODY_BYTES:
            raise ApiError(413, "body_too_large", "请求体过大")
        raw = self.rfile.read(length)
        if not raw.strip():
            return {}
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            raise ApiError(400, "bad_json", "请求体不是合法 JSON") from e
        if not isinstance(parsed, dict):
            raise ApiError(400, "bad_json", "请求体必须是 JSON 对象")
        return parsed

    def _dispatch(self, method: str) -> None:
        ctx: ServerContext = self.server.ctx  # type: ignore[attr-defined]
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        if method == "GET" and path == f"{API_PREFIX}/events":
            self._stream_events(ctx, urllib.parse.parse_qs(parsed.query))
            return
        if method == "GET":
            static = read_static(path)
            if static is not None:
                data, content_type = static
                self._send_bytes(200, data, content_type)
                return
        try:
            req = Request(
                method=method,
                path=path,
                query=urllib.parse.parse_qs(parsed.query),
                body=self._read_body(),
                headers=self.headers,
            )
            handler = ROUTES.get((method, path))
            if handler is None:
                raise ApiError(404, "not_found", "接口不存在")
            status, payload = handler(ctx, req)
        except ApiError as e:
            self._send_json(e.status, {"ok": False, "error": e.code, "message": e.message})
            return
        except Exception as e:  # pragma: no cover - unexpected
            logger.exception("处理请求出错: {} {}", method, path)
            self._send_json(
                500, {"ok": False, "error": "internal_error", "message": f"服务器内部错误: {e}"}
            )
            return
        self._send_json(status, payload)

    # -- server-sent events --------------------------------------------- #
    def _stream_events(self, ctx: ServerContext, query: dict[str, list[str]]) -> None:
        header = str(self.headers.get("Authorization") or "")
        token = header[7:].strip() if header.lower().startswith("bearer ") else ""
        if not token:
            token = str((query.get("token") or [""])[0]).strip()
        record = ctx.store.find_token(token_digest(token)) if token else None
        if record is None:
            self._send_json(
                401, {"ok": False, "error": "invalid_token", "message": "令牌无效或已过期"}
            )
            return
        account = ctx.store.get_account_by_id(int(record["account_id"]))
        if account is None or account.get("status") != "active":
            self._send_json(403, {"ok": False, "error": "forbidden", "message": "账号不可用"})
            return

        events: queue.Queue[dict[str, Any]] = queue.Queue()

        def on_event(event: dict[str, Any]) -> None:
            events.put(event)

        ctx.controller.subscribe(on_event)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self._cors()
        self.end_headers()
        try:
            self._sse_send({"type": "snapshot", "data": _control_payload(ctx)})
            while True:
                try:
                    event = events.get(timeout=20)
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    continue
                self._sse_send(event)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            ctx.controller.unsubscribe(on_event)

    def _sse_send(self, event: dict[str, Any]) -> None:
        payload = json.dumps(event, ensure_ascii=False)
        self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
        self.wfile.flush()
