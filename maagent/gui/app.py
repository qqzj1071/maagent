from __future__ import annotations

import os
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

import yaml
from loguru import logger
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from maagent.config import load_config
from maagent.control.autostart import set_enabled as set_autostart
from maagent.control.monthly import MonthlyState
from maagent.control.process import close_all
from maagent.control.game_time import game_weekday
from maagent.control.weekly import WEEKDAY_NAMES, WeeklyState
from maagent.core import workflow_config as wc
from maagent.core.controller import WorkflowController
from maagent.core.scheduler import Scheduler
from maagent.gui.config_log import config_snapshot, log_config_changes
from maagent.gui.controller_bridge import ControllerBridge
from maagent.gui.constants import (
    DEFAULT_HOTKEYS,
    SOFTWARE_META,
    WORKFLOW_DESC,
    WORKFLOW_KEY,
    WORKFLOW_NAME,
    software_meta,
)
from maagent.gui.hotkeys import GlobalHotkeys
from maagent.gui.settings import SettingsPage
from maagent.gui.widgets import (
    CollapsibleSection,
    LogBridge,
    Panel,
    SoftwareCard,
    ToggleSwitch,
    set_button_role,
)
from maagent.gui.workflow import WorkflowPanel
from maagent.i18n import DEFAULT_LANGUAGE, set_language, t
from maagent.log.logger import setup_logger

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
QProgressBar { border: none; border-radius: 5px; background: #e5e7eb; }
QProgressBar::chunk { border-radius: 5px; background: #4f46e5; }
QLineEdit { background: #f6f7fb; border: 1px solid #e5e7eb; border-radius: 10px; padding: 9px 12px; color: #1f2328; selection-background-color: #c7d2fe; }
QLineEdit:hover { border-color: #c7d2fe; }
QLineEdit:focus { border-color: #4f46e5; background: #ffffff; }

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

QDialog, QWidget#SettingsPage { background: #ffffff; }
QFrame#SettingsSidebar { background: #f8fafc; border-right: 1px solid #e5e7eb; }
QLabel#SettingsBrand { color: #4f46e5; font-size: 22px; font-weight: 800; }
QLabel#SettingsBrandSub { color: #9ca3af; font-size: 12px; }
QPushButton#SettingsNav { text-align: left; padding: 10px 14px; border: none; border-radius: 8px; color: #374151; font-size: 14px; background: transparent; }
QPushButton#SettingsNav:hover { background: #eef2ff; color: #4f46e5; }
QPushButton#SettingsNav:checked { background: #4f46e5; color: #ffffff; font-weight: 600; }
QLabel#SettingsPageTitle { font-size: 22px; font-weight: 800; color: #111827; }
QLabel#SettingsPageDesc { font-size: 13px; color: #6b7280; }
QLabel#SettingsAboutTitle { font-size: 18px; font-weight: 700; color: #111827; }
QFrame#SettingsCard { background: #ffffff; border: 1px solid #e5e7eb; border-radius: 12px; }
QScrollArea#SettingsScroll, QScrollArea#SettingsScroll > QWidget, QScrollArea#SettingsScroll > QWidget > QWidget { background: transparent; border: none; }
QWidget#SettingsPage QStackedWidget { background: transparent; }
QLabel#SettingsField { font-size: 14px; color: #374151; }
QLabel#SettingsSectionTitle { font-size: 14px; font-weight: 700; color: #111827; }
QPushButton#ChoiceButton { background: #f3f4f6; border: 1px solid transparent; border-radius: 10px; color: #374151; font-size: 14px; padding: 10px; }
QPushButton#ChoiceButton:hover { background: #eef2ff; border-color: #c7d2fe; }
QPushButton#ChoiceButton:checked { background: #4f46e5; border-color: #4f46e5; color: #ffffff; font-weight: 600; }
"""

DARK_QSS = """
QWidget { font-size: 14px; }
QMainWindow, QWidget#Central { background: #1e1e24; }
QDialog, QWidget#SettingsPage { background: #1e1e24; }
QLabel { color: #e5e7eb; }
QLabel#SectionTitle { color: #9ca3af; font-size: 13px; font-weight: 600; }
QLabel#StatusLabel { color: #cbd5e1; font-size: 14px; }

QFrame#Card { background: #26262e; border: 1px solid #3a3a45; border-radius: 10px; }
QFrame#Card:hover { border-color: #6366f1; background: #2c2c3a; }
QFrame#Card[selected="true"] { border: 2px solid #6366f1; background: #2f2f45; }
QFrame#Card[inactive="true"] { background: #22222a; border-color: #33333d; }
QLabel#CardName { font-size: 15px; font-weight: 700; color: #f3f4f6; }
QLabel#CardDesc { font-size: 12px; color: #9ca3af; }
QLabel#CardState { font-size: 11px; }

QFrame#Panel { background: #26262e; border: 1px solid #3a3a45; border-radius: 10px; }
QLabel#PanelTitle { color: #e5e7eb; font-size: 14px; font-weight: 600; }

QFrame#FutureArea { background: #202027; border: 1px dashed #3a3a45; border-radius: 10px; }
QLabel#Hint { color: #9ca3af; font-size: 13px; }
QToolButton#SectionHeader { border: none; background: transparent; color: #e5e7eb; font-size: 14px; font-weight: 600; padding: 6px 8px; text-align: left; }
QToolButton#SectionHeader:hover { background: #2f2f3a; border-radius: 6px; color: #a5b4fc; }
QLabel#ToggleLabel { color: #9ca3af; font-size: 13px; }
QLabel#OptionLabel { color: #9ca3af; font-size: 12px; }

QPushButton#DayChip { background: #2a2a33; border: 1px solid #45454f; color: #d1d5db; padding: 7px 0; border-radius: 8px; font-size: 14px; }
QPushButton#DayChip:hover { border-color: #6366f1; background: #34343f; }
QPushButton#DayChip:checked { background: #6366f1; border-color: #6366f1; color: #ffffff; font-weight: 600; }
QPushButton#ModeChip { background: #2a2a33; border: 1px solid #45454f; color: #d1d5db; padding: 6px 14px; border-radius: 8px; font-size: 13px; }
QPushButton#ModeChip:hover { border-color: #6366f1; background: #34343f; }
QPushButton#ModeChip:checked { background: #6366f1; border-color: #6366f1; color: #ffffff; font-weight: 600; }

QFrame#TaskRow { background: #2a2a33; border: 1px solid #3a3a45; border-radius: 8px; }
QFrame#TaskRow[dragging="true"] { background: #2f2f45; border: 1px solid #6366f1; }
QFrame#DropIndicator { background: #6366f1; border-radius: 1px; }
QLabel#TaskOrder { color: #a5b4fc; font-weight: 700; font-size: 13px; }
QLabel#DragHandle { color: #6b7280; font-size: 16px; font-weight: 700; }
QLabel#DragHandle:hover { color: #a5b4fc; }
QTimeEdit { background: #2a2a33; border: 1px solid #45454f; border-radius: 6px; padding: 3px 6px; color: #e5e7eb; }
QTimeEdit:hover { border-color: #6366f1; }

QPlainTextEdit { background: #1b1b21; border: 1px solid #3a3a45; border-radius: 8px; padding: 6px; color: #e5e7eb; }
QProgressBar { border: none; border-radius: 5px; background: #2a2a33; }
QProgressBar::chunk { border-radius: 5px; background: #6366f1; }
QLineEdit { background: #1b1b21; border: 1px solid #3a3a45; border-radius: 10px; padding: 9px 12px; color: #e5e7eb; selection-background-color: #4338ca; }
QLineEdit:hover { border-color: #6366f1; }
QLineEdit:focus { border-color: #6366f1; background: #23232b; }

QScrollArea#WeeklyScroll { background: transparent; border: none; }
QWidget#ScrollInner { background: transparent; }
QWidget#LeftPanel { background: transparent; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: #45454f; border-radius: 5px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: #6366f1; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }

QPushButton { background: #2a2a33; border: 1px solid #45454f; border-radius: 8px; padding: 8px 16px; color: #e5e7eb; }
QPushButton:hover { background: #34343f; }
QPushButton:pressed { background: #3d3d49; }
QPushButton:disabled { color: #6b7280; background: #26262e; }
QPushButton#Primary { background: #6366f1; border-color: #6366f1; color: #ffffff; font-weight: 600; }
QPushButton#Primary:hover { background: #4f46e5; }
QPushButton#Primary:disabled { background: #3d3d55; border-color: #3d3d55; color: #9ca3af; }
QPushButton#Danger { background: #dc2626; border-color: #dc2626; color: #ffffff; font-weight: 600; }
QPushButton#Danger:hover { background: #b91c1c; }
QPushButton#Danger:disabled { background: #5a2a2a; border-color: #5a2a2a; color: #9ca3af; }
QPushButton#Ghost { background: transparent; border: none; color: #9ca3af; padding: 4px 10px; border-radius: 6px; font-size: 13px; }
QPushButton#Ghost:hover { background: #2f2f3a; color: #a5b4fc; }
QPushButton#Ghost:disabled { color: #555b66; }

QFrame#SettingsSidebar { background: #202027; border-right: 1px solid #3a3a45; }
QLabel#SettingsBrand { color: #818cf8; font-size: 22px; font-weight: 800; }
QLabel#SettingsBrandSub { color: #6b7280; font-size: 12px; }
QPushButton#SettingsNav { text-align: left; padding: 10px 14px; border: none; border-radius: 8px; color: #cbd5e1; font-size: 14px; background: transparent; }
QPushButton#SettingsNav:hover { background: #2f2f3a; color: #a5b4fc; }
QPushButton#SettingsNav:checked { background: #6366f1; color: #ffffff; font-weight: 600; }
QLabel#SettingsPageTitle { font-size: 22px; font-weight: 800; color: #f3f4f6; }
QLabel#SettingsPageDesc { font-size: 13px; color: #9ca3af; }
QLabel#SettingsAboutTitle { font-size: 18px; font-weight: 700; color: #f3f4f6; }
QFrame#SettingsCard { background: #26262e; border: 1px solid #3a3a45; border-radius: 12px; }
QScrollArea#SettingsScroll, QScrollArea#SettingsScroll > QWidget, QScrollArea#SettingsScroll > QWidget > QWidget { background: transparent; border: none; }
QWidget#SettingsPage QStackedWidget { background: transparent; }
QLabel#SettingsField { font-size: 14px; color: #e5e7eb; }
QLabel#SettingsSectionTitle { font-size: 14px; font-weight: 700; color: #f3f4f6; }
QPushButton#ChoiceButton { background: #2a2a33; border: 1px solid #3a3a45; border-radius: 10px; color: #d1d5db; font-size: 14px; padding: 10px; }
QPushButton#ChoiceButton:hover { background: #34343f; border-color: #6366f1; }
QPushButton#ChoiceButton:checked { background: #6366f1; border-color: #6366f1; color: #ffffff; font-weight: 600; }
"""


def apply_theme(theme: str) -> None:
    app = QApplication.instance()
    if app is None:
        return
    if theme == "dark":
        palette = QPalette()
        palette.setColor(QPalette.Window, QColor("#1e1e24"))
        palette.setColor(QPalette.WindowText, QColor("#e5e7eb"))
        palette.setColor(QPalette.Base, QColor("#1b1b21"))
        palette.setColor(QPalette.AlternateBase, QColor("#26262e"))
        palette.setColor(QPalette.Text, QColor("#e5e7eb"))
        palette.setColor(QPalette.Button, QColor("#2a2a33"))
        palette.setColor(QPalette.ButtonText, QColor("#e5e7eb"))
        palette.setColor(QPalette.ToolTipBase, QColor("#2a2a33"))
        palette.setColor(QPalette.ToolTipText, QColor("#e5e7eb"))
        palette.setColor(QPalette.Highlight, QColor("#6366f1"))
        palette.setColor(QPalette.HighlightedText, QColor("#ffffff"))
        app.setPalette(palette)
        app.setStyleSheet(DARK_QSS)
    else:
        app.setPalette(app.style().standardPalette())
        app.setStyleSheet(LIGHT_QSS)


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


class MaAgentWindow(QMainWindow):
    def __init__(self, config: dict, config_path: Path, bridge: LogBridge) -> None:
        super().__init__()
        self.config = config
        self.config_path = config_path
        self.bridge = bridge
        self.controller = WorkflowController(
            config,
            (config.get("server", {}) or {}).get("report_file", "logs/reports.jsonl"),
        )
        self.controller_bridge = ControllerBridge(self.controller, self)
        self.controller_bridge.status_changed.connect(self._on_controller_status)
        self.controller_bridge.report_ready.connect(self._on_controller_report)
        self._server_httpd = None
        self._server_ctx = None
        self.cards: dict[str, SoftwareCard] = {}
        self.selected_software: str | None = None
        app_cfg = config.get("app", {}) or {}
        self.minimize_to_tray = bool(app_cfg.get("minimize_to_tray", True))
        self.theme = app_cfg.get("theme", "light")
        self._quitting = False
        self._tray_notice_shown = False
        self._warned_run: str | None = None
        self._tray_show_action = None
        self._tray_quit_action = None
        set_language(app_cfg.get("language", DEFAULT_LANGUAGE))
        self.hotkeys = GlobalHotkeys(self)
        self.hotkeys.triggered.connect(self._on_hotkey)

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
        self._last_saved = config_snapshot(self.config)
        if self._sync_account_name():
            self._update_account_chip()
            self.save_settings()
        self._setup_tray()
        self._register_hotkeys()
        self.bridge.message.connect(self.append_log)
        self.schedule_timer.start()
        self._start_embedded_server()
        self._ensure_phone_remote_ready()

    def _software_defs(self) -> list[tuple[str, str, str, bool]]:
        adapters = self.config.get("adapters", {}) or {}
        meta = software_meta()
        keys = list(meta.keys())
        keys += [k for k in adapters if k not in keys and k != "bgi"]
        defs = []
        for key in keys:
            name, desc = meta.get(key, (key.upper(), ""))
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
        self.setWindowTitle(t("app.title"))
        self.resize(1079, 667)
        self.setMinimumSize(940, 580)

        main_page = QWidget()
        main_page.setObjectName("Central")
        layout = QVBoxLayout(main_page)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(8)
        top_row.addStretch(1)
        self.account_chip = QPushButton()
        self.account_chip.setObjectName("Ghost")
        self.account_chip.setCursor(Qt.PointingHandCursor)
        self.account_chip.clicked.connect(self.open_account_settings)
        top_row.addWidget(self.account_chip)
        self.btn_settings = QPushButton(t("btn.settings"))
        self.btn_settings.setObjectName("Ghost")
        self.btn_settings.setCursor(Qt.PointingHandCursor)
        self.btn_settings.clicked.connect(self.open_settings)
        top_row.addWidget(self.btn_settings)
        layout.addLayout(top_row)
        self._update_account_chip()

        card_row = QHBoxLayout()
        card_row.setSpacing(10)
        chain_enabled = bool(
            (self.config.get("workflow", {}) or {}).get("chain", {}).get("enabled", False)
        )
        self.workflow_card = SoftwareCard(
            WORKFLOW_KEY, t(WORKFLOW_NAME), t(WORKFLOW_DESC), True
        )
        self.workflow_card.set_state_text(
            t("card.schedule_on") if chain_enabled else t("card.schedule_off"),
            active=chain_enabled,
        )
        self.workflow_card.clicked.connect(self.select_software)
        self.cards[WORKFLOW_KEY] = self.workflow_card
        card_row.addWidget(self.workflow_card)
        for key, name, desc, enabled in self._software_defs():
            card = SoftwareCard(key, name, desc, enabled, toggleable=key in wc.CHAIN_SOFTWARE)
            card.clicked.connect(self.select_software)
            card.enable_toggled.connect(self._on_card_enabled)
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

        self.weekly_panel = Panel(t("panel.weekly"))
        weekly_panel = self.weekly_panel
        potion_cfg = self.config.get("weekly", {}).get("potion") or {}
        potion_section = CollapsibleSection(
            t("weekly.potion"), enabled=bool(potion_cfg.get("enabled", False))
        )
        self.potion_section = potion_section
        potion_hint = QLabel(t("weekly.potion.hint"))
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
        anni_section = CollapsibleSection(
            t("weekly.annihilation"), enabled=bool(anni_cfg.get("enabled", False))
        )
        self.anni_section = anni_section
        anni_hint = QLabel(t("weekly.annihilation.hint"))
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

        self.monthly_panel = Panel(t("panel.monthly"))
        monthly_panel = self.monthly_panel
        self.monthly_sections: dict[str, CollapsibleSection] = {}
        self.monthly_status: dict[str, QLabel] = {}
        monthly_cfg = self.config.get("monthly", {}) or {}
        for key, title, hint in (
            ("green", t("monthly.green"), t("monthly.hint.green")),
            ("yellow", t("monthly.yellow"), t("monthly.hint.yellow")),
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

        self.workflow_panel = WorkflowPanel(self.config)
        self.workflow_panel.changed.connect(self.on_workflow_changed)
        self.workflow_panel.run_requested.connect(self.on_run_requested)
        inner.addWidget(self.workflow_panel)
        inner.addStretch(1)
        left_col.addWidget(scroll, 1)

        self.maa_auto_close_row = QWidget()
        auto_row = QHBoxLayout(self.maa_auto_close_row)
        auto_row.setContentsMargins(4, 0, 4, 0)
        auto_row.setSpacing(8)
        auto_label = QLabel(t("option.auto_close"))
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
        left_col.addWidget(self.maa_auto_close_row)

        self.left_panel = QWidget()
        self.left_panel.setObjectName("LeftPanel")
        self.left_panel.setLayout(left_col)
        body.addWidget(self.left_panel, 7)
        self.update_weekly_status()
        self.update_annihilation_status()
        self.update_monthly_status()
        self.workflow_panel.refresh_status(self.scheduler)

        right_col = QVBoxLayout()
        right_col.setSpacing(12)

        log_panel = Panel(t("panel.log"))
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setPlaceholderText(t("log.empty"))
        log_panel.add_widget(self.log_view, 1)
        btn_clear_log = QPushButton(t("btn.clear"))
        btn_clear_log.setObjectName("Ghost")
        btn_clear_log.setCursor(Qt.PointingHandCursor)
        btn_clear_log.clicked.connect(self.log_view.clear)
        log_panel.add_action(btn_clear_log)
        right_col.addWidget(log_panel, 3)

        report_panel = Panel(t("panel.report"))
        self.report_view = QPlainTextEdit()
        self.report_view.setReadOnly(True)
        self.report_view.setPlaceholderText(t("report.empty"))
        report_panel.add_widget(self.report_view, 1)
        btn_copy = QPushButton(t("btn.copy"))
        btn_copy.setObjectName("Ghost")
        btn_copy.setCursor(Qt.PointingHandCursor)
        btn_copy.clicked.connect(self.copy_report)
        report_panel.add_action(btn_copy)
        right_col.addWidget(report_panel, 2)

        body.addLayout(right_col, 3)
        layout.addLayout(body, 1)

        bottom = QHBoxLayout()
        bottom.setSpacing(10)
        self.status_label = QLabel(t("status.idle"))
        self.status_label.setObjectName("StatusLabel")
        bottom.addWidget(self.status_label)

        self.maaend_auto_close_row = QWidget()
        maaend_row_layout = QHBoxLayout(self.maaend_auto_close_row)
        maaend_row_layout.setContentsMargins(0, 0, 0, 0)
        maaend_row_layout.setSpacing(8)
        self.maaend_auto_close_label = QLabel(t("option.maaend_auto_close"))
        self.maaend_auto_close_label.setObjectName("OptionLabel")
        maaend_row_layout.addWidget(self.maaend_auto_close_label)
        self.maaend_auto_close_switch = ToggleSwitch(
            bool(
                (self.config.get("adapters", {}).get("maaend", {}) or {}).get(
                    "auto_close", False
                )
            ),
            width=38,
            height=20,
        )
        self.maaend_auto_close_switch.toggled.connect(self.on_option_changed)
        maaend_row_layout.addWidget(self.maaend_auto_close_switch)
        self.maaend_auto_close_row.setVisible(False)
        bottom.addWidget(self.maaend_auto_close_row)

        bottom.addStretch(1)
        self.btn_config = QPushButton(t("btn.open_config"))
        self.btn_config.clicked.connect(self.open_config_dir)
        self.btn_close = QPushButton(t("btn.close_all"))
        self.btn_close.clicked.connect(self.do_close_all)
        self.btn_start = QPushButton(t("btn.start"))
        self.btn_start.setObjectName("Primary")
        self.btn_start.clicked.connect(self.start_daily)
        bottom.addWidget(self.btn_config)
        bottom.addWidget(self.btn_close)
        bottom.addWidget(self.btn_start)
        layout.addLayout(bottom)

        self.settings_page = SettingsPage(self.config, self.windowIcon(), self)
        self.settings_page.saved.connect(self._on_settings_saved)
        self.settings_page.cancelled.connect(self._show_main)
        self.settings_page.account_changed.connect(self._on_account_changed)
        self.settings_page.account_send_url.connect(self._on_send_public_url)
        self.settings_page.clear_cache_requested.connect(self._on_clear_cache)
        self.settings_page.phone_configured.connect(self._on_phone_configured)
        self.view_stack = QStackedWidget()
        self.view_stack.addWidget(main_page)
        self.view_stack.addWidget(self.settings_page)
        self.setCentralWidget(self.view_stack)

        needs_save = wc.chain_needs_save(wc.chain_cfg(self.config))
        self._sync_chain_config()
        if needs_save:
            self.save_timer.start()
        self.select_software(WORKFLOW_KEY)
        QTimer.singleShot(1500, self._maybe_offer_phone_setup)

    def _on_phone_configured(self, url: str) -> None:
        url = (url or "").strip()
        if not url:
            return
        self.config.setdefault("server", {})["public_url"] = url
        self.save_settings()

    def _maybe_offer_phone_setup(self) -> None:
        server_cfg = self.config.setdefault("server", {})
        if not server_cfg.get("auto_setup", True) or server_cfg.get("setup_prompted"):
            return
        from maagent.control import tailscale as ts

        server_cfg["setup_prompted"] = True
        self.save_settings()
        if ts.is_installed() and ts.is_logged_in() and ts.funnel_enabled():
            return
        reply = QMessageBox.question(
            self,
            t("phone.offer.title"),
            t("phone.offer.message"),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if reply != QMessageBox.Yes:
            return
        self.open_settings()
        self.settings_page.select_page(1)
        self.settings_page.configure_phone()

    def _on_card_enabled(self, key: str, enabled: bool) -> None:
        adapters = self.config.setdefault("adapters", {})
        adapters.setdefault(key, {})["enabled"] = bool(enabled)
        if hasattr(self, "workflow_panel"):
            self.workflow_panel.refresh_enabled()
        name = SOFTWARE_META.get(key, (key, ""))[0]
        logger.info("{} 已{}", name, "启用" if enabled else "停用")
        self.save_timer.start()

    def select_software(self, key: str) -> None:
        self.selected_software = key
        for card_key, card in self.cards.items():
            card.set_selected(card_key == key)
        # 周常/月常 只对 MAA 有意义；日常工作流 有独立的编排面板
        is_maa = key == "maa"
        is_workflow = key == WORKFLOW_KEY
        self.weekly_panel.setVisible(is_maa)
        self.monthly_panel.setVisible(is_maa)
        self.workflow_panel.setVisible(is_workflow)
        self.left_panel.setVisible(key in ("maa", WORKFLOW_KEY))
        if hasattr(self, "maa_auto_close_row"):
            self.maa_auto_close_row.setVisible(key == "maa")
        if hasattr(self, "maaend_auto_close_row"):
            self.maaend_auto_close_row.setVisible(key == "maaend")
        # 工作流界面用自己的「立即执行 / 停止」，底部的关闭与开始按钮无意义
        self.btn_close.setVisible(not is_workflow)
        self.btn_start.setVisible(not is_workflow)

    # -- 日常工作流 ------------------------------------------------------ #
    def _check_schedule_warning(self) -> None:
        """Warn a few minutes before a scheduled run actually starts."""
        app_cfg = self.config.get("app", {}) or {}
        if not app_cfg.get("schedule_warning", True):
            self._warned_run = None
            return
        minutes = int(app_cfg.get("schedule_warning_minutes", 5) or 5)
        try:
            nxt = self.scheduler.next_run()
        except Exception:
            return
        if nxt is None:
            self._warned_run = None
            return
        key = nxt.strftime("%Y-%m-%d %H:%M")
        delta = (nxt - datetime.now()).total_seconds()
        if 0 < delta <= minutes * 60:
            if self._warned_run != key:
                self._warned_run = key
                label = nxt.strftime("%H:%M")
                logger.info("定时提醒：{} 分钟后将启动日常（{}）", minutes, label)
                self.notify("Maagent", t("tray.schedule_warning", minutes=minutes, time=label))
        else:
            self._warned_run = None

    def _sync_chain_config(self) -> None:
        """Push the current workflow UI state into the config for the scheduler."""
        self.config.setdefault("workflow", {})["chain"] = self.workflow_panel.collect()

    def on_workflow_changed(self) -> None:
        self._sync_chain_config()
        self.controller.emit_status()
        enabled = self.workflow_panel.is_enabled()
        self.workflow_card.set_state_text(
            t("card.schedule_on") if enabled else t("card.schedule_off"), active=enabled
        )
        self.workflow_panel.refresh_status(self.scheduler)
        self.save_timer.start()

    def on_run_requested(self, software_list: list[str]) -> None:
        if self.controller.is_running():
            self.request_stop()
            return
        self.start_chain(software_list)

    def check_schedule(self) -> None:
        self._check_schedule_warning()
        if self.controller.is_running():
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
            self.notify("Maagent", t("tray.scheduled", label=event.label))
            self.start_chain(event.software_list)
        self.workflow_panel.refresh_status(self.scheduler)

    def selected_days(self) -> list[int]:
        return [i for i, btn in enumerate(self.day_buttons) if btn.isChecked()]

    def update_weekly_status(self) -> None:
        today = game_weekday()
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
        today = game_weekday()
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
        workflow["chain"] = self.workflow_panel.collect()
        adapters = self.config.setdefault("adapters", {})
        adapters.setdefault("maaend", {})["auto_close"] = (
            self.maaend_auto_close_switch.isChecked()
        )
        app_cfg = self.config.setdefault("app", {})
        app_cfg["minimize_to_tray"] = self.minimize_to_tray
        try:
            target = self._config_write_path()
            with open(target, "w", encoding="utf-8") as f:
                yaml.safe_dump(self.config, f, allow_unicode=True, sort_keys=False)
            after = config_snapshot(self.config)
            log_config_changes(self._last_saved, after)
            self._last_saved = after
        except Exception as e:
            logger.error("保存配置失败: {}", e)

    def _config_write_path(self) -> Path:
        target = app_base_dir() / "config" / "config.yaml"
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def append_log(self, text: str) -> None:
        self.log_view.appendPlainText(text)

    def start_daily(self) -> None:
        if self.controller.is_running():
            self.request_stop()
            return
        if self.selected_software == WORKFLOW_KEY:
            self.workflow_panel.request_run()
            return
        software = self.selected_software or "maa"
        if not self._software_enabled(software):
            name = SOFTWARE_META.get(software, (software, ""))[0]
            logger.warning("{} 未启用，无法开始", name)
            self.status_label.setText(t("status.card_disabled", name=name))
            return
        self._start_controller([software], chain=False)

    def start_chain(self, software_list: list[str] | None = None) -> None:
        if self.controller.is_running():
            logger.warning("已有任务在运行，忽略本次工作流启动")
            return
        if software_list is None:
            software_list = self.workflow_panel.seq_list.enabled_softwares()
        runnable = [s for s in software_list if self._software_enabled(s)]
        for skipped in (s for s in software_list if s not in runnable):
            logger.warning("{} 未启用，已跳过", SOFTWARE_META.get(skipped, (skipped, ""))[0])
        if not runnable:
            logger.warning("日常工作流没有可执行的任务")
            self.status_label.setText(t("status.chain_empty"))
            return
        self._start_controller(runnable, chain=True)

    def _start_controller(self, software_list: list[str], chain: bool) -> None:
        self.report_view.clear()
        try:
            self.controller.start(software_list)
        except RuntimeError as e:
            logger.warning("{}", e)
            self.status_label.setText(t("status.chain_empty"))
            return
        if chain:
            self.workflow_panel.set_running(True)
            logger.info("开始日常工作流: {}", " → ".join(software_list))

    def _software_enabled(self, key: str) -> bool:
        return bool((self.config.get("adapters", {}) or {}).get(key, {}).get("enabled", False))

    def request_stop(self) -> None:
        if not self.controller.is_running():
            return
        self.btn_start.setEnabled(False)
        self.btn_start.setText(t("btn.stopping"))
        self.workflow_panel.set_running(True, stopping=True)
        self.status_label.setText(t("status.stopping"))
        self.controller.stop()
        logger.warning("已请求停止日常，正在中止当前工作流")

    def _on_controller_status(self, snapshot: object) -> None:
        if not isinstance(snapshot, dict) or not hasattr(self, "btn_start"):
            return
        running = bool(snapshot.get("running"))
        stopping = snapshot.get("status") == "stopping"
        was_running = bool(getattr(self, "_was_running", False))
        self._was_running = running
        if running:
            self.btn_start.setEnabled(not stopping)
            self.btn_start.setText(t("btn.stop") if not stopping else t("btn.stopping"))
            set_button_role(self.btn_start, "Danger")
            self.workflow_panel.set_running(True, stopping=stopping)
            self.status_label.setText(t("status.chain_running"))
        else:
            self.btn_start.setEnabled(True)
            self.btn_start.setText(t("btn.start"))
            set_button_role(self.btn_start, "Primary")
            self.workflow_panel.set_running(False)
            self.status_label.setText(t("status.idle"))
            if was_running:
                # a task just finished — let "接续上个任务" entries start promptly
                QTimer.singleShot(1000, self.check_schedule)

    def _on_controller_report(self, data: object) -> None:
        if not isinstance(data, dict):
            return
        text = str(data.get("text") or "")
        if text:
            existing = self.report_view.toPlainText()
            self.report_view.setPlainText(f"{existing}\n\n{text}" if existing else text)
            self.report_view.verticalScrollBar().setValue(
                self.report_view.verticalScrollBar().maximum()
            )
        label = str(data.get("status_label") or "")
        if label:
            self.notify("Maagent", t("tray.report", label=label))

    def _start_embedded_server(self) -> None:
        server_cfg = self.config.get("server", {}) or {}
        if not server_cfg.get("enabled"):
            return
        login = self._account_login()
        if not login:
            logger.info("未登录账号，暂不启动远程服务")
            return
        if self._server_httpd is not None:
            return
        try:
            from maagent.server.app import start_server

            self._server_httpd, self._server_ctx, _ = start_server(
                self.config, self.controller
            )
        except Exception as e:
            logger.error("启动远程服务失败: {}", e)

    def _public_url(self) -> str:
        server_cfg = self.config.get("server", {}) or {}
        configured = str(server_cfg.get("public_url") or "").strip()
        if configured:
            return configured
        from maagent.server.net import detect_public_url

        return detect_public_url() or ""

    def _send_public_url_email(self, email: str) -> None:
        url = self._public_url()
        if not url:
            logger.warning("未获取到手机网页地址，无法发送域名邮件")
            return
        email_cfg = (self.config.get("notify", {}) or {}).get("email", {}) or {}
        from maagent.notify.email import EmailNotifier

        html = (
            f"<p>Maagent 账号 <b>{email}</b> 已登录。</p>"
            f"<p>手机网页地址（手机浏览器直接打开，无需 VPN）：</p>"
            f"<p style='font-size:16px'><a href='{url}'>{url}</a></p>"
        )
        if EmailNotifier(email_cfg).send_to(email, "Maagent 手机网页地址", html):
            logger.info("已发送手机网页地址到 {}", email)

    def _account_login(self) -> str:
        server_cfg = self.config.get("server", {}) or {}
        return str(
            server_cfg.get("account_login") or server_cfg.get("account_email") or ""
        ).strip()

    def _ensure_phone_remote_ready(self) -> None:
        """Make sure the Tailscale client is running so the Funnel backend is up.

        Without the GUI client the Windows backend can stay in ``NoState`` and the
        persisted Funnel stays inactive, which looks like "server unavailable" on
        the phone until something starts Tailscale.
        """
        server_cfg = self.config.get("server", {}) or {}
        if not server_cfg.get("enabled") or not self._account_login():
            return

        def work() -> None:
            try:
                from maagent.control import tailscale as ts

                if ts.is_installed():
                    ts.ensure_ipn(lambda s: logger.info("手机远程: {}", s))
            except Exception as e:
                logger.warning("启动 Tailscale 客户端失败: {}", e)

        threading.Thread(target=work, name="phone-remote-ready", daemon=True).start()

    def _sync_account_name(self) -> bool:
        """Backfill display name / phone for sessions logged in before these fields."""
        server_cfg = self.config.get("server", {}) or {}
        login = self._account_login()
        if not login:
            return False
        need_name = not str(server_cfg.get("account_name") or "").strip()
        need_phone = not str(server_cfg.get("account_phone") or "").strip()
        if not need_name and not need_phone:
            return False
        try:
            from maagent.server.store import AccountStore

            db_path = str(server_cfg.get("db_path", "config/accounts.db"))
            store = AccountStore(db_path)
            account = store.get_account_by_login(login)
            store.close()
        except Exception:
            return False
        if not account:
            return False
        changed = False
        if need_name:
            self.config["server"]["account_name"] = str(
                account.get("username") or account.get("email") or login
            )
            changed = True
        if need_phone:
            phone = str(account.get("phone") or "").strip()
            if phone:
                self.config["server"]["account_phone"] = phone
                changed = True
        return changed

    def _on_account_changed(
        self, login: str, name: str = "", phone: str = "", email: str = ""
    ) -> None:
        server_cfg = self.config.setdefault("server", {})
        login = (login or "").strip()
        name = (name or "").strip()
        server_cfg["account_login"] = login
        server_cfg["account_name"] = name or login
        server_cfg["account_phone"] = (phone or "").strip()
        # Use the account's own email (only real email accounts have one).
        server_cfg["account_email"] = (email or "").strip()
        if server_cfg["account_email"]:
            email_cfg = self.config.setdefault("notify", {}).setdefault("email", {})
            email_cfg["send_log_on"] = ["success", "warning", "failed", "stopped"]
            email_cfg["enabled"] = True
        self.save_settings()
        self._update_account_chip()
        if not login:
            self._stop_embedded_server()
            logger.info("已退出账号，远程服务已停止")
            return
        logger.info("账号已登录: {}", login)
        self._start_embedded_server()

    def _on_send_public_url(self) -> None:
        email = str((self.config.get("server", {}) or {}).get("account_email") or "").strip()
        if email:
            self._send_public_url_email(email)
        else:
            logger.warning("当前为本机账号（无邮箱），无法发送手机网页地址邮件")

    def _on_clear_cache(self) -> None:
        log_dir = app_base_dir() / "logs"
        reply = QMessageBox.question(
            self,
            t("about.clear.title"),
            t("about.clear.message"),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        removed = 0
        skipped = 0
        if log_dir.is_dir():
            for path in sorted(log_dir.rglob("*"), reverse=True):
                try:
                    if path.is_file():
                        path.unlink()
                        removed += 1
                    elif path.is_dir():
                        path.rmdir()
                except OSError:
                    skipped += 1
        self.controller.clear_reports()
        logger.info("已清除缓存：删除 {} 个文件（跳过 {} 个占用中）", removed, skipped)
        message = t("about.clear.done", count=removed)
        self.notify("Maagent", message)
        QMessageBox.information(self, t("about.clear.title"), message)

    def _stop_embedded_server(self) -> None:
        if self._server_httpd is None:
            return
        try:
            from maagent.server.app import stop_server

            stop_server(self._server_httpd, self._server_ctx)
        except Exception as e:
            logger.warning("停止远程服务失败: {}", e)
        self._server_httpd = None
        self._server_ctx = None

    def copy_report(self) -> None:
        text = self.report_view.toPlainText()
        if not text:
            logger.info("暂无报告可复制")
            return
        QApplication.clipboard().setText(text)
        logger.info("报告已复制到剪贴板")

    def do_close_all(self) -> None:
        emu = self.config.get("adapters", {}).get("maa", {}).get("emulator", {})
        close_all(emu)
        self.status_label.setText(t("status.closed_all"))

    def open_config_dir(self) -> None:
        subprocess.Popen(["explorer", str(self.config_path.parent)])

    # -- 设置 ------------------------------------------------------------ #
    def open_settings(self) -> None:
        self.settings_page.reload()
        self.view_stack.setCurrentWidget(self.settings_page)

    def open_account_settings(self) -> None:
        self.open_settings()
        self.settings_page.select_page(0)

    def _update_account_chip(self) -> None:
        if not hasattr(self, "account_chip"):
            return
        server_cfg = self.config.get("server", {}) or {}
        login = self._account_login()
        name = str(server_cfg.get("account_name") or login).strip()
        self.account_chip.setText(
            t("account.chip.logged", email=name) if login else t("account.chip.none")
        )
        self.account_chip.setToolTip(t("settings.tab.account"))

    def _show_main(self) -> None:
        self.view_stack.setCurrentIndex(0)

    def _on_settings_saved(self, settings: dict) -> None:
        old_language = (self.config.get("app", {}) or {}).get("language", DEFAULT_LANGUAGE)
        self.apply_settings(settings)
        if settings["language"] == old_language:
            self._show_main()

    def apply_settings(self, settings: dict) -> None:
        app_cfg = self.config.setdefault("app", {})
        old_language = app_cfg.get("language", DEFAULT_LANGUAGE)
        app_cfg["language"] = settings["language"]
        app_cfg["theme"] = settings["theme"]
        app_cfg["autostart"] = settings["autostart"]
        app_cfg["minimize_to_tray"] = settings["minimize_to_tray"]
        app_cfg["schedule_warning"] = settings.get("schedule_warning", True)
        app_cfg["hotkeys"] = settings["hotkeys"]
        smtp = settings.get("smtp") or {}
        email_cfg = self.config.setdefault("notify", {}).setdefault("email", {})
        email_cfg["smtp_host"] = smtp.get("host") or "smtp.qq.com"
        try:
            email_cfg["smtp_port"] = int(smtp.get("port") or 465)
        except (TypeError, ValueError):
            email_cfg["smtp_port"] = 465
        email_cfg["username"] = smtp.get("username", "")
        email_cfg["password"] = smtp.get("password", "")
        email_cfg["send_log_on"] = list(settings.get("send_log_on", []))
        email_cfg.pop("send_log", None)

        set_autostart(settings["autostart"])
        self.minimize_to_tray = settings["minimize_to_tray"]
        self.theme = settings["theme"]
        apply_theme(settings["theme"])
        self._register_hotkeys()
        self.save_settings()

        if settings["language"] != old_language:
            set_language(settings["language"])
            self.reload_ui()

    def reload_ui(self) -> None:
        self._build_ui()
        self._retranslate_tray()

    # -- 全局快捷键 ------------------------------------------------------ #
    def _register_hotkeys(self) -> None:
        hotkeys = (self.config.get("app", {}) or {}).get("hotkeys", {}) or {}
        self.hotkeys.register("start", str(hotkeys.get("start", DEFAULT_HOTKEYS["start"])))
        self.hotkeys.register("stop", str(hotkeys.get("stop", DEFAULT_HOTKEYS["stop"])))

    def _on_hotkey(self, name: str) -> None:
        if name == "start":
            self.hotkey_start()
        elif name == "stop":
            self.hotkey_stop()

    def hotkey_start(self) -> None:
        if self.controller.is_running():
            logger.info("快捷键：已有任务在运行")
            return
        if self.selected_software == WORKFLOW_KEY:
            logger.info("快捷键：立即执行工作流")
            self.workflow_panel.request_run()
            return
        logger.info("快捷键：开始任务")
        self.start_daily()

    def hotkey_stop(self) -> None:
        if self.controller.is_running():
            logger.info("快捷键：强制结束任务")
            self.request_stop()
        else:
            logger.info("快捷键：当前没有运行中的任务")

    # -- 系统托盘 -------------------------------------------------------- #
    def _setup_tray(self) -> None:
        self.tray: QSystemTrayIcon | None = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            logger.warning("系统托盘不可用，关闭窗口将直接退出")
            return
        self.tray = QSystemTrayIcon(QIcon(str(asset_path("icon.ico"))), self)
        menu = QMenu()
        self._tray_show_action = menu.addAction(t("tray.show"))
        self._tray_show_action.triggered.connect(self.restore_window)
        menu.addSeparator()
        self._tray_quit_action = menu.addAction(t("tray.quit"))
        self._tray_quit_action.triggered.connect(self.quit_app)
        self.tray.setContextMenu(menu)
        self.tray.setToolTip(t("tray.tip"))
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _retranslate_tray(self) -> None:
        if self.tray is None:
            return
        self.tray.setToolTip(t("tray.tip"))
        if self._tray_show_action is not None:
            self._tray_show_action.setText(t("tray.show"))
        if self._tray_quit_action is not None:
            self._tray_quit_action.setText(t("tray.quit"))

    def _on_tray_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.DoubleClick, QSystemTrayIcon.Trigger):
            self.restore_window()

    def restore_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def notify(self, title: str, message: str) -> None:
        if self.tray is not None:
            self.tray.showMessage(title, message, QSystemTrayIcon.Information, 4000)

    def quit_app(self) -> None:
        if self.controller.is_running():
            self.restore_window()
            reply = QMessageBox.question(
                self, t("quit.title"), t("quit.message"),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return
        self._shutdown()
        QApplication.quit()

    def _shutdown(self) -> None:
        self._quitting = True
        self.hotkeys.unregister_all()
        if self.schedule_timer.isActive():
            self.schedule_timer.stop()
        if self.save_timer.isActive():
            self.save_timer.stop()
            self.save_settings()
        if self.controller.is_running():
            self.controller.stop()
        self._stop_embedded_server()
        if self.tray is not None:
            self.tray.hide()

    def closeEvent(self, event) -> None:  # noqa: N802
        minimize = self.tray is not None and self.minimize_to_tray and not self._quitting
        if minimize:
            if self.save_timer.isActive():
                self.save_timer.stop()
                self.save_settings()
            event.ignore()
            self.hide()
            if not self._tray_notice_shown:
                self._tray_notice_shown = True
                self.notify("Maagent", t("tray.minimized"))
            logger.info("已最小化到托盘，定时任务继续运行")
            return
        self._shutdown()
        event.accept()


def main() -> int:
    os.chdir(app_base_dir())
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setQuitOnLastWindowClosed(False)
    icon = QIcon(str(asset_path("icon.ico")))
    app.setWindowIcon(icon)

    config_path = resolve_config_path()
    config = load_config(config_path)
    apply_theme((config.get("app", {}) or {}).get("theme", "light"))
    setup_logger(log_dir=str(app_base_dir() / "logs"))

    bridge = LogBridge()
    logger.add(
        lambda m: bridge.message.emit(str(m).rstrip()),
        level="INFO",
        colorize=False,
        format="{time:HH:mm:ss} | {level: <7} | {message}",
    )
    logger.info("Maagent 启动，配置: {}", config_path)

    win = MaAgentWindow(config, config_path, bridge)
    win.setWindowIcon(icon)
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
