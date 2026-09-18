from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from loguru import logger

ALL_DAYS = [0, 1, 2, 3, 4, 5, 6]

MODE_SEQUENTIAL = "sequential"
MODE_SCHEDULED = "scheduled"


def parse_hhmm(value: Any) -> int | None:
    """Parse "HH:MM" (or an int number of minutes) into minutes since midnight."""
    if value is None or value == "":
        return None
    if isinstance(value, int):
        return value if 0 <= value < 1440 else None
    text = str(value).strip()
    if not text:
        return None
    if ":" not in text:
        try:
            minutes = int(text)
        except ValueError:
            return None
        return minutes if 0 <= minutes < 1440 else None
    try:
        hh, mm = text.split(":", 1)
        h, m = int(hh), int(mm)
    except ValueError:
        return None
    if not (0 <= h < 24 and 0 <= m < 60):
        return None
    return h * 60 + m


def format_hhmm(minutes: int | None) -> str:
    if minutes is None:
        return "--:--"
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


@dataclass
class ScheduleEvent:
    """A due workflow trigger produced by :meth:`Scheduler.poll`."""

    kind: str  # "chain" | "task"
    software: str | None
    software_list: list[str]
    scheduled_at: datetime
    label: str = ""
    chain_time: str = ""  # "HH:MM" of the chain slot that fired
    task_id: str = ""  # unique task entry id (scheduled mode)


