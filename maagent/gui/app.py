from __future__ import annotations

import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import yaml
from loguru import logger
from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from maagent.config import load_config
from maagent.control.monthly import MonthlyState
from maagent.control.process import close_all
from maagent.control.weekly import WEEKDAY_NAMES, WeeklyState
from maagent.core.maaend import MaaEndOrchestrator
from maagent.core.orchestrator import Orchestrator
from maagent.core.scheduler import Scheduler
from maagent.gui.widgets import (
    CollapsibleSection,
    LogBridge,
    Panel,
    SoftwareCard,
    TaskListEditor,
    TimeListEditor,
    ToggleSwitch,
)
from maagent.log.logger import setup_logger

SOFTWARE_META: dict[str, tuple[str, str]] = {
    "maa": ("MAA", "明日方舟"),
    "maaend": ("MaaEnd", "明日方舟：终末地"),
    "bgi": ("BetterGI", "原神"),
}

WORKFLOW_KEY = "workflow"
WORKFLOW_NAME = "日常工作流"
WORKFLOW_DESC = "编排顺序 · 定时执行"
CHAIN_SOFTWARE = ("maa", "maaend")

LIGHT_QSS = """
QWidget { font-size: 14px; }
QMainWindow, QWidget#Central { background: #ffffff; }
QLabel { color: #1f2328; }
QLabel#SectionTitle { color: #6b7280; font-size: 13px; font-weight: 600; }
QLabel#StatusLabel { color: #57606a; font-size: 14px; }

QFrame#Card { background: #ffffff; border: 1px solid #e5e7eb; border-radius: 10px; }
QFrame#Card:hover { border-color: #c7d2fe; background: #f8faff; }
QFrame#Card[selected="true"] { border: 2px solid #4f46e5; background: #eef2ff; }
QFrame#Card[inactive="true"] { background: #fafafa; border-color: #eceff3; }
QLabel#CardName { font-size: 15px; font-weight: 700; color: #111827; }
QLabel#CardDesc { font-size: 12px; color: #6b7280; }
QLabel#CardState { font-size: 11px; }

QFrame#Panel { background: #ffffff; border: 1px solid #e5e7eb; border-radius: 10px; }
QLabel#PanelTitle { color: #374151; font-size: 14px; font-weight: 600; }

QFrame#FutureArea { background: #fcfcfd; border: 1px dashed #d8dee6; border-radius: 10px; }
QLabel#Hint { color: #6b7280; font-size: 13px; }
QToolButton#SectionHeader { border: none; background: transparent; color: #111827; font-size: 14px; font-weight: 600; padding: 6px 8px; text-align: left; }
QToolButton#SectionHeader:hover { background: #f3f4f6; border-radius: 6px; color: #4f46e5; }
QLabel#ToggleLabel { color: #6b7280; font-size: 13px; }
QLabel#OptionLabel { color: #6b7280; font-size: 12px; }

QPushButton#DayChip { background: #ffffff; border: 1px solid #d0d7de; color: #374151; padding: 7px 0; border-radius: 8px; font-size: 14px; }
QPushButton#DayChip:hover { border-color: #a5b4fc; background: #f8faff; }
QPushButton#DayChip:checked { background: #4f46e5; border-color: #4f46e5; color: #ffffff; font-weight: 600; }
QPushButton#ModeChip { background: #ffffff; border: 1px solid #d0d7de; color: #374151; padding: 6px 14px; border-radius: 8px; font-size: 13px; }
QPushButton#ModeChip:hover { border-color: #a5b4fc; background: #f8faff; }
QPushButton#ModeChip:checked { background: #4f46e5; border-color: #4f46e5; color: #ffffff; font-weight: 600; }

QFrame#TaskRow { background: #fbfbfd; border: 1px solid #eceff3; border-radius: 8px; }
QFrame#TaskRow[dragging="true"] { background: #eef2ff; border: 1px solid #a5b4fc; }
QFrame#DropIndicator { background: #4f46e5; border-radius: 1px; }
QLabel#TaskOrder { color: #4f46e5; font-weight: 700; font-size: 13px; }
QLabel#DragHandle { color: #b6bec9; font-size: 16px; font-weight: 700; }
QLabel#DragHandle:hover { color: #4f46e5; }
QTimeEdit { background: #ffffff; border: 1px solid #d0d7de; border-radius: 6px; padding: 3px 6px; color: #1f2328; }
QTimeEdit:hover { border-color: #a5b4fc; }

QPlainTextEdit { background: #fbfbfd; border: 1px solid #e5e7eb; border-radius: 8px; padding: 6px; color: #1f2328; }

QScrollArea#WeeklyScroll { background: transparent; border: none; }
QWidget#ScrollInner { background: transparent; }
QWidget#LeftPanel { background: transparent; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: #d0d7de; border-radius: 5px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: #a5b4fc; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }

QPushButton { background: #ffffff; border: 1px solid #d0d7de; border-radius: 8px; padding: 8px 16px; color: #1f2328; }
QPushButton:hover { background: #f6f8fa; }
QPushButton:pressed { background: #eef1f4; }
QPushButton:disabled { color: #9ca3af; background: #f6f7f9; }
QPushButton#Primary { background: #4f46e5; border-color: #4f46e5; color: #ffffff; font-weight: 600; }
QPushButton#Primary:hover { background: #4338ca; }
QPushButton#Primary:disabled { background: #c7cbf5; border-color: #c7cbf5; color: #ffffff; }
QPushButton#Danger { background: #dc2626; border-color: #dc2626; color: #ffffff; font-weight: 600; }
QPushButton#Danger:hover { background: #b91c1c; }
QPushButton#Danger:disabled { background: #f0a3a3; border-color: #f0a3a3; color: #ffffff; }
QPushButton#Ghost { background: transparent; border: none; color: #6b7280; padding: 4px 10px; border-radius: 6px; font-size: 13px; }
QPushButton#Ghost:hover { background: #f3f4f6; color: #4f46e5; }
QPushButton#Ghost:disabled { color: #d8dee6; }
"""


