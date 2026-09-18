from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, Signal

from maagent.core.controller import WorkflowController


class ControllerBridge(QObject):
    """Republishes WorkflowController events as Qt signals.

    The controller emits from its worker thread; Qt signals are thread-safe and
    queued to the GUI thread, so slots may safely touch widgets.
    """

    status_changed = Signal(object)
    log_line = Signal(object)
    report_ready = Signal(object)

    def __init__(self, controller: WorkflowController, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        controller.subscribe(self._on_event)

    def _on_event(self, event: dict[str, Any]) -> None:
        kind = event.get("type")
        data = event.get("data")
        if kind in ("status", "snapshot"):
            self.status_changed.emit(data)
        elif kind == "log":
            self.log_line.emit(data)
        elif kind == "report":
            self.report_ready.emit(data)
