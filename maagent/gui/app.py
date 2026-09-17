from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path

import yaml
from loguru import logger
from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from maagent.control.process import close_all
from maagent.control.weekly import WEEKDAY_NAMES
from maagent.core.orchestrator import Orchestrator
from maagent.log.logger import setup_logger

SOFTWARE_META: dict[str, tuple[str, str]] = {
    "maa": ("MAA", "明日方舟"),
    "maaend": ("MaaEnd", "明日方舟：终末地"),
    "bgi": ("BetterGI", "原神"),
}

LIGHT_QSS = """
QMainWindow, QWidget#Central { background: #ffffff; }
QLabel { color: #1f2328; }
QLabel#SectionTitle { color: #6b7280; font-size: 12px; font-weight: 600; }
QLabel#StatusLabel { color: #57606a; font-size: 13px; }

QFrame#Card { background: #ffffff; border: 1px solid #e5e7eb; border-radius: 10px; }
QFrame#Card:hover { border-color: #c7d2fe; background: #f8faff; }
QFrame#Card[selected="true"] { border: 2px solid #4f46e5; background: #eef2ff; }
QFrame#Card[enabled="false"] { background: #fafafa; border-color: #eceff3; }
QLabel#CardName { font-size: 15px; font-weight: 700; color: #111827; }
QLabel#CardDesc { font-size: 12px; color: #6b7280; }
QLabel#CardState { font-size: 11px; }

QGroupBox { border: 1px solid #e5e7eb; border-radius: 10px; margin-top: 14px; color: #6b7280; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px; font-weight: 600; }

QFrame#FutureArea { background: #fcfcfd; border: 1px dashed #d8dee6; border-radius: 10px; }
QLabel#Hint { color: #6b7280; font-size: 12px; }
QToolButton#SectionHeader { border: none; background: transparent; color: #111827; font-size: 13px; font-weight: 600; padding: 3px 0; }
QToolButton#SectionHeader:hover { color: #4f46e5; }
QCheckBox { color: #1f2328; spacing: 6px; }
QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid #cbd5e1; border-radius: 4px; background: #ffffff; }
QCheckBox::indicator:hover { border-color: #a5b4fc; }
QCheckBox::indicator:checked { background: #4f46e5; border-color: #4f46e5; }

QPlainTextEdit { background: #fbfbfd; border: 1px solid #e5e7eb; border-radius: 8px; padding: 6px; color: #1f2328; }

QPushButton { background: #ffffff; border: 1px solid #d0d7de; border-radius: 8px; padding: 8px 16px; color: #1f2328; }
QPushButton:hover { background: #f6f8fa; }
QPushButton:pressed { background: #eef1f4; }
QPushButton:disabled { color: #9ca3af; background: #f6f7f9; }
QPushButton#Primary { background: #4f46e5; border-color: #4f46e5; color: #ffffff; font-weight: 600; }
QPushButton#Primary:hover { background: #4338ca; }
QPushButton#Primary:disabled { background: #c7cbf5; border-color: #c7cbf5; color: #ffffff; }
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
        self.setFixedSize(176, 104)
        self.setCursor(Qt.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(4)

        top = QHBoxLayout()
        top.setSpacing(6)
        name_label = QLabel(name)
        name_label.setObjectName("CardName")
        self.dot = QLabel("●")
        self.dot.setStyleSheet("color: #22c55e;" if enabled else "color: #d1d5db;")
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
        self.state_label.setStyleSheet("color: #22c55e;" if enabled else "color: #9ca3af;")
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


class CollapsibleSection(QWidget):
    """A click-to-expand section (accordion item) for the weekly dashboard."""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.header = QToolButton()
        self.header.setObjectName("SectionHeader")
        self.header.setText(title)
        self.header.setCheckable(True)
        self.header.setChecked(True)
        self.header.setArrowType(Qt.DownArrow)
        self.header.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.header.setCursor(Qt.PointingHandCursor)
        self.header.toggled.connect(self._on_toggled)
        layout.addWidget(self.header)

        self.body = QWidget()
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(20, 0, 0, 4)
        self.body_layout.setSpacing(8)
        layout.addWidget(self.body)

    def _on_toggled(self, checked: bool) -> None:
        self.header.setArrowType(Qt.DownArrow if checked else Qt.RightArrow)
        self.body.setVisible(checked)

    def add_widget(self, widget: QWidget) -> None:
        self.body_layout.addWidget(widget)

    def add_layout(self, layout) -> None:
        self.body_layout.addLayout(layout)


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
        self.weekly_save_timer = QTimer(self)
        self.weekly_save_timer.setSingleShot(True)
        self.weekly_save_timer.setInterval(5000)
        self.weekly_save_timer.timeout.connect(self.save_weekly)
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

    def _build_ui(self) -> None:
        self.setWindowTitle("maagent - 二游日常助手")
        self.resize(1080, 720)

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

        self.status_label = QLabel("状态: 空闲")
        self.status_label.setObjectName("StatusLabel")
        layout.addWidget(self.status_label)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        self.btn_start = QPushButton("开始日常")
        self.btn_start.setObjectName("Primary")
        self.btn_start.clicked.connect(self.start_daily)
        self.btn_close = QPushButton("关闭 MAA / 模拟器")
        self.btn_close.clicked.connect(self.do_close_all)
        self.btn_config = QPushButton("打开配置目录")
        self.btn_config.clicked.connect(self.open_config_dir)
        for b in (self.btn_start, self.btn_close, self.btn_config):
            btn_row.addWidget(b)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        body = QHBoxLayout()
        body.setSpacing(12)

        left_col = QVBoxLayout()
        left_col.setSpacing(10)

        weekly_box = QGroupBox("周常")
        weekly_layout = QVBoxLayout(weekly_box)
        weekly_layout.setSpacing(6)

        potion_section = CollapsibleSection("体力药使用")
        potion_hint = QLabel("勾选需要使用体力药的日子；其余日子会自动关闭 MAA 的「使用药剂」，避免浪费。")
        potion_hint.setObjectName("Hint")
        potion_hint.setWordWrap(True)
        potion_section.add_widget(potion_hint)

        day_grid = QGridLayout()
        day_grid.setSpacing(10)
        self.day_checks: list[QCheckBox] = []
        selected_days = set(self.config.get("weekly", {}).get("use_potion_days") or [])
        for i, name in enumerate(WEEKDAY_NAMES):
            cb = QCheckBox(name)
            cb.setChecked(i in selected_days)
            cb.stateChanged.connect(self.on_weekly_changed)
            self.day_checks.append(cb)
            day_grid.addWidget(cb, i // 4, i % 4)
        potion_section.add_layout(day_grid)

        self.weekly_status = QLabel()
        self.weekly_status.setObjectName("Hint")
        self.weekly_status.setWordWrap(True)
        potion_section.add_widget(self.weekly_status)

        weekly_layout.addWidget(potion_section)
        weekly_layout.addStretch(1)
        self.update_weekly_status()

        left_col.addWidget(weekly_box)
        left_col.addStretch(1)
        body.addLayout(left_col, 3)

        right_col = QVBoxLayout()
        right_col.setSpacing(10)

        log_box = QGroupBox("运行日志")
        log_layout = QVBoxLayout(log_box)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        log_layout.addWidget(self.log_view)
        right_col.addWidget(log_box, 3)

        rep_box = QGroupBox("任务报告")
        rep_layout = QVBoxLayout(rep_box)
        self.report_view = QPlainTextEdit()
        self.report_view.setReadOnly(True)
        rep_layout.addWidget(self.report_view)
        right_col.addWidget(rep_box, 2)

        body.addLayout(right_col, 2)
        layout.addLayout(body, 1)

        self.setCentralWidget(central)

        default = next((k for k in ("maa", "maaend", "bgi") if k in self.cards), None)
        if default:
            self.select_software(default)

    def select_software(self, key: str) -> None:
        self.selected_software = key
        for card_key, card in self.cards.items():
            card.set_selected(card_key == key)

    def selected_days(self) -> list[int]:
        return [i for i, cb in enumerate(self.day_checks) if cb.isChecked()]

    def update_weekly_status(self) -> None:
        today = datetime.now().weekday()
        days = self.selected_days()
        names = "、".join(WEEKDAY_NAMES[i] for i in days) or "（未选择）"
        action = "开启" if today in days else "关闭"
        self.weekly_status.setText(
            f"使用体力药：{names}\n今天 {WEEKDAY_NAMES[today]} → 自动{action}「使用药剂」"
        )

    def on_weekly_changed(self) -> None:
        self.update_weekly_status()
        self.weekly_save_timer.start()

    def save_weekly(self) -> None:
        weekly = self.config.setdefault("weekly", {})
        weekly["enabled"] = True
        weekly["use_potion_days"] = self.selected_days()
        try:
            target = self._config_write_path()
            with open(target, "w", encoding="utf-8") as f:
                yaml.safe_dump(self.config, f, allow_unicode=True, sort_keys=False)
            logger.info("周常设置已保存: 使用体力药日 {}", weekly["use_potion_days"])
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
        if self.weekly_save_timer.isActive():
            self.weekly_save_timer.stop()
            self.save_weekly()
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
