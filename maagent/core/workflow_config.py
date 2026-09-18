from __future__ import annotations

from typing import Any
from uuid import uuid4

CHAIN_SOFTWARE = ("maa", "maaend")
ALL_DAYS = list(range(7))


def new_task_id(software: str) -> str:
    return f"{software}-{uuid4().hex[:6]}"


def chain_cfg(config: dict[str, Any]) -> dict[str, Any]:
    return (config.get("workflow", {}) or {}).get("chain", {}) or {}


def normalize_task(task: dict[str, Any], scheduled: bool) -> dict[str, Any]:
    item = dict(task)
    item["id"] = str(item.get("id") or new_task_id(item["software"]))
    item.setdefault("enabled", True)
    if scheduled:
        item.setdefault("time", "08:00")
        item.setdefault("days", list(ALL_DAYS))
    return item


def legacy_split(chain: dict[str, Any]) -> tuple[list[dict], list[dict]]:
    """Split the old shared ``tasks`` list into (sequential, scheduled)."""
    seq: list[dict] = []
    sched: list[dict] = []
    seen: set[str] = set()
    for item in chain.get("tasks") or []:
        if not isinstance(item, dict) or item.get("software") not in CHAIN_SOFTWARE:
            continue
        sched.append(item)
        key = item["software"]
        if key not in seen:
            seen.add(key)
            seq.append(
                {"id": item.get("id"), "software": key, "enabled": item.get("enabled", True)}
            )
    return seq, sched


def config_start_slots(chain: dict[str, Any]) -> list[dict]:
    """Normalize the chain start slots, migrating legacy ``start_time`` forms."""
    raw = chain.get("start_slots")
    if raw is None:
        days = chain.get("days")
        if days is None:
            days = list(ALL_DAYS)
        times = chain.get("start_times")
        if times is None:
            single = chain.get("start_time")
            times = [single] if single else []
        if not isinstance(times, list):
            times = [times]
        raw = [{"time": str(t), "days": list(days)} for t in times]
    if not raw:
        raw = [{"time": "08:00", "days": list(ALL_DAYS)}]
    return raw


def config_sequential_tasks(chain: dict[str, Any]) -> list[dict]:
    raw = chain.get("sequential_tasks")
    if raw is None:
        raw, _ = legacy_split(chain)
    tasks: list[dict] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict) or item.get("software") not in CHAIN_SOFTWARE:
            continue
        key = item["software"]
        if key in seen:
            continue
        seen.add(key)
        tasks.append(normalize_task(item, scheduled=False))
    for key in CHAIN_SOFTWARE:
        if key not in seen:
            tasks.append({"id": new_task_id(key), "software": key, "enabled": True})
    return tasks


def config_scheduled_tasks(chain: dict[str, Any]) -> list[dict]:
    raw = chain.get("scheduled_tasks")
    if raw is None:
        _, raw = legacy_split(chain)
    tasks = [
        normalize_task(item, scheduled=True)
        for item in raw
        if isinstance(item, dict) and item.get("software") in CHAIN_SOFTWARE
    ]
    present = {t["software"] for t in tasks}
    for key in CHAIN_SOFTWARE:
        if key not in present:
            tasks.append(normalize_task({"software": key}, scheduled=True))
    return tasks


def chain_needs_save(chain: dict[str, Any]) -> bool:
    """True when the stored chain lacks the split task lists or stable ids."""
    if "start_slots" not in chain:
        return True
    for key in ("sequential_tasks", "scheduled_tasks"):
        stored = chain.get(key)
        if not stored:
            return True
        if any(not isinstance(t, dict) or not t.get("id") for t in stored):
            return True
    return False
