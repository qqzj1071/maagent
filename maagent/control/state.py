from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from loguru import logger


class JsonState:
    """A tiny JSON-backed store that resets whenever its period key changes."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        try:
            self.data: dict[str, Any] = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            self.data = {}

    def _period_bucket(self, period: str, defaults: dict[str, Any]) -> dict[str, Any]:
        if self.data.get("period") != period:
            self.data = {"period": period, **defaults}
        return self.data

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:
            logger.warning("保存状态失败 {}: {}", self.path, e)
