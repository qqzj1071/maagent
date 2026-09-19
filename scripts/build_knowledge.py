"""Fetch PRTS wiki pages via the MediaWiki API and build the agent knowledge base.

Usage:
    .venv\\Scripts\\python.exe scripts\\build_knowledge.py
    .venv\\Scripts\\python.exe scripts\\build_knowledge.py --pages 新人入门 理智 基建
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests
from loguru import logger

from maagent.agent.knowledge import KnowledgeBase

API = "https://prts.wiki/api.php"

DEFAULT_PAGES = [
    "新人入门",
    "理智",
    "基建",
    "作战机制",
    "公开招募",
    "剿灭作战",
    "寻访",
    "职业",
]


def fetch_page(title: str, timeout: float = 30.0) -> tuple[str, str, str]:
    params = {
        "action": "query",
        "prop": "extracts|info",
        "explaintext": 1,
        "titles": title,
        "format": "json",
        "redirects": 1,
        "inprop": "url",
    }
    # PRTS 会拦截浏览器 UA，保持 requests 默认 UA 即可
    resp = requests.get(API, params=params, timeout=timeout)
    resp.raise_for_status()
    pages = resp.json().get("query", {}).get("pages", {})
    for page in pages.values():
        return page.get("title", title), page.get("extract", ""), page.get("fullurl", "")
    return title, "", ""


def main() -> int:
    parser = argparse.ArgumentParser(description="构建 PRTS 知识库")
    parser.add_argument("--pages", nargs="*", default=None, help="要抓取的页面标题")
    parser.add_argument("--out", default="config/agent_knowledge.json")
    args = parser.parse_args()

    pages = args.pages or DEFAULT_PAGES
    kb = KnowledgeBase(args.out)
    total = 0
    for title in pages:
        try:
            real, text, url = fetch_page(title)
        except Exception as e:
            logger.warning("抓取「{}」失败: {}", title, e)
            continue
        if not text.strip():
            logger.warning("「{}」无内容（页面不存在？）", title)
            continue
        added = kb.add_document(real, text, source=url)
        total += added
        logger.info("已收录《{}》{} 字 → {} 块", real, len(text), added)
        time.sleep(1.0)

    logger.info("知识库完成：{} 篇 / {} 块，保存于 {}", len(kb.docs()), len(kb), args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
