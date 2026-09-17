from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path

import yaml
from loguru import logger
from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QObject,
    QPropertyAnimation,
    Qt,
    QThread,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QIcon, QPainter
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
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from maagent.control.monthly import MonthlyState
from maagent.control.process import close_all
from maagent.control.weekly import WEEKDAY_NAMES, WeeklyState
from maagent.core.orchestrator import Orchestrator
from maagent.log.logger import setup_logger

SOFTWARE_META: dict[str, tuple[str, str]] = {
    "maa": ("MAA", "明日方舟"),
    "maaend": ("MaaEnd", "明日方舟：终末地"),
    "bgi": ("BetterGI", "原神"),
}

LIGHT_QSS = """
QWidget { font-size: 14px; }
QMainWindow, QWidget#Central { background: #ffffff; }
QLabel { color: #1f2328; }
QLabel#SectionTitle { color: #6b7280; font-size: 13px; font-weight: 600; }
QLabel#StatusLabel { color: #57606a; font-size: 14px; }

QFrame#Card { background: #ffffff; border: 1px solid #e5e7eb; border-radius: 10px; }
QFrame#Card:hover { border-color: #c7d2fe; background: #f8faff; }
QFrame#Card[selected="true"] { border: 2px solid #4f46e5; background: #eef2ff; }
QFrame#Card[enabled="false"] { background: #fafafa; border-color: #eceff3; }
QLabel#CardName { font-size: 16px; font-weight: 700; color: #111827; }
QLabel#CardDesc { font-size: 13px; color: #6b7280; }
QLabel#CardState { font-size: 12px; }

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

QPlainTextEdit { background: #fbfbfd; border: 1px solid #e5e7eb; border-radius: 8px; padding: 6px; color: #1f2328; }

QScrollArea#WeeklyScroll { background: transparent; border: none; }
QWidget#ScrollInner { background: transparent; }
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
QPushButton#Ghost { background: transparent; border: none; color: #6b7280; padding: 4px 10px; border-radius: 6px; font-size: 13px; }
QPushButton#Ghost:hover { background: #f3f4f6; color: #4f46e5; }
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


def load_config(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


class LogBridge(QObject):
    message = Signal(str)


class SoftwareCard(QFrame):
    clicked = Signal(str)

    def __init__(self, key: str, name: str, desc: str, enabled: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.key = key
        self._enabled = enabled
        self._selected = False
        self.setObjectName("Card")
        self.setFixedSize(190, 112)
        self.setCursor(Qt.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(4)

        top = QHBoxLayout()
        top.setSpacing(6)
        name_label = QLabel(name)
        name_label.setObjectName("CardName")
        self.dot = QLabel("●")
        self.dot.setStyleSheet("color: #4f46e5;" if enabled else "color: #d1d5db;")
        top.addWidget(name_label)
        top.addStretch(1)
        top.addWidget(self.dot)
        layout.addLayout(top)

        desc_label = QLabel(desc)
        desc_label.setObjectName("CardDesc")
        desc_label.setWordWrap(True)
        layout.addWidget(desc_label)

        layout.addStretch(1)
        self.state_label = QLabel("已启用" if enabled else "未启用")
        self.state_label.setObjectName("CardState")
        self.state_label.setStyleSheet("color: #4f46e5;" if enabled else "color: #9ca3af;")
        layout.addWidget(self.state_label)

        self.setProperty("enabled", enabled)
        self.setProperty("selected", False)

    def set_selected(self, value: bool) -> None:
        self._selected = value
        self.setProperty("selected", value)
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self.clicked.emit(self.key)
        super().mousePressEvent(event)


class ToggleSwitch(QWidget):
    """An iOS-style on/off switch (pill track + sliding knob)."""

    toggled = Signal(bool)

    def __init__(
        self,
        checked: bool = False,
        width: int = 48,
        height: int = 26,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._checked = bool(checked)
        self._pos = 1.0 if self._checked else 0.0
        self.setFixedSize(width, height)
        self.setCursor(Qt.PointingHandCursor)
        self._anim = QPropertyAnimation(self, b"position", self)
        self._anim.setDuration(140)
        self._anim.setEasingCurve(QEasingCurve.InOutQuad)

    def isChecked(self) -> bool:  # noqa: N802
        return self._checked

    def setChecked(self, value: bool) -> None:  # noqa: N802
        value = bool(value)
        if value == self._checked:
            return
        self._checked = value
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if value else 0.0)
        self._anim.start()
        self.toggled.emit(value)

    def _get_position(self) -> float:
        return self._pos

    def _set_position(self, value: float) -> None:
        self._pos = value
        self.update()

    position = Property(float, _get_position, _set_position)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.setChecked(not self._checked)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        radius = h / 2
        off = QColor("#d0d7de")
        on = QColor("#4f46e5")
        track = QColor(
            int(off.red() + (on.red() - off.red()) * self._pos),
            int(off.green() + (on.green() - off.green()) * self._pos),
            int(off.blue() + (on.blue() - off.blue()) * self._pos),
        )
        painter.setPen(Qt.NoPen)
        painter.setBrush(track)
        painter.drawRoundedRect(0, 0, w, h, radius, radius)
        margin = max(2, round(h * 0.12))
        d = h - margin * 2
        x = margin + (w - d - margin * 2) * self._pos
        painter.setBrush(QColor("#ffffff"))
        painter.drawEllipse(int(x), margin, d, d)


class CollapsibleSection(QWidget):
    """A click-to-expand section (accordion item) with an enable switch."""

    def __init__(self, title: str, enabled: bool = True, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(4)

        self.header = QToolButton()
        self.header.setObjectName("SectionHeader")
        self.header.setText(title)
        self.header.setCheckable(True)
        self.header.setChecked(True)
        self.header.setArrowType(Qt.DownArrow)
        self.header.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.header.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.header.setCursor(Qt.PointingHandCursor)
        self.header.toggled.connect(self._on_toggled)
        header_row.addWidget(self.header, 1)

        enable_label = QLabel("启用")
        enable_label.setObjectName("ToggleLabel")
        header_row.addWidget(enable_label, 0)
        self.enable_switch = ToggleSwitch(enabled)
        header_row.addWidget(self.enable_switch, 0)
        layout.addLayout(header_row)

        self.body = QWidget()
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(20, 0, 0, 4)
        self.body_layout.setSpacing(8)
        layout.addWidget(self.body)

    def is_enabled(self) -> bool:
        return self.enable_switch.isChecked()

    def _on_toggled(self, checked: bool) -> None:
        self.header.setArrowType(Qt.DownArrow if checked else Qt.RightArrow)
        self.body.setVisible(checked)

    def add_widget(self, widget: QWidget) -> None:
        self.body_layout.addWidget(widget)

    def add_layout(self, layout) -> None:
        self.body_layout.addLayout(layout)


class Panel(QFrame):
    """A card-style panel with a title row that can host header actions."""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Panel")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 14)
        outer.setSpacing(10)

        self.header = QHBoxLayout()
        self.header.setSpacing(6)
        title_label = QLabel(title)
        title_label.setObjectName("PanelTitle")
        self.header.addWidget(title_label)
        self.header.addStretch(1)
        outer.addLayout(self.header)

        self.body = QVBoxLayout()
        self.body.setSpacing(8)
        outer.addLayout(self.body, 1)

    def add_action(self, widget: QWidget) -> None:
        self.header.addWidget(widget)

    def add_widget(self, widget: QWidget, stretch: int = 0) -> None:
        self.body.addWidget(widget, stretch)


class DailyWorker(QThread):
    status = Signal(str)
    finished_report = Signal(object)

    def __init__(self, config: dict) -> None:
        super().__init__()
        self.config = config

    def run(self) -> None:
        try:
            report = Orchestrator(self.config).run_daily()
            self.finished_report.emit(report)
        except Exception as e:
            logger.exception("工作流异常: {}", e)
            self.status.emit(f"状态: 异常 - {e}")


class MaAgentWindow(QMainWindow):
    def __init__(self, config: dict, config_path: Path, bridge: LogBridge) -> None:
        super().__init__()
        self.config = config
        self.config_path = config_path
        self.bridge = bridge
        self.worker: DailyWorker | None = None
        self.cards: dict[str, SoftwareCard] = {}
        self.selected_software: str | None = None
        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.setInterval(5000)
        self.save_timer.timeout.connect(self.save_settings)
        self._build_ui()
        self.bridge.message.connect(self.append_log)

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
        self.resize(1152, 648)
        self.setMinimumSize(1024, 576)

        central = QWidget()
        central.setObjectName("Central")
        layout = QVBoxLayout(central)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        section = QLabel("可调用软件")
        section.setObjectName("SectionTitle")
        layout.addWidget(section)

        card_row = QHBoxLayout()
        card_row.setSpacing(12)
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

        weekly_panel = Panel("周常")
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

        monthly_panel = Panel("月常")
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

        body.addLayout(left_col, 3)
        self.update_weekly_status()
        self.update_annihilation_status()
        self.update_monthly_status()

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

        body.addLayout(right_col, 2)
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

        default = next((k for k in ("maa", "maaend", "bgi") if k in self.cards), None)
        if default:
            self.select_software(default)

    def select_software(self, key: str) -> None:
        self.selected_software = key
        for card_key, card in self.cards.items():
            card.set_selected(card_key == key)

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
        try:
            target = self._config_write_path()
            with open(target, "w", encoding="utf-8") as f:
                yaml.safe_dump(self.config, f, allow_unicode=True, sort_keys=False)
            logger.info(
                "设置已保存: 体力药刷取={} 使用日={} | 剿灭刷取={} 刷取日={} | "
                "绿票商店={} 黄票商店={} | 完成后自动关闭={}",
                potion["enabled"],
                potion["days"],
                anni["enabled"],
                anni["day"],
                monthly["green"]["enabled"],
                monthly["yellow"]["enabled"],
                workflow["auto_close"],
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
            return
        self.btn_start.setEnabled(False)
        self.status_label.setText("状态: 运行中...")
        self.report_view.clear()
        self.worker = DailyWorker(self.config)
        self.worker.status.connect(self.status_label.setText)
        self.worker.finished_report.connect(self.on_report)
        self.worker.finished.connect(self.on_worker_finished)
        self.worker.start()

    def on_report(self, report) -> None:
        self.report_view.setPlainText(report.to_text())

    def copy_report(self) -> None:
        text = self.report_view.toPlainText()
        if not text:
            logger.info("暂无报告可复制")
            return
        QApplication.clipboard().setText(text)
        logger.info("报告已复制到剪贴板")

    def on_worker_finished(self) -> None:
        self.btn_start.setEnabled(True)
        self.status_label.setText("状态: 空闲")

    def do_close_all(self) -> None:
        emu = self.config.get("adapters", {}).get("maa", {}).get("emulator", {})
        close_all(emu)
        self.status_label.setText("状态: 已关闭 MAA 与模拟器")

    def open_config_dir(self) -> None:
        subprocess.Popen(["explorer", str(self.config_path.parent)])

    def closeEvent(self, event) -> None:  # noqa: N802
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
