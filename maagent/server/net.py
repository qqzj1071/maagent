from __future__ import annotations

import ipaddress
import json
import shutil
import subprocess
from pathlib import Path

TAILSCALE_CGNAT = ipaddress.ip_network("100.64.0.0/10")

TAILSCALE_PATHS = (
    r"C:\Program Files\Tailscale\tailscale.exe",
    r"C:\Program Files (x86)\Tailscale\tailscale.exe",
)


def tailscale_exe() -> str | None:
    exe = shutil.which("tailscale")
    if exe:
        return exe
    for candidate in TAILSCALE_PATHS:
        if Path(candidate).is_file():
            return candidate
    return None


def _from_cli() -> str | None:
    exe = tailscale_exe()
    if not exe:
        return None
    try:
        result = subprocess.run(
            [exe, "ip", "-4"], capture_output=True, text=True, timeout=5
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        ip = line.strip()
        if ip:
            return ip
    return None


def _from_interfaces() -> str | None:
    try:
        import psutil
    except Exception:
        return None
    try:
        interfaces = psutil.net_if_addrs()
    except Exception:
        return None

    def ipv4s(items):
        for item in items:
            address = getattr(item, "address", "")
            try:
                addr = ipaddress.ip_address(address)
            except ValueError:
                continue
            if addr.version == 4:
                yield str(addr)

    for name, items in interfaces.items():
        if "tailscale" in name.lower():
            found = next(ipv4s(items), None)
            if found:
                return found
    for _, items in interfaces.items():
        for ip in ipv4s(items):
            if ipaddress.ip_address(ip) in TAILSCALE_CGNAT:
                return ip
    return None


def detect_tailscale_ip() -> str | None:
    """Return this machine's Tailscale IPv4 address, if any."""
    return _from_cli() or _from_interfaces()


def detect_public_url() -> str | None:
    """Return the machine's public HTTPS URL (Tailscale Funnel DNS name)."""
    exe = tailscale_exe()
    if not exe:
        return None
    try:
        result = subprocess.run(
            [exe, "status", "--json"], capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout)
    except Exception:
        return None
    dns_name = str((data.get("Self") or {}).get("DNSName") or "").rstrip(".")
    return f"https://{dns_name}" if dns_name else None
