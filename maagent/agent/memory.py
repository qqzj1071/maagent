from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from loguru import logger


class AgentMemory:
    """Persistent, teachable memory: user profile, facts and learned skills."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.data: dict[str, Any] = {"profile": {}, "facts": [], "skills": []}
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                self.data["profile"] = loaded.get("profile") or {}
                self.data["facts"] = loaded.get("facts") or []
                self.data["skills"] = loaded.get("skills") or []
        except Exception as e:
            logger.warning("读取 agent 记忆失败: {}", e)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    def add_fact(self, content: str, category: str = "general", source: str = "user") -> dict[str, Any]:
        item = {
            "id": uuid.uuid4().hex[:8],
            "category": category,
            "content": content.strip(),
            "source": source,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        self.data["facts"].append(item)
        self.save()
        return item

    def add_skill(self, name: str, description: str, steps: list[str] | None = None) -> dict[str, Any]:
        item = {
            "id": uuid.uuid4().hex[:8],
            "name": name.strip(),
            "description": description.strip(),
            "steps": steps or [],
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        self.data["skills"].append(item)
        self.save()
        return item

    def remove(self, mem_id: str) -> bool:
        for key in ("facts", "skills"):
            before = len(self.data[key])
            self.data[key] = [i for i in self.data[key] if i.get("id") != mem_id]
            if len(self.data[key]) != before:
                self.save()
                return True
        return False

    def _tokens(self, text: str) -> list[str]:
        text = text.lower()
        words = [w for w in text.replace("，", " ").replace("。", " ").split() if w]
        grams = [text[i : i + 2] for i in range(len(text) - 1)]
        return words + grams

    def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        tokens = self._tokens(query)
        if not tokens:
            return []
        scored: list[tuple[int, dict[str, Any]]] = []
        for item in self.data["facts"] + self.data["skills"]:
            text = json.dumps(item, ensure_ascii=False).lower()
            score = sum(len(t) for t in set(tokens) if t in text)
            if score:
                scored.append((score, item))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in scored[:limit]]

    def all_items(self) -> list[dict[str, Any]]:
        return list(self.data["facts"]) + list(self.data["skills"])

    def digest(self, limit: int = 15) -> str:
        facts = self.data["facts"][-limit:]
        skills = self.data["skills"][-limit:]
        lines: list[str] = []
        if facts:
            lines.append("你记得的事实/偏好：")
            lines += [f"- [{f.get('id')}] ({f.get('category')}) {f.get('content')}" for f in facts]
        if skills:
            lines.append("你学会的技能：")
            lines += [f"- [{s.get('id')}] {s.get('name')}：{s.get('description')}" for s in skills]
        return "\n".join(lines)

    def pretty(self) -> str:
        return json.dumps(self.data, ensure_ascii=False, indent=2)
