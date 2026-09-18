from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QIcon, QKeySequence
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from maagent.gui.widgets import ToggleSwitch
from maagent.i18n import DEFAULT_LANGUAGE, LANGUAGES, t

APP_VERSION = "0.1.0"


class HotkeyEdit(QPushButton):
    """A button that captures a key combination when clicked."""

    hotkey_changed = Signal(str)

    def __init__(self, value: str = "", parent: QWidget | None = None) -> None:
        super().__init__(value, parent)
        self._value = value or ""
        self._capturing = False
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.clicked.connect(self._start_capture)

    def _start_capture(self) -> None:
        self._capturing = True
        self.setText("…")

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if not self._capturing:
            super().keyPressEvent(event)
            return
        key = event.key()
        if key in (Qt.Key_Control, Qt.Key_Shift, Qt.Key_Alt, Qt.Key_Meta):
            return
        if key == Qt.Key_Escape:
            self._capturing = False
            self.setText(self._value)
            return
        modifiers = event.modifiers()
        parts: list[str] = []
        if modifiers & Qt.ControlModifier:
            parts.append("Ctrl")
        if modifiers & Qt.ShiftModifier:
            parts.append("Shift")
        if modifiers & Qt.AltModifier:
            parts.append("Alt")
        if modifiers & Qt.MetaModifier:
            parts.append("Win")
        name = QKeySequence(key).toString()
        if name:
            parts.append(name)
            self._value = "+".join(parts)
            self._capturing = False
            self.setText(self._value)
            self.hotkey_changed.emit(self._value)

    def value(self) -> str:
        return self._value

    def set_value(self, value: str) -> None:
        self._value = value or ""
        self._capturing = False
        self.setText(self._value)


