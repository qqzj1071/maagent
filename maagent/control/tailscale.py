from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
import urllib.request
import webbrowser
from pathlib import Path
from typing import Callable

from loguru import logger

from maagent.server.net import tailscale_exe

DOWNLOAD_URL = "https://pkgs.tailscale.com/stable/tailscale-setup-latest.exe"
INSTALL_TIMEOUT = 900
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# On Windows, `tailscale serve/funnel` talks to the GUI (tailscale-ipn); if it
# is not running they fail with "unexpected state: NoState" even when the
# backend reports Running. So we make sure the GUI is up before serving.
IPN_PATHS = (
    r"C:\Program Files\Tailscale\tailscale-ipn.exe",
    r"C:\Program Files (x86)\Tailscale\tailscale-ipn.exe",
)

Log = Callable[[str], None]


def _ipn_exe() -> str | None:
    for candidate in IPN_PATHS:
        if Path(candidate).is_file():
            return candidate
    return None


def _ipn_running() -> bool:
    try:
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq tailscale-ipn.exe", "/NH"],
            capture_output=True, text=True, timeout=10, creationflags=_NO_WINDOW,
        )
        return "tailscale-ipn.exe" in (result.stdout or "").lower()
    except Exception:
        return False


def ensure_ipn(log: Log) -> None:
    """Start the Tailscale GUI client if it is not running (required by serve/funnel)."""
    if _ipn_running():
        return
    exe = _ipn_exe()
    if not exe:
        return
    log("启动 Tailscale 客户端…")
    try:
        subprocess.Popen([exe], creationflags=_NO_WINDOW)
    except Exception as e:
        log(f"启动 Tailscale 客户端失败：{e}")
        return
    for _ in range(15):
        if _ipn_running():
            break
        time.sleep(1)
    time.sleep(3)



def is_installed() -> bool:
    return tailscale_exe() is not None


def _run(args: list[str], timeout: int = 10) -> subprocess.CompletedProcess | None:
    exe = tailscale_exe()
    if not exe:
        return None
    try:
        return subprocess.run(
            [exe, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=_NO_WINDOW,
        )
    except Exception as e:
        logger.warning("tailscale {} 失败: {}", " ".join(args), e)
        return None


def status_json() -> dict | None:
    result = _run(["status", "--json"], timeout=8)
    if result is None or result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except (ValueError, TypeError):
        return None


def backend_state() -> str:
    data = status_json() or {}
    return str(data.get("BackendState") or "")


def is_logged_in() -> bool:
    return backend_state() == "Running"


def dns_name() -> str | None:
    data = status_json() or {}
    name = str((data.get("Self") or {}).get("DNSName") or "").rstrip(".")
    return name or None


def public_url() -> str | None:
    name = dns_name()
    return f"https://{name}" if name else None


def funnel_enabled() -> bool:
    result = _run(["funnel", "status"], timeout=10)
    if result is None or result.returncode != 0:
        return False
    return "Funnel on" in (result.stdout or "")


def _download(log: Log) -> Path | None:
    target = Path(tempfile.gettempdir()) / "tailscale-setup-latest.exe"
    log(f"下载 Tailscale 安装包：{DOWNLOAD_URL}")
    try:
        with urllib.request.urlopen(DOWNLOAD_URL, timeout=90) as resp, open(target, "wb") as f:
            shutil.copyfileobj(resp, f)
    except Exception as e:
        log(f"下载失败：{e}")
        return None
    return target


def install(log: Log) -> bool:
    """Download and silently install Tailscale (the app already runs elevated)."""
    if is_installed():
        return True
    installer = _download(log)
    if installer is None:
        return False
    log("静默安装 Tailscale…")
    try:
        subprocess.run(
            [str(installer), "/quiet", "/norestart"],
            timeout=INSTALL_TIMEOUT,
            creationflags=_NO_WINDOW,
        )
    except Exception as e:
        log(f"安装失败：{e}")
        return False
    for _ in range(30):
        if is_installed():
            log("Tailscale 安装完成")
            return True
        time.sleep(2)
    log("未检测到 Tailscale，请手动安装后重试")
    return False


def login(log: Log, timeout_seconds: int = 300) -> bool:
    """Open the browser login flow and wait until the backend is running."""
    if is_logged_in():
        return True
    ensure_ipn(log)
    log("正在打开浏览器进行 Tailscale 登录，请在浏览器中完成授权…")
    exe = tailscale_exe()
    if exe:
        try:
            subprocess.Popen(
                [exe, "up"], creationflags=_NO_WINDOW,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            log(f"启动登录失败：{e}")
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if is_logged_in():
            log("Tailscale 已登录")
            return True
        time.sleep(2)
    log("等待 Tailscale 登录超时")
    return False


def _find_enable_url(output: str) -> str | None:
    for line in output.splitlines():
        line = line.strip()
        if "login.tailscale.com" in line or "tailscale.com/f/funnel" in line:
            for token in line.split():
                if token.startswith("http"):
                    return token
    return None


def _wait_running(log: Log, timeout_seconds: int = 30) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if is_logged_in():
            return True
        time.sleep(2)
    return is_logged_in()


def enable_funnel(port: int, log: Log, attempts: int = 6) -> bool:
    """Run ``tailscale funnel --bg <port>`` (proxies 443 -> 127.0.0.1:port).

    The GUI client must be running, and the first run may require approving
    Funnel in the browser; we open the approval URL and retry.
    """
    exe = tailscale_exe()
    if not exe:
        return False
    ensure_ipn(log)
    if not _wait_running(log, 30):
        log("Tailscale 尚未就绪，请稍后重试")
    for _ in range(attempts):
        log(f"启用 Funnel（公网 https://…:443 → 本机 127.0.0.1:{port}）…")
        try:
            result = subprocess.run(
                [exe, "funnel", "--bg", "--yes", str(port)],
                capture_output=True, text=True, timeout=60,
                creationflags=_NO_WINDOW,
            )
        except Exception as e:
            log(f"Funnel 启动失败：{e}")
            return False
        if result.returncode == 0:
            log("Funnel 已启用")
            return True
        output = (result.stdout or "") + (result.stderr or "")
        url = _find_enable_url(output)
        if url:
            log("需要在浏览器中批准启用 Funnel，已为你打开页面…")
            try:
                webbrowser.open(url)
            except Exception:
                log(f"请手动访问：{url}")
            time.sleep(8)
            continue
        if "NoState" in output or "starting" in output.lower():
            log("Tailscale 正在启动，稍后重试…")
            ensure_ipn(log)
            time.sleep(5)
            continue
        log(output.strip() or "Funnel 启用失败")
        return False
    log("Funnel 启用超时，请稍后在设置中重试")
    return False


def setup(port: int, log: Log) -> tuple[bool, str]:
    """Full phone-remote setup. Returns ``(ok, url_or_message)``."""
    log("检测 Tailscale…")
    if not is_installed() and not install(log):
        return False, "Tailscale 安装失败"
    ensure_ipn(log)
    if not is_logged_in() and not login(log):
        return False, "Tailscale 未登录"
    if not enable_funnel(port, log):
        return False, "Funnel 启用失败"
    url = public_url()
    if not url:
        return False, "已启用但未获取到公网地址"
    log(f"手机网页地址：{url}")
    return True, url
