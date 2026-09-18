from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from maagent.core import workflow_config as wc
from maagent.core.scheduler import Scheduler
from maagent.core.workflow_config import CHAIN_SOFTWARE
from maagent.gui.constants import WORKFLOW_NAME, software_meta
from maagent.gui.widgets import (
    Panel,
    TaskListEditor,
    TimeListEditor,
    ToggleSwitch,
    set_button_role,
)
from maagent.i18n import t


class WorkflowPanel(Panel):
    """The 日常工作流 panel: schedule toggle, mode, task lists and run button.

    Emits ``changed`` for any edit (the window then syncs + autosaves) and
    ``run_requested`` with the active mode's software list.
    """

    changed = Signal()
    run_requested = Signal(list)

    def __init__(self, config: dict, parent: QWidget | None = None) -> None:
        super().__init__(t(WORKFLOW_NAME), parent)
        self.config = config
        self._build()

    # -- construction --------------------------------------------------- #
    def _build(self) -> None:
        chain = wc.chain_cfg(self.config)

        hint = QLabel(t("wf.hint"))
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        self.add_widget(hint)

        enable_row = QHBoxLayout()
        enable_label = QLabel(t("wf.enable_schedule"))
        enable_label.setObjectName("OptionLabel")
        enable_row.addWidget(enable_label)
        enable_row.addStretch(1)
        self.chain_switch = ToggleSwitch(bool(chain.get("enabled", False)), width=38, height=20)
        self.chain_switch.toggled.connect(self._on_changed)
        enable_row.addWidget(self.chain_switch)
        self.add_layout(enable_row)

        mode_row = QHBoxLayout()
        mode_label = QLabel(t("wf.mode"))
        mode_label.setObjectName("OptionLabel")
        mode_row.addWidget(mode_label)
        self.mode_seq_btn = QPushButton(t("wf.mode_sequential"))
        self.mode_task_btn = QPushButton(t("wf.mode_scheduled"))
        for button in (self.mode_seq_btn, self.mode_task_btn):
            button.setObjectName("ModeChip")
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
        self.mode_seq_btn.clicked.connect(lambda: self.set_mode("sequential"))
        self.mode_task_btn.clicked.connect(lambda: self.set_mode("scheduled"))
        mode_row.addWidget(self.mode_seq_btn)
        mode_row.addWidget(self.mode_task_btn)
        mode_row.addStretch(1)
        self.add_layout(mode_row)

        self.seq_box = QWidget()
        seq_layout = QVBoxLayout(self.seq_box)
        seq_layout.setContentsMargins(0, 0, 0, 0)
        seq_layout.setSpacing(6)
        times_label = QLabel(t("wf.start_times_label"))
        times_label.setObjectName("ToggleLabel")
        seq_layout.addWidget(times_label)
        self.chain_times = TimeListEditor(wc.config_start_slots(chain))
        self.chain_times.changed.connect(self._on_changed)
        seq_layout.addWidget(self.chain_times)
        seq_tasks_label = QLabel(t("wf.seq_order_label"))
        seq_tasks_label.setObjectName("Hint")
        seq_layout.addWidget(seq_tasks_label)
        self.seq_list = TaskListEditor(False, software_meta(), list(CHAIN_SOFTWARE))
        self.seq_list.changed.connect(self._on_changed)
        self.seq_list.set_tasks(wc.config_sequential_tasks(chain))
        seq_layout.addWidget(self.seq_list)
        self.add_widget(self.seq_box)

        self.sched_box = QWidget()
        sched_layout = QVBoxLayout(self.sched_box)
        sched_layout.setContentsMargins(0, 0, 0, 0)
        sched_layout.setSpacing(6)
        sched_hint = QLabel(t("wf.sched_hint"))
        sched_hint.setObjectName("Hint")
        sched_hint.setWordWrap(True)
        sched_layout.addWidget(sched_hint)
        self.sched_list = TaskListEditor(True, software_meta(), list(CHAIN_SOFTWARE))
        self.sched_list.changed.connect(self._on_changed)
        self.sched_list.set_tasks(wc.config_scheduled_tasks(chain))
        sched_layout.addWidget(self.sched_list)
        self.add_widget(self.sched_box)

        action_row = QHBoxLayout()
        self.btn_run = QPushButton(t("btn.run_chain"))
        self.btn_run.setObjectName("Primary")
        self.btn_run.setCursor(Qt.PointingHandCursor)
        self.btn_run.clicked.connect(self.request_run)
        action_row.addWidget(self.btn_run)
        action_row.addStretch(1)
        self.add_layout(action_row)

        self.status_label = QLabel()
        self.status_label.setObjectName("Hint")
        self.status_label.setWordWrap(True)
        self.add_widget(self.status_label)

        mode = chain.get("mode", "sequential")
        self.mode_seq_btn.setChecked(mode != "scheduled")
        self.mode_task_btn.setChecked(mode == "scheduled")
        self.apply_mode()

    # -- state ---------------------------------------------------------- #
    def is_enabled(self) -> bool:
        return self.chain_switch.isChecked()

    def mode(self) -> str:
        return "scheduled" if self.mode_task_btn.isChecked() else "sequential"

    def set_mode(self, mode: str) -> None:
        self.mode_seq_btn.setChecked(mode != "scheduled")
        self.mode_task_btn.setChecked(mode == "scheduled")
        self._on_changed()

    def apply_mode(self) -> None:
        scheduled = self.mode_task_btn.isChecked()
        self.seq_box.setVisible(not scheduled)
        self.sched_box.setVisible(scheduled)

    def run_list(self) -> list[str]:
        if self.mode_task_btn.isChecked():
            return self.sched_list.enabled_softwares(unique=False)
        return self.seq_list.enabled_softwares(unique=True)

    def request_run(self) -> None:
        self.run_requested.emit(self.run_list())

    def collect(self) -> dict:
        chain = wc.chain_cfg(self.config)
        return {
            "enabled": self.chain_switch.isChecked(),
            "mode": self.mode(),
            "start_slots": self.chain_times.slots(),
            "grace_minutes": int(chain.get("grace_minutes", 30) or 0),
            "state_file": chain.get("state_file", "logs/schedule_state.json"),
            "sequential_tasks": self.seq_list.collect(),
            "scheduled_tasks": self.sched_list.collect(),
        }

    def refresh_status(self, scheduler: Scheduler) -> None:
        if not self.chain_switch.isChecked():
            self.status_label.setText("定时：已停用 → 不会自动触发")
            return
        next_run = scheduler.next_run()
        if next_run is None:
            self.status_label.setText("定时：未设置有效时间")
            return
        minutes = max(0, int((next_run - datetime.now()).total_seconds() // 60))
        hours, mins = divmod(minutes, 60)
        eta = f"{hours} 小时 {mins} 分" if hours else f"{mins} 分"
        self.status_label.setText(
            f"下次运行：{next_run.strftime('%m-%d %H:%M')}（约 {eta} 后）"
        )

    def set_running(self, running: bool, stopping: bool = False) -> None:
        if stopping:
            self.btn_run.setEnabled(False)
            self.btn_run.setText(t("btn.stopping"))
            set_button_role(self.btn_run, "Danger")
        elif running:
            self.btn_run.setEnabled(True)
            self.btn_run.setText(t("btn.stop"))
            set_button_role(self.btn_run, "Danger")
        else:
            self.btn_run.setEnabled(True)
            self.btn_run.setText(t("btn.run_chain"))
            set_button_role(self.btn_run, "Primary")

    def _on_changed(self) -> None:
        self.apply_mode()
        self.changed.emit()
