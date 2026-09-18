from __future__ import annotations

import json
import threading
import uuid
from collections import deque
from pathlib import Path
from typing import Any, Callable

from loguru import logger

from maagent.core.base import now_str
from maagent.core.maaend import MaaEndOrchestrator
from maagent.core.orchestrator import Orchestrator
from maagent.core.workflow_config import software_label
from maagent.report.generator import RunReport

MAX_REPORTS = 200
MAX_LOG_LINES = 500
LOG_SEP = "\x1f"


def report_to_dict(report: RunReport) -> dict[str, Any]:
    return {
        "id": uuid.uuid4().hex[:12],
        "game": report.game,
        "status": report.status,
        "status_label": report.status_label,
        "started_at": report.started_at,
        "finished_at": report.finished_at,
        "duration": report.duration,
        "errors": list(report.errors),
        "warnings": list(report.warnings),
        "sanity": report.sanity,
        "next_deadline": report.next_deadline,
        "annihilation": report.annihilation,
        "monthly": report.monthly,
        "tasks": report.tasks,
        "extra": [{"label": label, "value": value} for label, value in report.extra],
        "popups_closed": len(report.popups_closed),
        "logs": list(report.logs),
        "text": report.to_text(),
    }


def report_summary(data: dict[str, Any]) -> dict[str, Any]:
    summary = {k: v for k, v in data.items() if k not in ("logs",)}
    return summary


