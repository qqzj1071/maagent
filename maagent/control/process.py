from __future__ import annotations

import ctypes
import subprocess
import time
from pathlib import Path
from typing import Any

import psutil
from loguru import logger

SW_SHOWNORMAL = 1
ERROR_ELEVATION_REQUIRED = 740


def _run(args: list[str], timeout: int = 30) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(
            args, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
    except Exception as e:
        logger.warning("执行 {} 失败: {}", args, e)
        return None


def process_running(name: str) -> bool:
    target = name.lower()
    for proc in psutil.process_iter(["name"]):
        try:
            if (proc.info.get("name") or "").lower() == target:
                return True
        except Exception:
            continue
    return False


def is_elevated() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def shell_execute_runas(program: str, args: str = "", cwd: str | None = None) -> bool:
    """Start a program elevated (triggers a UAC prompt)."""
    try:
        res = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", program, args or None, cwd, SW_SHOWNORMAL
        )
        return int(res) > 32
    except Exception as e:
        logger.warning("提权启动失败 {}: {}", program, e)
        return False


def spawn(program: str, args: str = "", cwd: str | None = None,
          prefer_elevated: bool = False) -> str | None:
    """Launch a program, escalating via UAC only when really needed.

    Returns "normal" / "runas" on success, None on failure.
    """
    if prefer_elevated and not is_elevated():
        return "runas" if shell_execute_runas(program, args, cwd) else None
    try:
        cmd = [program] + (args.split() if args else [])
        subprocess.Popen(cmd, cwd=cwd)
        return "normal"
    except OSError as e:
        if getattr(e, "winerror", None) == ERROR_ELEVATION_REQUIRED:
            logger.info("{} 需要管理员权限，请求提权...", Path(program).name)
            return "runas" if shell_execute_runas(program, args, cwd) else None
        logger.warning("启动 {} 失败: {}", program, e)
        return None


def close_maa(process_name: str = "MAA.exe") -> bool:
    r = _run(["taskkill", "/IM", process_name, "/F"])
    if r and r.returncode == 0:
        logger.info("已关闭 {}", process_name)
        return True
    logger.info("{} 未在运行", process_name)
    return False


def close_emulator(manager_path: str, vmindex: int = 0) -> bool:
    r = _run([manager_path, "control", "-v", str(vmindex), "shutdown"], timeout=60)
    if r and r.returncode == 0:
        logger.info("已关闭模拟器 (vmindex={})", vmindex)
        return True
    logger.warning("关闭模拟器失败: {}", (r.stdout or r.stderr) if r else "")
    return False


def close_all(emulator_cfg: dict[str, Any], process_name: str = "MAA.exe") -> None:
    close_maa(process_name)
    if emulator_cfg.get("manager_path"):
        close_emulator(emulator_cfg["manager_path"], emulator_cfg.get("vmindex", 0))
    time.sleep(2)
