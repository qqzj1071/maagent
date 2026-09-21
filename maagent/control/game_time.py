from __future__ import annotations

from datetime import datetime, timedelta

# 明日方舟 / 明日方舟：终末地 都以每天凌晨 4 点切换「游戏内日期」。
# 因此 00:00–03:59 仍属于前一天（例如周一凌晨 4 点前仍算周日）。
GAME_DAY_START_HOUR = 4


def game_datetime(dt: datetime | None = None) -> datetime:
    """The game's own clock: shift back so 00:00-03:59 counts as the previous day."""
    return (dt or datetime.now()) - timedelta(hours=GAME_DAY_START_HOUR)


def game_day_key(dt: datetime | None = None) -> str:
    """The game day as ``YYYY-MM-DD`` (used for per-day dedup keys)."""
    return game_datetime(dt).strftime("%Y-%m-%d")


def game_weekday(dt: datetime | None = None) -> int:
    """Weekday under the game's 4 AM boundary (周一=0 ... 周日=6)."""
    return game_datetime(dt).weekday()
