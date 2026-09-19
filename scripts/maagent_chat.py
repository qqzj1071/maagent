"""Standalone launcher for the chat agent (used by the packaged 维维美.exe).

Keeps the desktop clean: memory and logs live under %LOCALAPPDATA%\\Maagent when
frozen. Config is read from next to the exe first, then the bundled copy.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def _frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _base_dir() -> Path:
    if _frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def _data_dir() -> Path:
    if _frozen():
        root = os.environ.get("LOCALAPPDATA") or str(Path.home())
        d = Path(root) / "Maagent"
    else:
        d = _base_dir() / "config"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _find_config(base: Path) -> Path | None:
    candidates: list[Path] = []
    env = os.environ.get("MAAGENT_CONFIG")
    if env:
        candidates.append(Path(env))
    candidates.append(base / "config.yaml")
    candidates.append(base / "config" / "config.yaml")
    if _frozen():
        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            candidates.append(Path(meipass) / "config" / "config.yaml")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _enable_utf8_console() -> None:
    if os.name != "nt":
        return
    try:
        import ctypes

        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        ctypes.windll.kernel32.SetConsoleCP(65001)
    except Exception:
        pass
    for stream in (sys.stdout, sys.stderr, sys.stdin):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def main() -> int:
    _enable_utf8_console()
    base = _base_dir()
    os.chdir(base)

    config_path = _find_config(base)
    if config_path is None:
        print("找不到配置文件 config/config.yaml，请把它放在本程序同目录的 config 文件夹下。")
        return 1

    from maagent.config import load_config

    cfg = load_config(config_path)
    agent_cfg = cfg.setdefault("agent", {})
    data = _data_dir()
    if _frozen():
        agent_cfg["memory_file"] = str(data / "agent_memory.json")
        knowledge = data / "agent_knowledge.json"
        bundled = Path(getattr(sys, "_MEIPASS", "")) / "config" / "agent_knowledge.json"
        if not knowledge.exists() and bundled.is_file():
            shutil.copyfile(bundled, knowledge)
        if knowledge.exists():
            agent_cfg["knowledge_file"] = str(knowledge)
        # 截图等运行数据放到用户数据目录，避免散落在 exe 同目录（桌面）
        agent_cfg.setdefault("game", {})["screenshot_dir"] = str(data / "screenshots")

    from loguru import logger

    logger.remove()
    log_dir = data / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logger.add(
        log_dir / "chat_{time:YYYY-MM-DD}.log",
        encoding="utf-8",
        level="INFO",
        rotation="00:00",
        retention="14 days",
    )

    from maagent.agent.cli import run_chat

    return run_chat(cfg)


if __name__ == "__main__":
    raise SystemExit(main())