class WorkflowController:
    """Headless runner for the daily workflow chain, driven over HTTP.

    It reuses the same orchestrators as the GUI but without Qt, so the account
    server can start/stop the chain and expose its state, logs and reports.
    """

    def __init__(self, config: dict[str, Any], report_file: str | Path = "logs/reports.jsonl") -> None:
        self.config = config
        self.report_file = Path(report_file)
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._stop_event: threading.Event | None = None
        self._status = "idle"
        self._running = False
        self._software_list: list[str] = []
        self._index = 0
        self._current: str | None = None
        self._message = ""
        self._started_at = ""
        self._finished_at = ""
        self._reports: list[dict[str, Any]] = self._load_reports()
        self._log_lines: deque[dict[str, str]] = deque(maxlen=MAX_LOG_LINES)
        self._subscribers: list[Callable[[dict[str, Any]], None]] = []
        self._sink_id: int | None = None

    # ------------------------------------------------------------------ #
    # software resolution
    # ------------------------------------------------------------------ #
    def _software_enabled(self, key: str) -> bool:
        return bool((self.config.get("adapters", {}) or {}).get(key, {}).get("enabled", False))

    def resolve_software_list(self) -> list[str]:
        chain = (self.config.get("workflow", {}) or {}).get("chain", {}) or {}
        tasks = chain.get("sequential_tasks")
        if tasks is None:
            tasks = chain.get("tasks") or []
        result: list[str] = []
        for item in tasks:
            if not isinstance(item, dict):
                continue
            software = item.get("software")
            if not software or software in result:
                continue
            if not item.get("enabled", True):
                continue
            if not self._software_enabled(software):
                continue
            result.append(software)
        return result

    def available_software(self) -> list[dict[str, Any]]:
        chain = (self.config.get("workflow", {}) or {}).get("chain", {}) or {}
        tasks = chain.get("sequential_tasks")
        if tasks is None:
            tasks = chain.get("tasks") or []
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        for task in tasks:
            if not isinstance(task, dict):
                continue
            software = task.get("software")
            if not software or software in seen:
                continue
            seen.add(software)
            items.append(
                {
                    "software": software,
                    "name": software_label(software),
                    "enabled": bool(task.get("enabled", True)) and self._software_enabled(software),
                }
            )
        return items

    # ------------------------------------------------------------------ #
    # state
    # ------------------------------------------------------------------ #
    def is_running(self) -> bool:
        with self._lock:
            return self._running

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            running = self._running
            latest = report_summary(self._reports[-1]) if self._reports else None
            return {
                "status": self._status,
                "running": running,
                "software_list": list(self._software_list),
                "index": self._index,
                "total": len(self._software_list),
                "current": self._current,
                "message": self._message,
                "started_at": self._started_at,
                "finished_at": self._finished_at,
                "latest_report": latest,
                "report_count": len(self._reports),
                "software": self.available_software(),
            }

    # ------------------------------------------------------------------ #
    # control
    # ------------------------------------------------------------------ #
    def start(self, software_list: list[str] | None = None) -> dict[str, Any]:
        with self._lock:
            if self._running:
                raise RuntimeError("已有任务正在运行")
            if software_list:
                requested = [str(s) for s in software_list]
            else:
                requested = self.resolve_software_list()
            runnable = [s for s in requested if self._software_enabled(s)]
            if not runnable:
                raise RuntimeError("没有可执行的任务")
            self._stop_event = threading.Event()
            self._software_list = runnable
            self._running = True
            self._status = "running"
            self._index = 0
            self._current = None
            self._message = "正在启动..."
            self._started_at = now_str()
            self._finished_at = ""
            self._thread = threading.Thread(
                target=self._worker, args=(runnable,), name="maagent-remote", daemon=True
            )
        self._thread.start()
        self._emit_status()
        return self.snapshot()

    def stop(self) -> dict[str, Any]:
        with self._lock:
            if not self._running:
                return self.snapshot()
            self._status = "stopping"
            self._message = "正在停止..."
            event = self._stop_event
        if event is not None:
            event.set()
        self._emit_status()
        return self.snapshot()

    def _worker(self, software_list: list[str]) -> None:
        total = len(software_list)
        try:
            for index, software in enumerate(software_list, 1):
                if self._stop_event is not None and self._stop_event.is_set():
                    break
                with self._lock:
                    self._index = index
                    self._current = software
                    self._message = f"正在执行 {software_label(software)} ({index}/{total})"
                self._emit_status()
                logger.info("=== 远程工作流 {}/{}: {} ===", index, total, software)
                runner_cls = MaaEndOrchestrator if software == "maaend" else Orchestrator
                runner = runner_cls(self.config, stop_event=self._stop_event)
                report = runner.run_daily()
                self._append_report(report)
                if report.status == "stopped":
                    break
        except Exception as e:  # pragma: no cover - defensive
            logger.exception("远程工作流异常: {}", e)
        finally:
            with self._lock:
                self._running = False
                self._status = "idle"
                self._current = None
                self._finished_at = now_str()
                self._message = "已结束"
            self._emit_status()

    # ------------------------------------------------------------------ #
    # reports
    # ------------------------------------------------------------------ #
    def _load_reports(self) -> list[dict[str, Any]]:
        if not self.report_file.exists():
            return []
        reports: list[dict[str, Any]] = []
        try:
            for line in self.report_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    reports.append(json.loads(line))
                except ValueError:
                    continue
        except OSError:
            return []
        return reports[-MAX_REPORTS:]

    def _append_report(self, report: RunReport) -> None:
        data = report_to_dict(report)
        with self._lock:
            self._reports.append(data)
            self._reports = self._reports[-MAX_REPORTS:]
        try:
            self.report_file.parent.mkdir(parents=True, exist_ok=True)
            with self.report_file.open("a", encoding="utf-8") as f:
                f.write(json.dumps(data, ensure_ascii=False) + "\n")
        except OSError as e:
            logger.warning("保存报告失败: {}", e)
        self._emit({"type": "report", "data": report_summary(data)})

    def reports(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            items = self._reports[-max(1, int(limit)) :]
        return [report_summary(r) for r in reversed(items)]

    def get_report(self, report_id: str) -> dict[str, Any] | None:
        with self._lock:
            for report in reversed(self._reports):
                if report.get("id") == report_id:
                    return report
        return None

    def latest_report(self) -> dict[str, Any] | None:
        with self._lock:
            return self._reports[-1] if self._reports else None

    # ------------------------------------------------------------------ #
    # logs / events
    # ------------------------------------------------------------------ #
    def on_log(self, message: Any) -> None:
        try:
            parts = str(message).split(LOG_SEP)
            time_part = parts[0] if parts else ""
            level = parts[1] if len(parts) > 1 else "INFO"
            text = parts[2] if len(parts) > 2 else str(message)
        except Exception:
            time_part, level, text = "", "INFO", str(message)
        entry = {"time": time_part, "level": level, "message": text}
        self._log_lines.append(entry)
        self._emit({"type": "log", "data": entry})

    def logs(self, limit: int = 200) -> list[dict[str, str]]:
        with self._lock:
            return list(self._log_lines)[-max(1, int(limit)) :]

    def attach_log_sink(self) -> None:
        if self._sink_id is not None:
            return
        self._sink_id = logger.add(
            self.on_log,
            level="INFO",
            format="{time:HH:mm:ss}" + LOG_SEP + "{level}" + LOG_SEP + "{message}",
        )

    def detach_log_sink(self) -> None:
        if self._sink_id is not None:
            try:
                logger.remove(self._sink_id)
            except ValueError:
                pass
            self._sink_id = None

    def subscribe(self, callback: Callable[[dict[str, Any]], None]) -> None:
        with self._lock:
            self._subscribers.append(callback)

    def unsubscribe(self, callback: Callable[[dict[str, Any]], None]) -> None:
        with self._lock:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

    def emit_status(self) -> None:
        """Push the current snapshot to subscribers (e.g. after a config edit)."""
        self._emit_status()

    def _emit_status(self) -> None:
        self._emit({"type": "status", "data": self.snapshot()})

    def _emit(self, event: dict[str, Any]) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for callback in subscribers:
            try:
                callback(event)
            except Exception:
                pass
