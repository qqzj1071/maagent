from __future__ import annotations

import ssl
import threading
from http.server import ThreadingHTTPServer
from typing import Any

from loguru import logger

from maagent.core.controller import WorkflowController
from maagent.server.api import ApiHandler
from maagent.server.context import ServerContext, build_context
from maagent.server.net import detect_tailscale_ip


class ApiServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], ctx: ServerContext) -> None:
        self.ctx = ctx
        super().__init__(address, ApiHandler)


def create_server(
    config: dict[str, Any],
    host: str | None = None,
    port: int | None = None,
    controller: WorkflowController | None = None,
) -> tuple[ApiServer, ServerContext]:
    ctx = build_context(config, controller=controller)
    settings = ctx.settings
    bind_host = host if host is not None else str(settings.get("host") or "0.0.0.0")
    if bind_host.lower() == "tailscale":
        tailscale_ip = detect_tailscale_ip()
        if tailscale_ip:
            bind_host = tailscale_ip
            logger.info("账号服务仅监听 Tailscale 地址 {}", tailscale_ip)
        else:
            bind_host = "0.0.0.0"
            logger.warning("未检测到 Tailscale 地址，回退监听 0.0.0.0")
    bind_port = int(port if port is not None else settings.get("port") or 8765)
    httpd = ApiServer((bind_host, bind_port), ctx)

    tls = settings.get("tls") or {}
    certfile = str(tls.get("certfile") or "").strip()
    keyfile = str(tls.get("keyfile") or "").strip()
    if certfile and keyfile:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certfile, keyfile)
        httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
        logger.info("账号服务已启用 TLS")
    return httpd, ctx


def _log_urls(httpd: ApiServer, ctx: ServerContext) -> None:
    bind_host, bind_port = httpd.server_address[:2]
    scheme = "https" if (ctx.settings.get("tls") or {}).get("certfile") else "http"
    urls: list[str] = []
    if bind_host in ("0.0.0.0", "::"):
        tailscale_ip = detect_tailscale_ip()
        if tailscale_ip:
            urls.append(f"{scheme}://{tailscale_ip}:{bind_port}")
        urls.append(f"{scheme}://127.0.0.1:{bind_port}")
    else:
        urls.append(f"{scheme}://{bind_host}:{bind_port}")
    logger.info("账号服务已启动: {}", "  ".join(urls))
    logger.info("数据库: {}", ctx.store.path)


def start_server(
    config: dict[str, Any],
    controller: WorkflowController | None = None,
    host: str | None = None,
    port: int | None = None,
) -> tuple[ApiServer, ServerContext, threading.Thread]:
    """Start the API server in a daemon thread (used by the GUI)."""
    httpd, ctx = create_server(config, host, port, controller)
    ctx.controller.attach_log_sink()
    thread = threading.Thread(
        target=httpd.serve_forever, name="maagent-server", daemon=True
    )
    thread.start()
    _log_urls(httpd, ctx)
    return httpd, ctx, thread


def stop_server(httpd: ApiServer, ctx: ServerContext) -> None:
    httpd.shutdown()
    httpd.server_close()
    ctx.controller.detach_log_sink()
    ctx.store.close()


def serve(
    config: dict[str, Any], host: str | None = None, port: int | None = None
) -> int:
    httpd, ctx = create_server(config, host, port)
    ctx.controller.attach_log_sink()
    _log_urls(httpd, ctx)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger.info("账号服务收到中断，正在停止")
    finally:
        if ctx.controller.is_running():
            ctx.controller.stop()
        ctx.controller.detach_log_sink()
        httpd.server_close()
        ctx.store.close()
    return 0
