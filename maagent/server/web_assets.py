from __future__ import annotations

import sys
from pathlib import Path


def _web_dir() -> Path:
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        external = exe_dir / "web"
        if external.is_dir():
            return external
        base = Path(getattr(sys, "_MEIPASS", exe_dir))
        candidate = base / "maagent" / "server" / "web"
        if candidate.is_dir():
            return candidate
    return Path(__file__).resolve().parent / "web"


WEB_DIR = _web_dir()

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".webmanifest": "application/manifest+json; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}

ROUTES = {
    "/": "index.html",
    "/index.html": "index.html",
    "/app.js": "app.js",
    "/styles.css": "styles.css",
    "/manifest.webmanifest": "manifest.webmanifest",
    "/sw.js": "sw.js",
    "/icon.png": "icon.png",
    "/icon-192.png": "icon-192.png",
    "/icon-512.png": "icon-512.png",
}


def read_static(path: str) -> tuple[bytes, str] | None:
    name = ROUTES.get(path)
    if not name:
        return None
    file = WEB_DIR / name
    if not file.is_file():
        return None
    data = file.read_bytes()
    content_type = CONTENT_TYPES.get(file.suffix, "application/octet-stream")
    return data, content_type