class Scheduler:
    """Time-of-day scheduler for the daily workflow chain.

    Pure logic (no Qt): the GUI polls :meth:`poll` and calls :meth:`mark` once a
    run actually starts. Fired slots are persisted so they fire at most once per
    day. A grace window lets a missed minute still run (e.g. the app was busy or
    just launched).

    - ``sequential`` mode has one or more ``start_times``; each fires the whole
      enabled chain in order, deduped per time slot.
    - ``scheduled`` mode fires each task entry independently, keyed by its ``id``
      so duplicated entries can run at different times.
    """

    def __init__(
        self, config: dict[str, Any], state_file: str | Path = "logs/schedule_state.json"
    ) -> None:
        self.config = config
        self.state_file = Path(state_file)
        self.state: dict[str, Any] = self._load()

    # -- config helpers ------------------------------------------------- #
    @property
    def chain(self) -> dict[str, Any]:
        return (self.config.get("workflow", {}) or {}).get("chain", {}) or {}

    def mode(self) -> str:
        return self.chain.get("mode", MODE_SEQUENTIAL)

    def _task_lists(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Return (sequential_tasks, scheduled_tasks), migrating the old shared list."""
        chain = self.chain
        sequential = chain.get("sequential_tasks")
        scheduled = chain.get("scheduled_tasks")
        if sequential is None or scheduled is None:
            seq: list[dict[str, Any]] = []
            sched: list[dict[str, Any]] = []
            seen: set[str] = set()
            for item in chain.get("tasks") or []:
                if not isinstance(item, dict) or not item.get("software"):
                    continue
                sched.append(item)
                key = item["software"]
                if key not in seen:
                    seen.add(key)
                    seq.append(
                        {"id": item.get("id"), "software": key, "enabled": item.get("enabled", True)}
                    )
            if sequential is None:
                sequential = seq
            if scheduled is None:
                scheduled = sched
        return sequential or [], scheduled or []

    def tasks(self) -> list[dict[str, Any]]:
        sequential, scheduled = self._task_lists()
        source = scheduled if self.mode() == MODE_SCHEDULED else sequential
        return [t for t in source if isinstance(t, dict) and t.get("software")]

    def enabled_tasks(self) -> list[dict[str, Any]]:
        return [t for t in self.tasks() if t.get("enabled", True)]

    def is_enabled(self) -> bool:
        return bool(self.chain.get("enabled", False))

    def start_slots(self) -> list[tuple[int, list[int]]]:
        """Chain start slots as (minutes-since-midnight, run-days) pairs.

        Reads the current ``start_slots`` list; falls back to the legacy single
        ``start_time`` / ``start_times`` list sharing the chain-level ``days``.
        """
        chain = self.chain
        raw = chain.get("start_slots")
        if raw is None:
            global_days = self._days(chain.get("days"))
            times = chain.get("start_times")
            if times is None:
                single = chain.get("start_time")
                times = [single] if single else []
            if not isinstance(times, (list, tuple)):
                times = [times]
            slots = []
            for item in times:
                minutes = parse_hhmm(item)
                if minutes is not None:
                    slots.append((minutes, global_days))
            return slots
        if not isinstance(raw, (list, tuple)):
            raw = [raw]
        slots = []
        for item in raw:
            if isinstance(item, dict):
                minutes = parse_hhmm(item.get("time"))
                days = self._days(item.get("days"))
            else:
                minutes = parse_hhmm(item)
                days = self._days(chain.get("days"))
            if minutes is not None:
                slots.append((minutes, days))
        return slots

    # -- state ---------------------------------------------------------- #
    def _load(self) -> dict[str, Any]:
        try:
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save(self) -> None:
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            self.state_file.write_text(
                json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:
            logger.warning("保存定时状态失败 {}: {}", self.state_file, e)

    def _chain_fired(self, today: str) -> set[str]:
        data = self.state.get("chain") or {}
        if not isinstance(data, dict) or data.get("date") != today:
            return set()
        return set(data.get("times") or [])

    def _mark_chain(self, hhmm: str, today: str) -> None:
        fired = self._chain_fired(today) | {hhmm}
        self.state["chain"] = {"date": today, "times": sorted(fired)}

    def _task_ran_today(self, task_id: str, today: str) -> bool:
        return (self.state.get("tasks", {}) or {}).get(task_id) == today

    def _mark_task(self, task_id: str, today: str) -> None:
        data = {
            key: value
            for key, value in (self.state.get("tasks") or {}).items()
            if value == today
        }
        data[task_id] = today
        self.state["tasks"] = data

    @staticmethod
    def task_id(task: dict[str, Any], index: int = 0) -> str:
        value = task.get("id")
        if value:
            return str(value)
        return f"{task.get('software', 'task')}#{index}"

    # -- scheduling ----------------------------------------------------- #
    @staticmethod
    def _within_window(now: datetime, start: int, grace: int) -> bool:
        now_min = now.hour * 60 + now.minute
        return 0 <= now_min - start <= grace

    @staticmethod
    def _days(value: Any) -> list[int]:
        """None means every day; an explicit empty list means never."""
        if value is None:
            return ALL_DAYS
        try:
            return [int(d) for d in value]
        except (TypeError, ValueError):
            return ALL_DAYS

    def poll(self, now: datetime | None = None) -> list[ScheduleEvent]:
        now = now or datetime.now()
        if not self.is_enabled():
            return []
        chain = self.chain
        grace = int(chain.get("grace_minutes", 30) or 0)
        today = now.strftime("%Y-%m-%d")
        weekday = now.weekday()
        events: list[ScheduleEvent] = []

        if self.mode() == MODE_SEQUENTIAL:
            software: list[str] = []
            for task in self.enabled_tasks():
                if task["software"] not in software:
                    software.append(task["software"])
            if not software:
                return []
            fired = self._chain_fired(today)
            for start, days in self.start_slots():
                if weekday not in days:
                    continue
                hhmm = format_hhmm(start)
                if hhmm in fired or not self._within_window(now, start, grace):
                    continue
                events.append(
                    ScheduleEvent(
                        "chain", None, list(software), now, f"整链 {hhmm}", chain_time=hhmm
                    )
                )
            return events

        enabled = self.enabled_tasks()
        for index, task in enumerate(enabled):
            task_id = self.task_id(task, index)
            if task.get("trigger") == "after_previous":
                if index == 0 or self._task_ran_today(task_id, today):
                    continue
                prev_id = self.task_id(enabled[index - 1], index - 1)
                if not self._task_ran_today(prev_id, today):
                    continue
                events.append(
                    ScheduleEvent(
                        "task",
                        task["software"],
                        [task["software"]],
                        now,
                        f"{task['software']} 接续上个任务",
                        task_id=task_id,
                    )
                )
                continue
            start = parse_hhmm(task.get("time"))
            days = self._days(task.get("days"))
            if start is None or weekday not in days:
                continue
            if self._task_ran_today(task_id, today):
                continue
            if self._within_window(now, start, grace):
                events.append(
                    ScheduleEvent(
                        "task",
                        task["software"],
                        [task["software"]],
                        now,
                        f"{task['software']} {format_hhmm(start)}",
                        task_id=task_id,
                    )
                )
        return events

    def mark(self, event: ScheduleEvent, now: datetime | None = None) -> None:
        now = now or datetime.now()
        today = now.strftime("%Y-%m-%d")
        if event.kind == "chain":
            if event.chain_time:
                self._mark_chain(event.chain_time, today)
                self._save()
        elif event.task_id:
            self._mark_task(event.task_id, today)
            self._save()

    def next_run(self, now: datetime | None = None) -> datetime | None:
        now = now or datetime.now()
        if not self.is_enabled():
            return None
        candidates: list[datetime] = []
        if self.mode() == MODE_SEQUENTIAL:
            for start, days in self.start_slots():
                if days:
                    candidates.append(self._next_datetime(now, start, days))
        else:
            for task in self.enabled_tasks():
                if task.get("trigger") == "after_previous":
                    continue
                start = parse_hhmm(task.get("time"))
                days = self._days(task.get("days"))
                if start is None or not days:
                    continue
                candidates.append(self._next_datetime(now, start, days))
        return min(candidates) if candidates else None

    @staticmethod
    def _next_datetime(now: datetime, start_min: int, days: list[int]) -> datetime:
        for offset in range(0, 8):
            day = (now + timedelta(days=offset)).date()
            candidate = datetime.combine(day, datetime.min.time()).replace(
                hour=start_min // 60, minute=start_min % 60
            )
            if candidate.weekday() in days and candidate > now:
                return candidate
        return now