def app_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def asset_path(name: str) -> Path:
    if getattr(sys, "frozen", False):
        candidates = [
            Path(getattr(sys, "_MEIPASS", app_base_dir())) / "maagent" / "gui" / "assets" / name,
            app_base_dir() / "assets" / name,
        ]
    else:
        candidates = [Path(__file__).resolve().parent / "assets" / name]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def resolve_config_path() -> Path:
    base = app_base_dir()
    external = base / "config" / "config.yaml"
    if external.exists():
        return external
    bundled = Path(getattr(sys, "_MEIPASS", base)) / "config" / "config.yaml"
    return bundled


class DailyWorker(QThread):
    status = Signal(str)
    finished_report = Signal(object)

    def __init__(
        self,
        config: dict,
        stop_event: threading.Event | None = None,
        software: str = "maa",
    ) -> None:
        super().__init__()
        self.config = config
        self.stop_event = stop_event
        self.software = software

    def run(self) -> None:
        try:
            if self.software == "maaend":
                runner = MaaEndOrchestrator(self.config, stop_event=self.stop_event)
            else:
                runner = Orchestrator(self.config, stop_event=self.stop_event)
            report = runner.run_daily()
            self.finished_report.emit(report)
        except Exception as e:
            logger.exception("工作流异常: {}", e)
            self.status.emit(f"状态: 异常 - {e}")


class ChainWorker(QThread):
    """Runs several daily workflows sequentially (the 日常工作流 chain)."""

    status = Signal(str)
    finished_report = Signal(object)

    def __init__(
        self,
        config: dict,
        stop_event: threading.Event | None,
        software_list: list[str],
    ) -> None:
        super().__init__()
        self.config = config
        self.stop_event = stop_event
        self.software_list = software_list

    def run(self) -> None:
        total = len(self.software_list)
        for index, software in enumerate(self.software_list, 1):
            if self.stop_event is not None and self.stop_event.is_set():
                break
            name = SOFTWARE_META.get(software, (software, ""))[0]
            self.status.emit(f"状态: 运行中... ({index}/{total}) {name}")
            logger.info("=== 日常工作流 {}/{}: {} ===", index, total, name)
            try:
                if software == "maaend":
                    runner = MaaEndOrchestrator(self.config, stop_event=self.stop_event)
                else:
                    runner = Orchestrator(self.config, stop_event=self.stop_event)
                report = runner.run_daily()
                self.finished_report.emit(report)
                if report.status == "stopped":
                    break
            except Exception as e:
                logger.exception("{} 工作流异常: {}", name, e)


