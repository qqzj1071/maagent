from __future__ import annotations

import threading

from loguru import logger
from PySide6.QtCore import QThread, Signal

from maagent.core.maaend import MaaEndOrchestrator
from maagent.core.orchestrator import Orchestrator
from maagent.gui.constants import SOFTWARE_META
from maagent.i18n import t


class WorkflowWorker(QThread):
    """Runs one or more daily workflows sequentially in a background thread.

    A single-software list is the plain "start this card" case; a longer list is
    the daily-workflow chain (same code path either way).
    """

    status = Signal(str)
    finished_report = Signal(object)

    def __init__(
        self,
        config: dict,
        software_list: list[str],
        stop_event: threading.Event | None = None,
    ) -> None:
        super().__init__()
        self.config = config
        self.software_list = list(software_list)
        self.stop_event = stop_event

    def run(self) -> None:
        total = len(self.software_list)
        for index, software in enumerate(self.software_list, 1):
            if self.stop_event is not None and self.stop_event.is_set():
                break
            name = SOFTWARE_META.get(software, (software, ""))[0]
            if total > 1:
                self.status.emit(
                    t("status.chain_progress", index=index, total=total, name=name)
                )
            else:
                self.status.emit(t("status.running"))
            logger.info("=== 日常工作流 {}/{}: {} ===", index, total, name)
            try:
                runner_cls = MaaEndOrchestrator if software == "maaend" else Orchestrator
                runner = runner_cls(self.config, stop_event=self.stop_event)
                report = runner.run_daily()
                self.finished_report.emit(report)
                if report.status == "stopped":
                    break
            except Exception as e:
                logger.exception("{} 工作流异常: {}", name, e)
