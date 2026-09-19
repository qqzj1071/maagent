"""Keyless web search backends (DuckDuckGo HTML / Bing) for the agent.

No API key, no per-call cost. Parsing is regex-based on the public result
pages, so it can break if the providers change their markup — callers should
treat an empty list as "no results / unavailable" and fall back gracefully.
"""

from __future__ import annotations

import html
import re
import urllib.parse
from dataclasses import dataclass

import requests
from loguru import logger

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str = ""
    source: str = ""


def _clean(text: str) -> str:
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    return _WS_RE.sub(" ", text).strip()


def _proxies(proxy: str | None) -> dict[str, str] | None:
    if not proxy:
        return None
    return {"http": proxy, "https": proxy}


def _ddg(query: str, count: int, proxy: str | None, timeout: float) -> list[SearchResult]:
    resp = requests.post(
        "https://html.duckduckgo.com/html/",
        data={"q": query},
        headers={"User-Agent": USER_AGENT},
        proxies=_proxies(proxy),
        timeout=timeout,
    )
    resp.raise_for_status()
    page = resp.text
    results: list[SearchResult] = []
    for m in re.finditer(
        r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', page, re.S
    ):
        url = html.unescape(m.group(1))
        if "uddg=" in url:
            match = re.search(r"uddg=([^&]+)", url)
            if match:
                url = urllib.parse.unquote(match.group(1))
        results.append(SearchResult(_clean(m.group(2)), url, source="duckduckgo"))
    snippets = re.findall(
        r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>', page, re.S
    )
    for result, snippet in zip(results, snippets):
        result.snippet = _clean(snippet)
    return results[:count]


def _bing(query: str, count: int, proxy: str | None, timeout: float) -> list[SearchResult]:
    resp = requests.get(
        "https://www.bing.com/search",
        params={"q": query},
        headers={"User-Agent": USER_AGENT},
        proxies=_proxies(proxy),
        timeout=timeout,
    )
    resp.raise_for_status()
    page = resp.text
    results: list[SearchResult] = []
    for block in re.findall(r'<li class="b_algo"[\s\S]*?</li>', page)[:count]:
        link = re.search(r'<h2[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
        if not link:
            continue
        snippet = re.search(r"<p[^>]*>(.*?)</p>", block, re.S)
        results.append(SearchResult(
            title=_clean(link.group(2)),
            url=html.unescape(link.group(1)),
            snippet=_clean(snippet.group(1)) if snippet else "",
            source="bing",
        ))
    return results


_BACKENDS = {"duckduckgo": _ddg, "bing": _bing}
_AUTO_ORDER = ["bing", "duckduckgo"]


def search(
    query: str,
    count: int = 5,
    backend: str = "auto",
    proxy: str | None = None,
    timeout: float = 15.0,
) -> list[SearchResult]:
    names = _AUTO_ORDER if backend in ("auto", "", None) else [backend]
    for name in names:
        fn = _BACKENDS.get(name)
        if fn is None:
            continue
        try:
            results = fn(query, count, proxy, timeout)
        except Exception as e:
            logger.warning("搜索后端 {} 失败: {}", name, e)
            continue
        if results:
            return results
    return []
