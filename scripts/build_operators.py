"""Build the operator knowledge base from PRTS (stats, talents, skills).

For each operator we combine:
  * wikitext infobox -> 职业 / 分支 / 稀有度
  * rendered HTML    -> 部署信息 / 基础属性 / 天赋 / 技能

Usage:
    .venv\\Scripts\\python.exe scripts\\build_operators.py --limit 5
    .venv\\Scripts\\python.exe scripts\\build_operators.py            # 全部干员
"""

from __future__ import annotations

import argparse
import copy
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests
from bs4 import BeautifulSoup
from loguru import logger

from maagent.agent.knowledge import KnowledgeBase

API = "https://prts.wiki/api.php"


def _api(params: dict, retries: int = 4) -> dict:
    last = None
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(API, params=params, timeout=45)
            if resp.status_code == 200:
                return resp.json()
            last = f"HTTP {resp.status_code}"
        except Exception as e:
            last = e
        time.sleep(2.0 * attempt)
    raise RuntimeError(f"API 请求失败: {last}")


def list_operators() -> list[str]:
    titles: list[str] = []
    params = {
        "action": "query", "list": "categorymembers",
        "cmtitle": "Category:干员", "cmlimit": 500, "format": "json",
    }
    while True:
        data = _api(params)
        titles += [m["title"] for m in data.get("query", {}).get("categorymembers", [])]
        cont = data.get("continue")
        if not cont:
            break
        params.update(cont)
        time.sleep(0.5)
    return titles


def _clean(el) -> str:
    el = copy.copy(el)
    for hidden in el.select('[style*="display:none"], .mc-tooltips, .mw-editsection, sup.reference'):
        hidden.decompose()
    return " ".join(el.get_text(" ", strip=True).split())


def _infobox(wikitext: str) -> str:
    fields = {}
    for key in ("职业", "分支", "稀有度", "星级"):
        m = re.search(rf"\|\s*{key}\s*=\s*([^\n|}}]+)", wikitext)
        if m:
            fields[key] = m.group(1).strip()
    parts = []
    if "职业" in fields:
        parts.append(f"职业：{fields['职业']}")
    if "分支" in fields:
        parts.append(f"分支：{fields['分支']}")
    rarity = fields.get("稀有度") or fields.get("星级")
    if rarity and rarity.isdigit():
        parts.append(f"星级：{int(rarity) + 1}星")
    elif rarity:
        parts.append(f"星级：{rarity}")
    return "；".join(parts)


def extract_operator(html: str, wikitext: str = "") -> str:
    soup = BeautifulSoup(html, "html.parser")
    out: list[str] = []
    head = _infobox(wikitext)
    if head:
        out.append(head)

    branch = soup.select_one("table.wikitable.logo")
    if branch:
        out.append("分支/特性：" + _clean(branch)[:300])

    extra = soup.select_one("table.char-extra-attr-table")
    if extra:
        out.append("部署信息：" + _clean(extra))

    base = soup.select_one("table.char-base-attr-table")
    if base:
        out.append("基础属性：" + _clean(base))

    heading = ""
    talents, skills = [], []
    for el in soup.find_all(["h2", "h3", "table"]):
        if el.name in ("h2", "h3"):
            heading = _clean(el)
            continue
        cls = " ".join(el.get("class") or [])
        if "nodesktop" in cls:
            continue
        if "天赋" in heading:
            talents.append(_clean(el))
        elif "技能" in heading and "基建" not in heading:
            skills.append(_clean(el))

    if talents:
        out.append("天赋：\n" + "\n".join(f"- {t}" for t in talents[:4]))
    if skills:
        out.append("技能：\n" + "\n".join(f"- {s}" for s in skills[:4]))
    return "\n".join(out)


def fetch_operator(title: str) -> str:
    html = _api({"action": "parse", "page": title, "prop": "text", "format": "json", "redirects": 1})
    html = html.get("parse", {}).get("text", {}).get("*", "")
    time.sleep(0.3)
    wt = _api({"action": "parse", "page": title, "prop": "wikitext", "format": "json", "redirects": 1})
    wt = wt.get("parse", {}).get("wikitext", {}).get("*", "")
    return extract_operator(html, wt)


def main() -> int:
    parser = argparse.ArgumentParser(description="构建干员知识库")
    parser.add_argument("--out", default="config/agent_knowledge.json")
    parser.add_argument("--limit", type=int, default=0, help="只抓前 N 个（调试用）")
    parser.add_argument("--sleep", type=float, default=1.0)
    parser.add_argument("--refresh", action="store_true", help="重抓已存在的干员")
    args = parser.parse_args()

    kb = KnowledgeBase(args.out)
    existing = set(kb.docs())
    titles = list_operators()
    logger.info("PRTS 干员共 {} 位", len(titles))
    if args.limit:
        titles = titles[: args.limit]

    done = 0
    for title in titles:
        doc = f"干员：{title}"
        if doc in existing and not args.refresh:
            continue
        try:
            text = fetch_operator(title)
        except Exception as e:
            logger.warning("抓取「{}」失败: {}", title, e)
            continue
        if len(text) < 50:
            logger.warning("「{}」内容过短，跳过", title)
            continue
        kb.add_document(doc, text, source=f"https://prts.wiki/w/{title}")
        done += 1
        if done % 10 == 0:
            logger.info("已抓取 {} 位（当前 {} 块）", done, len(kb))
        time.sleep(args.sleep)

    logger.info("干员知识库完成：本次新增 {}，共 {} 篇 / {} 块", done, len(kb.docs()), len(kb))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