class ChoiceGroup(QWidget):
    """A grid of large selectable buttons (selected = accent fill)."""

    changed = Signal(str)

    def __init__(
        self,
        options: list[tuple[str, str]],
        columns: int = 2,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._value: str | None = None
        self._buttons: dict[str, QPushButton] = {}
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(10)
        for index, (value, label) in enumerate(options):
            button = QPushButton(label)
            button.setObjectName("ChoiceButton")
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setMinimumHeight(46)
            button.clicked.connect(lambda _=False, v=value: self.set_value(v, emit=True))
            self._buttons[value] = button
            grid.addWidget(button, index // columns, index % columns)
        for column in range(columns):
            grid.setColumnStretch(column, 1)

    def value(self) -> str | None:
        return self._value

    def set_value(self, value: str, emit: bool = False) -> None:
        if value not in self._buttons:
            return
        self._value = value
        for key, button in self._buttons.items():
            button.setChecked(key == value)
        if emit:
            self.changed.emit(value)


class SettingsPage(QWidget):
    """The in-window settings view (sidebar + pages), modelled on MaaEnd."""

    saved = Signal(dict)
    cancelled = Signal()

    def __init__(self, config: dict, icon: QIcon | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.config = config
        self.setObjectName("SettingsPage")

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_sidebar())

        right = QVBoxLayout()
        right.setContentsMargins(36, 28, 36, 20)
        right.setSpacing(16)
        self.stack = QStackedWidget()
        self.stack.addWidget(self._appearance_page())
        self.stack.addWidget(self._general_page())
        self.stack.addWidget(self._hotkeys_page())
        self.stack.addWidget(self._about_page(icon))
        self.nav_group.idClicked.connect(self.stack.setCurrentIndex)
        right.addWidget(self.stack, 1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        back = QPushButton(t("settings.back"))
        back.setCursor(Qt.PointingHandCursor)
        back.clicked.connect(self.cancelled.emit)
        save = QPushButton(t("settings.save"))
        save.setObjectName("Primary")
        save.setCursor(Qt.PointingHandCursor)
        save.clicked.connect(lambda: self.saved.emit(self.result_settings()))
        buttons.addWidget(back)
        buttons.addWidget(save)
        right.addLayout(buttons)
        root.addLayout(right, 1)

        self.reload()
        self.nav_buttons[0].setChecked(True)
        self.stack.setCurrentIndex(0)

    # -- chrome --------------------------------------------------------- #
    def _build_sidebar(self) -> QWidget:
        sidebar = QFrame()
        sidebar.setObjectName("SettingsSidebar")
        sidebar.setFixedWidth(200)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(18, 26, 18, 24)
        layout.setSpacing(6)

        brand = QLabel("Maagent")
        brand.setObjectName("SettingsBrand")
        layout.addWidget(brand)
        subtitle = QLabel(t("settings.title"))
        subtitle.setObjectName("SettingsBrandSub")
        layout.addWidget(subtitle)
        layout.addSpacing(18)

        self.nav_buttons: list[QPushButton] = []
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        for index, label in enumerate(
            (
                t("settings.tab.appearance"),
                t("settings.tab.general"),
                t("settings.tab.hotkeys"),
                t("settings.tab.about"),
            )
        ):
            button = QPushButton(label)
            button.setObjectName("SettingsNav")
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setMinimumHeight(40)
            self.nav_group.addButton(button, index)
            self.nav_buttons.append(button)
            layout.addWidget(button)
        layout.addStretch(1)
        return sidebar

    def _page(self, title_key: str, desc_key: str) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        title = QLabel(t(title_key))
        title.setObjectName("SettingsPageTitle")
        layout.addWidget(title)
        desc = QLabel(t(desc_key))
        desc.setObjectName("SettingsPageDesc")
        desc.setWordWrap(True)
        layout.addWidget(desc)
        layout.addSpacing(6)
        return page, layout

    def _section(self, title: str, content: QWidget) -> QFrame:
        card = QFrame()
        card.setObjectName("SettingsCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        label = QLabel(title)
        label.setObjectName("SettingsSectionTitle")
        layout.addWidget(label)
        layout.addWidget(content)
        return card

    def _toggle_row(self, label: str, switch: ToggleSwitch) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        text = QLabel(label)
        text.setObjectName("SettingsField")
        layout.addWidget(text)
        layout.addStretch(1)
        layout.addWidget(switch)
        return row

    # -- pages ---------------------------------------------------------- #
    def _appearance_page(self) -> QWidget:
        page, layout = self._page("settings.tab.appearance", "settings.appearance.desc")
        self.lang_group = ChoiceGroup(
            [(code, name) for code, name in LANGUAGES.items()], columns=2
        )
        self.theme_group = ChoiceGroup(
            [
                ("light", t("settings.theme.light")),
                ("dark", t("settings.theme.dark")),
            ],
            columns=2,
        )
        layout.addWidget(self._section(t("settings.language"), self.lang_group))
        layout.addWidget(self._section(t("settings.theme"), self.theme_group))
        layout.addStretch(1)
        return page

    def _general_page(self) -> QWidget:
        page, layout = self._page("settings.tab.general", "settings.general.desc")
        self.autostart_switch = ToggleSwitch(False, width=42, height=22)
        self.tray_switch = ToggleSwitch(True, width=42, height=22)
        self.email_log_switch = ToggleSwitch(False, width=42, height=22)
        rows = QWidget()
        rows_layout = QVBoxLayout(rows)
        rows_layout.setContentsMargins(0, 0, 0, 0)
        rows_layout.setSpacing(14)
        rows_layout.addWidget(self._toggle_row(t("settings.autostart"), self.autostart_switch))
        rows_layout.addWidget(self._toggle_row(t("settings.tray"), self.tray_switch))
        rows_layout.addWidget(self._toggle_row(t("settings.email_log"), self.email_log_switch))
        layout.addWidget(self._section(t("settings.general.section"), rows))
        layout.addStretch(1)
        return page

    def _hotkeys_page(self) -> QWidget:
        page, layout = self._page("settings.tab.hotkeys", "settings.hotkeys.desc")
        self.hotkey_start = HotkeyEdit("F8")
        self.hotkey_stop = HotkeyEdit("F9")
        start_row = self._toggle_row(t("settings.hotkey.start"), self.hotkey_start)
        stop_row = self._toggle_row(t("settings.hotkey.stop"), self.hotkey_stop)
        rows = QWidget()
        rows_layout = QVBoxLayout(rows)
        rows_layout.setContentsMargins(0, 0, 0, 0)
        rows_layout.setSpacing(14)
        rows_layout.addWidget(start_row)
        rows_layout.addWidget(stop_row)
        layout.addWidget(self._section(t("settings.hotkeys.section"), rows))

        reset = QPushButton(t("settings.hotkey.reset"))
        reset.setCursor(Qt.PointingHandCursor)
        reset.clicked.connect(self._reset_hotkeys)
        reset_row = QHBoxLayout()
        reset_row.addWidget(reset)
        reset_row.addStretch(1)
        layout.addLayout(reset_row)
        layout.addStretch(1)
        return page

    def _about_page(self, icon: QIcon | None) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addStretch(1)
        icon_label = QLabel()
        if icon is not None and not icon.isNull():
            icon_label.setPixmap(icon.pixmap(144, 144))
        icon_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(icon_label)
        layout.addSpacing(16)
        welcome = QLabel(t("settings.about.welcome"))
        welcome.setObjectName("SettingsAboutTitle")
        welcome.setAlignment(Qt.AlignCenter)
        welcome.setWordWrap(True)
        layout.addWidget(welcome)
        version = QLabel(f"{t('settings.about.version')} {APP_VERSION}")
        version.setObjectName("SettingsPageDesc")
        version.setAlignment(Qt.AlignCenter)
        layout.addWidget(version)
        layout.addStretch(2)
        return page

    def _reset_hotkeys(self) -> None:
        self.hotkey_start.set_value("F8")
        self.hotkey_stop.set_value("F9")

    # -- load / result -------------------------------------------------- #
    def reload(self) -> None:
        app_cfg = self.config.get("app", {}) or {}
        self.lang_group.set_value(app_cfg.get("language", DEFAULT_LANGUAGE))
        self.theme_group.set_value(app_cfg.get("theme", "light"))

        self.autostart_switch.setChecked(bool(app_cfg.get("autostart", False)))
        self.tray_switch.setChecked(bool(app_cfg.get("minimize_to_tray", True)))
        email = (self.config.get("notify", {}) or {}).get("email", {}) or {}
        self.email_log_switch.setChecked(bool(email.get("send_log", False)))

        hotkeys = app_cfg.get("hotkeys", {}) or {}
        self.hotkey_start.set_value(str(hotkeys.get("start", "F8")))
        self.hotkey_stop.set_value(str(hotkeys.get("stop", "F9")))

    def result_settings(self) -> dict:
        return {
            "language": self.lang_group.value(),
            "theme": self.theme_group.value(),
            "autostart": self.autostart_switch.isChecked(),
            "minimize_to_tray": self.tray_switch.isChecked(),
            "send_log": self.email_log_switch.isChecked(),
            "hotkeys": {
                "start": self.hotkey_start.value(),
                "stop": self.hotkey_stop.value(),
            },
        }
