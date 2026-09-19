"""Chunked, retrievable knowledge base (e.g. PRTS wiki) for the agent.

Large documents are split into sections/chunks and searched by keyword overlap,
so a small model can pull just the relevant passages instead of the whole text.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

from loguru import logger

_HEADING_RE = re.compile(r"^=+\s*(.+?)\s*=+$")
_TOKEN_SPLIT = re.compile(r"[\s，。、；：？！,.;:?!（）()\[\]【】\"'“”‘’]+")


class KnowledgeBase:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.chunks: list[dict[str, Any]] = []
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.chunks = data.get("chunks") or []
        except Exception as e:
            logger.warning("读取知识库失败: {}", e)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"chunks": self.chunks}, f, ensure_ascii=False, indent=2)

    def __len__(self) -> int:
        return len(self.chunks)

    def _split(self, title: str, text: str, max_len: int = 900) -> list[tuple[str, str]]:
        sections: list[tuple[str, str]] = []
        heading = title
        buf: list[str] = []
        for line in text.splitlines():
            m = _HEADING_RE.match(line.strip())
            if m:
                if buf:
                    sections.append((heading, "\n".join(buf)))
                    buf = []
                heading = m.group(1)
            else:
                buf.append(line)
        if buf:
            sections.append((heading, "\n".join(buf)))

        out: list[tuple[str, str]] = []
        for head, body in sections:
            body = body.strip()
            while len(body) > max_len:
                cut = body.rfind("\n", 0, max_len)
                if cut <= 0:
                    cut = max_len
                out.append((head, body[:cut].strip()))
                body = body[cut:].strip()
            if body:
                out.append((head, body))
        return out

    def add_document(self, title: str, text: str, source: str = "", replace: bool = True) -> int:
        if replace:
            self.chunks = [c for c in self.chunks if c.get("doc") != title]
        added = 0
        for head, body in self._split(title, text):
            self.chunks.append({
                "id": uuid.uuid4().hex[:8],
                "doc": title,
                "title": head,
                "source": source,
                "text": body,
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            })
            added += 1
        self.save()
        return added

    def _tokens(self, text: str) -> list[str]:
        text = text.lower()
        parts = [p for p in _TOKEN_SPLIT.split(text) if p]
        grams: list[str] = []
        for p in parts:
            if len(p) >= 2:
                grams += [p[i : i + 2] for i in range(len(p) - 1)]
        return parts + grams

    def search(self, query: str, limit: int = 4) -> list[dict[str, Any]]:
        tokens = set(self._tokens(query))
        if not tokens:
            return []
        scored: list[tuple[int, dict[str, Any]]] = []
        for chunk in self.chunks:
            text = (chunk.get("title", "") + " " + chunk.get("text", "")).lower()
            score = sum(len(t) for t in tokens if t in text)
            if score:
                scored.append((score, chunk))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [c for _, c in scored[:limit]]

    def docs(self) -> list[str]:
        return sorted({c.get("doc", "") for c in self.chunks})
