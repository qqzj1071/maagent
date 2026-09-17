from __future__ import annotations

import subprocess
import time
from typing import Any

from loguru import logger


def _run(args: list[str], timeout: int = 30) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(
            args, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
    except Exception as e:
        logger.warning("执行 {} 失败: {}", args, e)
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
