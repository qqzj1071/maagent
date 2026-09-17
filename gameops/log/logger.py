import sys
from pathlib import Path

from loguru import logger


def setup_logger(log_dir: str = "logs", level: str = "INFO") -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass
    logger.remove()
    if sys.stderr is not None:
        logger.add(sys.stderr, level=level, colorize=True)
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    logger.add(
        Path(log_dir) / "gameops_{time:YYYY-MM-DD}.log",
        rotation="00:00",
        retention="14 days",
        encoding="utf-8",
        level="DEBUG",
    )