class MaAgentWindow(QMainWindow):
    def __init__(self, config: dict, config_path: Path, bridge: LogBridge) -> None:
        super().__init__()
        self.config = config
        self.config_path = config_path
        self.bridge = bridge
        self.worker: DailyWorker | None = None
        self.stop_event: threading.Event | None = None
        self.cards: dict[str, SoftwareCard] = {}
        self.selected_software: str | None = None

        self.scheduler = Scheduler(
            config,
            (config.get("workflow", {}) or {}).get("chain", {}).get(
                "state_file", "logs/schedule_state.json"
            ),
        )
        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.setInterval(5000)
        self.save_timer.timeout.connect(self.save_settings)
        self.schedule_timer = QTimer(self)
        self.schedule_timer.setInterval(20000)
        self.schedule_timer.timeout.connect(self.check_schedule)
        self._build_ui()
        self.bridge.message.connect(self.append_log)
        self.schedule_timer.start()

    def _software_defs(self) -> list[tuple[str, str, str, bool]]:
        adapters = self.config.get("adapters", {}) or {}
        keys = list(SOFTWARE_META.keys())
        keys += [k for k in adapters if k not in keys]
        defs = []
        for key in keys:
            name, desc = SOFTWARE_META.get(key, (key.upper(), ""))
            enabled = bool((adapters.get(key) or {}).get("enabled", False))
            defs.append((key, name, desc, enabled))
        return defs

    def _build_day_grid(self, selected: set[int], on_click) -> tuple[QGridLayout, list[QPushButton]]:
        grid = QGridLayout()
        grid.setSpacing(8)
        buttons: list[QPushButton] = []
        for i, name in enumerate(WEEKDAY_NAMES):
            btn = QPushButton(name)
            btn.setObjectName("DayChip")
            btn.setCheckable(True)
            btn.setChecked(i in selected)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _=False, idx=i: on_click(idx))
            buttons.append(btn)
            grid.addWidget(btn, i // 4, i % 4)
        return grid, buttons

    def _build_ui(self) -> None:
        self.setWindowTitle("maagent - 二游日常助手")
        self.resize(1079, 667)
        self.setMinimumSize(940, 580)

        central = QWidget()
        central.setObjectName("Central")
        layout = QVBoxLayout(central)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        card_row = QHBoxLayout()
        card_row.setSpacing(10)
        chain_enabled = bool(
            (self.config.get("workflow", {}) or {}).get("chain", {}).get("enabled", False)
        )
        self.workflow_card = SoftwareCard(WORKFLOW_KEY, WORKFLOW_NAME, WORKFLOW_DESC, True)
        self.workflow_card.set_state_text(
            "定时已开" if chain_enabled else "定时已关", active=chain_enabled
        )
        self.workflow_card.clicked.connect(self.select_software)
        self.cards[WORKFLOW_KEY] = self.workflow_card
        card_row.addWidget(self.workflow_card)
        for key, name, desc, enabled in self._software_defs():
            card = SoftwareCard(key, name, desc, enabled)
            card.clicked.connect(self.select_software)
            self.cards[key] = card
            card_row.addWidget(card)
        card_row.addStretch(1)
        layout.addLayout(card_row)

        body = QHBoxLayout()
        body.setSpacing(12)

        left_col = QVBoxLayout()
        left_col.setSpacing(12)

        scroll = QScrollArea()
        scroll.setObjectName("WeeklyScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.viewport().setAutoFillBackground(False)
        scroll_inner = QWidget()
        scroll_inner.setObjectName("ScrollInner")
        inner = QVBoxLayout(scroll_inner)
        inner.setContentsMargins(0, 0, 8, 0)
        inner.setSpacing(12)
        scroll.setWidget(scroll_inner)

        self.weekly_panel = Panel("周常")
        weekly_panel = self.weekly_panel
        potion_cfg = self.config.get("weekly", {}).get("potion") or {}
        potion_section = CollapsibleSection("体力药使用", enabled=bool(potion_cfg.get("enabled", False)))
        self.potion_section = potion_section
        potion_hint = QLabel("勾选需要使用体力药的日子；其余日子会自动关闭 MAA 的「使用药剂」，避免浪费。")
        potion_hint.setObjectName("Hint")
        potion_hint.setWordWrap(True)
        potion_section.add_widget(potion_hint)

        day_grid, self.day_buttons = self._build_day_grid(
            set(potion_cfg.get("days") or []), lambda _i: self.on_weekly_changed()
        )
        potion_section.add_layout(day_grid)

        self.weekly_status = QLabel()
        self.weekly_status.setObjectName("Hint")
        self.weekly_status.setWordWrap(True)
        potion_section.add_widget(self.weekly_status)

        potion_section.enable_switch.toggled.connect(self.on_weekly_changed)

        anni_cfg = self.config.get("weekly", {}).get("annihilation") or {}
        anni_section = CollapsibleSection("剿灭刷取", enabled=bool(anni_cfg.get("enabled", False)))
        self.anni_section = anni_section
        anni_hint = QLabel("每周剿灭上限为剿灭模式 1800/1800。选择一个刷取日，其余日子会自动取消勾选「剿灭刷取」。")
        anni_hint.setObjectName("Hint")
        anni_hint.setWordWrap(True)
        anni_section.add_widget(anni_hint)

        selected_day = anni_cfg.get("day")
        selected_anni = {int(selected_day)} if selected_day is not None else set()
        anni_grid, self.anni_day_buttons = self._build_day_grid(
            selected_anni, self.on_annihilation_day_clicked
        )
        anni_section.add_layout(anni_grid)

        self.anni_status = QLabel()
        self.anni_status.setObjectName("Hint")
        self.anni_status.setWordWrap(True)
        anni_section.add_widget(self.anni_status)

        anni_section.enable_switch.toggled.connect(self.on_weekly_changed)

        weekly_panel.add_widget(potion_section)
        weekly_panel.add_widget(anni_section)
        inner.addWidget(weekly_panel)

        self.monthly_panel = Panel("月常")
        monthly_panel = self.monthly_panel
        self.monthly_sections: dict[str, CollapsibleSection] = {}
        self.monthly_status: dict[str, QLabel] = {}
        monthly_cfg = self.config.get("monthly", {}) or {}
        for key, title, hint in (
            ("green", "绿票商店", "每月刷新后自动购买绿票商店物资，购买结果会写入任务报告。"),
            ("yellow", "黄票商店", "每月刷新后自动购买黄票商店物资，购买结果会写入任务报告。"),
        ):
            item_cfg = monthly_cfg.get(key) or {}
            section = CollapsibleSection(title, enabled=bool(item_cfg.get("enabled", False)))
            hint_label = QLabel(hint)
            hint_label.setObjectName("Hint")
            hint_label.setWordWrap(True)
            section.add_widget(hint_label)
            status = QLabel()
            status.setObjectName("Hint")
            status.setWordWrap(True)
            section.add_widget(status)
            section.enable_switch.toggled.connect(self.on_weekly_changed)
            self.monthly_sections[key] = section
            self.monthly_status[key] = status
            monthly_panel.add_widget(section)
        inner.addWidget(monthly_panel)

        self.workflow_panel = self._build_workflow_panel()
        inner.addWidget(self.workflow_panel)
        inner.addStretch(1)
        left_col.addWidget(scroll, 1)

        auto_row = QHBoxLayout()
        auto_row.setContentsMargins(4, 0, 4, 0)
        auto_row.setSpacing(8)
        auto_label = QLabel("任务完成后自动关闭 MAA 与模拟器")
        auto_label.setObjectName("OptionLabel")
        auto_row.addWidget(auto_label)
        auto_row.addStretch(1)
        self.auto_close_switch = ToggleSwitch(
            bool((self.config.get("workflow") or {}).get("auto_close", False)),
            width=38,
            height=20,
        )
        self.auto_close_switch.toggled.connect(self.on_option_changed)
        auto_row.addWidget(self.auto_close_switch)
        left_col.addLayout(auto_row)

        self.left_panel = QWidget()
        self.left_panel.setObjectName("LeftPanel")
        self.left_panel.setLayout(left_col)
        body.addWidget(self.left_panel, 7)
        self.update_weekly_status()
        self.update_annihilation_status()
        self.update_monthly_status()
        self.update_schedule_status()

        right_col = QVBoxLayout()
        right_col.setSpacing(12)

        log_panel = Panel("运行日志")
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setPlaceholderText("暂无日志")
        log_panel.add_widget(self.log_view, 1)
        btn_clear_log = QPushButton("清空")
        btn_clear_log.setObjectName("Ghost")
        btn_clear_log.setCursor(Qt.PointingHandCursor)
        btn_clear_log.clicked.connect(self.log_view.clear)
        log_panel.add_action(btn_clear_log)
        right_col.addWidget(log_panel, 3)

        report_panel = Panel("任务报告")
        self.report_view = QPlainTextEdit()
        self.report_view.setReadOnly(True)
        self.report_view.setPlaceholderText("暂无报告")
        report_panel.add_widget(self.report_view, 1)
        btn_copy = QPushButton("复制")
        btn_copy.setObjectName("Ghost")
        btn_copy.setCursor(Qt.PointingHandCursor)
        btn_copy.clicked.connect(self.copy_report)
        report_panel.add_action(btn_copy)
        right_col.addWidget(report_panel, 2)

        body.addLayout(right_col, 3)
        layout.addLayout(body, 1)

        bottom = QHBoxLayout()
        bottom.setSpacing(10)
        self.status_label = QLabel("状态: 空闲")
        self.status_label.setObjectName("StatusLabel")
        bottom.addWidget(self.status_label)
        bottom.addStretch(1)
        self.btn_config = QPushButton("打开配置目录")
        self.btn_config.clicked.connect(self.open_config_dir)
        self.btn_close = QPushButton("关闭 MAA / 模拟器")
        self.btn_close.clicked.connect(self.do_close_all)
        self.btn_start = QPushButton("开始日常")
        self.btn_start.setObjectName("Primary")
        self.btn_start.clicked.connect(self.start_daily)
        bottom.addWidget(self.btn_config)
        bottom.addWidget(self.btn_close)
        bottom.addWidget(self.btn_start)
        layout.addLayout(bottom)

        self.setCentralWidget(central)

        needs_save = self._chain_needs_save()
        self._sync_chain_config()
        if needs_save:
            self.save_timer.start()
        self.select_software(WORKFLOW_KEY)

    def _chain_cfg(self) -> dict:
        return (self.config.get("workflow", {}) or {}).get("chain", {}) or {}

    @staticmethod
    def _new_task_id(software: str) -> str:
        return f"{software}-{uuid4().hex[:6]}"

    def _config_start_slots(self) -> list[dict]:
        chain = self._chain_cfg()
        raw = chain.get("start_slots")
        if raw is None:
            days = chain.get("days")
            if days is None:
                days = list(range(7))
            times = chain.get("start_times")
            if times is None:
                single = chain.get("start_time")
                times = [single] if single else []
            if not isinstance(times, list):
                times = [times]
            raw = [{"time": str(t), "days": list(days)} for t in times]
        if not raw:
            raw = [{"time": "08:00", "days": list(range(7))}]
        return raw

    def _normalize_task(self, task: dict, scheduled: bool) -> dict:
        item = dict(task)
        item["id"] = str(item.get("id") or self._new_task_id(item["software"]))
        item.setdefault("enabled", True)
        if scheduled:
            item.setdefault("time", "08:00")
            item.setdefault("days", list(range(7)))
        return item

    def _legacy_split(self, chain: dict) -> tuple[list[dict], list[dict]]:
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

    def _config_sequential_tasks(self) -> list[dict]:
        chain = self._chain_cfg()
        raw = chain.get("sequential_tasks")
        if raw is None:
            raw, _ = self._legacy_split(chain)
        tasks: list[dict] = []
        seen: set[str] = set()
        for item in raw:
            if not isinstance(item, dict) or item.get("software") not in CHAIN_SOFTWARE:
                continue
            key = item["software"]
            if key in seen:
                continue
            seen.add(key)
            tasks.append(self._normalize_task(item, False))
        for key in CHAIN_SOFTWARE:
            if key not in seen:
                tasks.append({"id": self._new_task_id(key), "software": key, "enabled": True})
        return tasks

    def _config_scheduled_tasks(self) -> list[dict]:
        chain = self._chain_cfg()
        raw = chain.get("scheduled_tasks")
        if raw is None:
            _, raw = self._legacy_split(chain)
        tasks = [
            self._normalize_task(item, True)
            for item in raw
            if isinstance(item, dict) and item.get("software") in CHAIN_SOFTWARE
        ]
        present = {t["software"] for t in tasks}
        for key in CHAIN_SOFTWARE:
            if key not in present:
                tasks.append(self._normalize_task({"software": key}, True))
        return tasks

    def _build_workflow_panel(self) -> QWidget:
        chain = self._chain_cfg()
        panel = Panel(WORKFLOW_NAME)
        hint = QLabel(
            "编排各日常的执行顺序，并按日程表定时自动执行。\n"
            "整链定时：到点后按顺序依次跑完所有已启用任务；"
            "逐条定时：每条任务按各自的时间与星期单独触发。"
        )
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        panel.add_widget(hint)

        enable_row = QHBoxLayout()
        enable_label = QLabel("启用定时")
        enable_label.setObjectName("OptionLabel")
        enable_row.addWidget(enable_label)
        enable_row.addStretch(1)
        self.chain_switch = ToggleSwitch(bool(chain.get("enabled", False)), width=38, height=20)
        self.chain_switch.toggled.connect(self.on_workflow_changed)
        enable_row.addWidget(self.chain_switch)
        panel.add_layout(enable_row)

        mode_row = QHBoxLayout()
        mode_label = QLabel("定时方式")
        mode_label.setObjectName("OptionLabel")
        mode_row.addWidget(mode_label)
        self.mode_seq_btn = QPushButton("整链定时")
        self.mode_task_btn = QPushButton("逐条定时")
        for btn in (self.mode_seq_btn, self.mode_task_btn):
            btn.setObjectName("ModeChip")
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
        self.mode_seq_btn.clicked.connect(lambda: self.set_workflow_mode("sequential"))
        self.mode_task_btn.clicked.connect(lambda: self.set_workflow_mode("scheduled"))
        mode_row.addWidget(self.mode_seq_btn)
        mode_row.addWidget(self.mode_task_btn)
        mode_row.addStretch(1)
        panel.add_layout(mode_row)

        self.seq_box = QWidget()
        seq_layout = QVBoxLayout(self.seq_box)
        seq_layout.setContentsMargins(0, 0, 0, 0)
        seq_layout.setSpacing(6)
        times_label = QLabel("启动时间与运行日（可添加多个时间，每个时间点按顺序跑一遍）")
        times_label.setObjectName("ToggleLabel")
        seq_layout.addWidget(times_label)
        self.chain_times = TimeListEditor(self._config_start_slots())
        self.chain_times.changed.connect(self.on_workflow_changed)
        seq_layout.addWidget(self.chain_times)
        seq_tasks_label = QLabel("执行顺序（每个日常仅一条，按住 ≡ 拖动调整顺序）")
        seq_tasks_label.setObjectName("Hint")
        seq_layout.addWidget(seq_tasks_label)
        self.seq_list = TaskListEditor(False, SOFTWARE_META, list(CHAIN_SOFTWARE))
        self.seq_list.changed.connect(self.on_workflow_changed)
        self.seq_list.set_tasks(self._config_sequential_tasks())
        seq_layout.addWidget(self.seq_list)
        panel.add_widget(self.seq_box)

        self.sched_box = QWidget()
        sched_layout = QVBoxLayout(self.sched_box)
        sched_layout.setContentsMargins(0, 0, 0, 0)
        sched_layout.setSpacing(6)
        sched_hint = QLabel("每条任务按各自的时间与星期单独触发；可复制条目设置不同时间。")
        sched_hint.setObjectName("Hint")
        sched_hint.setWordWrap(True)
        sched_layout.addWidget(sched_hint)
        self.sched_list = TaskListEditor(True, SOFTWARE_META, list(CHAIN_SOFTWARE))
        self.sched_list.changed.connect(self.on_workflow_changed)
        self.sched_list.set_tasks(self._config_scheduled_tasks())
        sched_layout.addWidget(self.sched_list)
        panel.add_widget(self.sched_box)

        action_row = QHBoxLayout()
        self.btn_run_chain = QPushButton("按顺序立即执行")
        self.btn_run_chain.setObjectName("Primary")
        self.btn_run_chain.setCursor(Qt.PointingHandCursor)
        self.btn_run_chain.clicked.connect(lambda: self.start_chain())
        action_row.addWidget(self.btn_run_chain)
        action_row.addStretch(1)
        panel.add_layout(action_row)

        self.schedule_status = QLabel()
        self.schedule_status.setObjectName("Hint")
        self.schedule_status.setWordWrap(True)
        panel.add_widget(self.schedule_status)

        mode = chain.get("mode", "sequential")
        self.mode_seq_btn.setChecked(mode != "scheduled")
        self.mode_task_btn.setChecked(mode == "scheduled")
        self.apply_workflow_mode()
        return panel

    def select_software(self, key: str) -> None:
        self.selected_software = key
        for card_key, card in self.cards.items():
            card.set_selected(card_key == key)
        # 周常/月常 只对 MAA 有意义；日常工作流 有独立的编排面板
        is_maa = key == "maa"
        self.weekly_panel.setVisible(is_maa)
        self.monthly_panel.setVisible(is_maa)
        self.workflow_panel.setVisible(key == WORKFLOW_KEY)
        self.left_panel.setVisible(key in ("maa", WORKFLOW_KEY))

    # -- 日常工作流 ------------------------------------------------------ #
    def set_workflow_mode(self, mode: str) -> None:
        self.mode_seq_btn.setChecked(mode != "scheduled")
        self.mode_task_btn.setChecked(mode == "scheduled")
        self.on_workflow_changed()

    def apply_workflow_mode(self) -> None:
        scheduled = self.mode_task_btn.isChecked()
        self.seq_box.setVisible(not scheduled)
        self.sched_box.setVisible(scheduled)

    def _chain_needs_save(self) -> bool:
        """True when the stored chain lacks the split task lists or stable ids."""
        chain = self._chain_cfg()
        if "start_slots" not in chain:
            return True
        for key in ("sequential_tasks", "scheduled_tasks"):
            stored = chain.get(key)
            if not stored:
                return True
            if any(
                not isinstance(t, dict) or not t.get("id") for t in stored
            ):
                return True
        return False

    def _sync_chain_config(self) -> None:
        """Push the current UI state into the config so the scheduler sees it."""
        self.config.setdefault("workflow", {})["chain"] = self._collect_chain()

    def on_workflow_changed(self) -> None:
        self.apply_workflow_mode()
        self._sync_chain_config()
        enabled = self.chain_switch.isChecked()
        self.workflow_card.set_state_text("定时已开" if enabled else "定时已关", active=enabled)
        self.update_schedule_status()
        self.save_timer.start()

    def _collect_chain(self) -> dict:
        chain = self._chain_cfg()
        return {
            "enabled": self.chain_switch.isChecked(),
            "mode": "scheduled" if self.mode_task_btn.isChecked() else "sequential",
            "start_slots": self.chain_times.slots(),
            "grace_minutes": int(chain.get("grace_minutes", 30) or 0),
            "state_file": chain.get("state_file", "logs/schedule_state.json"),
            "sequential_tasks": self.seq_list.collect(),
            "scheduled_tasks": self.sched_list.collect(),
        }

    def update_schedule_status(self) -> None:
        if not hasattr(self, "schedule_status"):
            return
        if not self.chain_switch.isChecked():
            self.schedule_status.setText("定时：已停用 → 不会自动触发")
            return
        next_run = self.scheduler.next_run()
        if next_run is None:
            self.schedule_status.setText("定时：未设置有效时间")
            return
        minutes = max(0, int((next_run - datetime.now()).total_seconds() // 60))
        hours, mins = divmod(minutes, 60)
        eta = f"{hours} 小时 {mins} 分" if hours else f"{mins} 分"
        self.schedule_status.setText(
            f"下次运行：{next_run.strftime('%m-%d %H:%M')}（约 {eta} 后）"
        )

    def check_schedule(self) -> None:
        if self.worker and self.worker.isRunning():
            return
        self._sync_chain_config()
        try:
            events = self.scheduler.poll()
        except Exception as e:
            logger.warning("定时检查失败: {}", e)
            return
        if events:
            event = events[0]
            self.scheduler.mark(event)
            logger.info("定时触发日常工作流: {}", event.label)
            self.start_chain(event.software_list)
        self.update_schedule_status()

    def selected_days(self) -> list[int]:
        return [i for i, btn in enumerate(self.day_buttons) if btn.isChecked()]

    def update_weekly_status(self) -> None:
        today = datetime.now().weekday()
        if not self.potion_section.is_enabled():
            self.weekly_status.setText("体力药刷取：已停用 → 始终关闭「使用药剂」")
            return
        days = self.selected_days()
        names = "、".join(WEEKDAY_NAMES[i] for i in days) or "（未选择）"
        action = "开启" if today in days else "关闭"
        self.weekly_status.setText(
            f"使用体力药：{names}\n今天 {WEEKDAY_NAMES[today]} → 自动{action}「使用药剂」"
        )

    def on_weekly_changed(self) -> None:
        self.update_weekly_status()
        self.update_annihilation_status()
        self.update_monthly_status()
        self.save_timer.start()

    def selected_annihilation_day(self) -> int | None:
        for i, btn in enumerate(self.anni_day_buttons):
            if btn.isChecked():
                return i
        return None

    def on_annihilation_day_clicked(self, index: int) -> None:
        for i, btn in enumerate(self.anni_day_buttons):
            btn.setChecked(i == index)
        self.on_weekly_changed()

    def update_annihilation_status(self) -> None:
        today = datetime.now().weekday()
        if not self.anni_section.is_enabled():
            self.anni_status.setText("剿灭刷取：已停用 → 始终取消勾选「剿灭刷取」")
            return
        day = self.selected_annihilation_day()
        if day is None:
            self.anni_status.setText("剿灭刷取：未选择刷取日 → 暂不执行")
            return
        action = "勾选" if today == day else "取消勾选"
        self.anni_status.setText(
            f"刷取日：{WEEKDAY_NAMES[day]}　本周进度：{self._annihilation_progress()}\n"
            f"今天 {WEEKDAY_NAMES[today]} → 自动{action}「剿灭刷取」"
        )

    def _annihilation_progress(self) -> str:
        try:
            weekly_cfg = self.config.get("weekly", {}) or {}
            cfg = weekly_cfg.get("annihilation") or {}
            state = WeeklyState(weekly_cfg.get("state_file", "logs/weekly_state.json"))
            anni = state.annihilation()
            cap = int(cfg.get("cap", 1800))
            progress = int(anni.get("progress", 0))
            if anni.get("done") or progress >= cap:
                return f"已完成（剿灭模式 {progress}/{cap}）"
            return f"剿灭模式 {progress}/{cap}"
        except Exception:
            return "未知"

    def update_monthly_status(self) -> None:
        monthly_cfg = self.config.get("monthly", {}) or {}
        try:
            state = MonthlyState(monthly_cfg.get("state_file", "logs/monthly_state.json"))
        except Exception:
            state = None
        for key, label in (("green", "绿票商店"), ("yellow", "黄票商店")):
            section = self.monthly_sections.get(key)
            status = self.monthly_status.get(key)
            if section is None or status is None:
                continue
            if not section.is_enabled():
                status.setText(f"{label}：已停用 → 本月不购买")
            elif state is not None and state.is_done(key):
                status.setText(f"{label}：本月已完成购买")
            else:
                status.setText(f"{label}：本月尚未购买 → 下次任务自动购买")

    def on_option_changed(self) -> None:
        self.save_timer.start()

    def save_settings(self) -> None:
        weekly = self.config.setdefault("weekly", {})
        weekly["enabled"] = True
        potion = weekly.setdefault("potion", {})
        potion["enabled"] = self.potion_section.is_enabled()
        potion["days"] = self.selected_days()
        anni = weekly.setdefault("annihilation", {})
        anni["enabled"] = self.anni_section.is_enabled()
        anni["day"] = self.selected_annihilation_day()
        monthly = self.config.setdefault("monthly", {})
        monthly["enabled"] = True
        for key in ("green", "yellow"):
            monthly.setdefault(key, {})["enabled"] = self.monthly_sections[key].is_enabled()
        monthly.setdefault("state_file", "logs/monthly_state.json")
        workflow = self.config.setdefault("workflow", {})
        workflow["auto_close"] = self.auto_close_switch.isChecked()
        workflow["chain"] = self._collect_chain()
        try:
            target = self._config_write_path()
            with open(target, "w", encoding="utf-8") as f:
                yaml.safe_dump(self.config, f, allow_unicode=True, sort_keys=False)
            logger.info(
                "设置已保存: 体力药刷取={} 使用日={} | 剿灭刷取={} 刷取日={} | "
                "绿票商店={} 黄票商店={} | 完成后自动关闭={} | 工作流定时={} 方式={}",
                potion["enabled"],
                potion["days"],
                anni["enabled"],
                anni["day"],
                monthly["green"]["enabled"],
                monthly["yellow"]["enabled"],
                workflow["auto_close"],
                workflow["chain"]["enabled"],
                workflow["chain"]["mode"],
            )
        except Exception as e:
            logger.error("保存周常设置失败: {}", e)

    def _config_write_path(self) -> Path:
        target = app_base_dir() / "config" / "config.yaml"
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def append_log(self, text: str) -> None:
        self.log_view.appendPlainText(text)

    def start_daily(self) -> None:
        if self.worker and self.worker.isRunning():
            self.request_stop()
            return
        if self.selected_software == WORKFLOW_KEY:
            self.start_chain()
            return
        software = self.selected_software or "maa"
        if not self._software_enabled(software):
            name = SOFTWARE_META.get(software, (software, ""))[0]
            logger.warning("{} 未启用，无法开始", name)
            self.status_label.setText(f"状态: {name} 未启用")
            return
        self.stop_event = threading.Event()
        self.btn_start.setEnabled(True)
        self.btn_start.setText("停止日常")
        self._set_button_role(self.btn_start, "Danger")
        self.status_label.setText("状态: 运行中...")
        self.report_view.clear()
        self.worker = DailyWorker(self.config, self.stop_event, software)
        self.worker.status.connect(self.status_label.setText)
        self.worker.finished_report.connect(self.on_report)
        self.worker.finished.connect(self.on_worker_finished)
        self.worker.start()

    def start_chain(self, software_list: list[str] | None = None) -> None:
        if self.worker and self.worker.isRunning():
            logger.warning("已有任务在运行，忽略本次工作流启动")
            return
        if software_list is None:
            software_list = self.seq_list.enabled_softwares()
        runnable = [s for s in software_list if self._software_enabled(s)]
        for skipped in (s for s in software_list if s not in runnable):
            logger.warning("{} 未启用，已跳过", SOFTWARE_META.get(skipped, (skipped, ""))[0])
        if not runnable:
            logger.warning("日常工作流没有可执行的任务")
            self.status_label.setText("状态: 工作流无可用任务")
            return
        self.stop_event = threading.Event()
        self.btn_start.setEnabled(True)
        self.btn_start.setText("停止日常")
        self._set_button_role(self.btn_start, "Danger")
        self.status_label.setText("状态: 工作流运行中...")
        self.report_view.clear()
        logger.info("开始日常工作流: {}", " → ".join(runnable))
        self.worker = ChainWorker(self.config, self.stop_event, runnable)
        self.worker.status.connect(self.status_label.setText)
        self.worker.finished_report.connect(self.on_report)
        self.worker.finished.connect(self.on_worker_finished)
        self.worker.start()

    def _software_enabled(self, key: str) -> bool:
        return bool((self.config.get("adapters", {}) or {}).get(key, {}).get("enabled", False))

    def request_stop(self) -> None:
        if self.stop_event is not None:
            self.stop_event.set()
        self.btn_start.setEnabled(False)
        self.btn_start.setText("停止中...")
        self.status_label.setText("状态: 正在停止...")
        logger.warning("已请求停止日常，正在中止当前工作流")

    @staticmethod
    def _set_button_role(button: QPushButton, role: str) -> None:
        button.setObjectName(role)
        button.style().unpolish(button)
        button.style().polish(button)

    def on_report(self, report) -> None:
        text = report.to_text()
        existing = self.report_view.toPlainText()
        self.report_view.setPlainText(f"{existing}\n\n{text}" if existing else text)
        self.report_view.verticalScrollBar().setValue(
            self.report_view.verticalScrollBar().maximum()
        )

    def copy_report(self) -> None:
        text = self.report_view.toPlainText()
        if not text:
            logger.info("暂无报告可复制")
            return
        QApplication.clipboard().setText(text)
        logger.info("报告已复制到剪贴板")

    def on_worker_finished(self) -> None:
        self.stop_event = None
        self.btn_start.setEnabled(True)
        self.btn_start.setText("开始日常")
        self._set_button_role(self.btn_start, "Primary")
        self.status_label.setText("状态: 空闲")

    def do_close_all(self) -> None:
        emu = self.config.get("adapters", {}).get("maa", {}).get("emulator", {})
        close_all(emu)
        self.status_label.setText("状态: 已关闭 MAA 与模拟器")

    def open_config_dir(self) -> None:
        subprocess.Popen(["explorer", str(self.config_path.parent)])

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.schedule_timer.isActive():
            self.schedule_timer.stop()
        if self.save_timer.isActive():
            self.save_timer.stop()
            self.save_settings()
        if self.worker and self.worker.isRunning():
            reply = QMessageBox.question(
                self, "确认退出", "日常任务仍在运行，确定要退出吗？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                event.ignore()
                return
        event.accept()


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(LIGHT_QSS)
    icon = QIcon(str(asset_path("icon.ico")))
    app.setWindowIcon(icon)

    config_path = resolve_config_path()
    config = load_config(config_path)
    setup_logger(log_dir=str(app_base_dir() / "logs"))

    bridge = LogBridge()
    logger.add(
        lambda m: bridge.message.emit(str(m).rstrip()),
        level="INFO",
        colorize=False,
        format="{time:HH:mm:ss} | {level: <7} | {message}",
    )
    logger.info("maagent 启动，配置: {}", config_path)

    win = MaAgentWindow(config, config_path, bridge)
    win.setWindowIcon(icon)
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
