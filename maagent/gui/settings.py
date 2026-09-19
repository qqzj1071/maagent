from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QIcon, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from maagent.gui.widgets import ToggleSwitch
from maagent.i18n import DEFAULT_LANGUAGE, LANGUAGES, t
from maagent.notify.email import LOG_STATUSES, normalize_log_statuses

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
    account_changed = Signal(str, str, str)
    account_send_url = Signal()
    clear_cache_requested = Signal()
    phone_configured = Signal(str)

    def __init__(self, config: dict, icon: QIcon | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.config = config
        self._service = None
        self.setObjectName("SettingsPage")

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_sidebar())

        right = QVBoxLayout()
        right.setContentsMargins(36, 28, 36, 20)
        right.setSpacing(16)
        self.stack = QStackedWidget()
        self.stack.setAutoFillBackground(False)
        # Only the account page can be taller than the view, so it alone scrolls;
        # the remaining pages fit and should not scroll.
        self.stack.addWidget(self._scrollable(self._account_page()))
        self.stack.addWidget(self._phone_page())
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
                t("settings.tab.account"),
                t("settings.tab.phone"),
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

    def _scrollable(self, page: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setObjectName("SettingsScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.viewport().setAutoFillBackground(False)
        scroll.setWidget(page)
        return scroll

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

    # -- account -------------------------------------------------------- #
    def _account_service(self):
        if self._service is None:
            from maagent.notify.email import EmailNotifier
            from maagent.server.accounts import AccountService
            from maagent.server.store import AccountStore

            server_cfg = self.config.get("server", {}) or {}
            db_path = str(server_cfg.get("db_path", "config/accounts.db"))
            email_cfg = (self.config.get("notify", {}) or {}).get("email", {}) or {}
            self._service = AccountService(
                AccountStore(db_path), EmailNotifier(email_cfg), server_cfg
            )
        return self._service

    @staticmethod
    def _account_error_text(error) -> str:
        key = {
            "bad_credentials": "account.msg.bad_login",
            "forbidden": "account.msg.disabled",
            "email_exists": "account.msg.email_exists",
            "bad_code": "account.msg.bad_code",
            "username_exists": "account.msg.username_exists",
            "bad_username": "account.msg.bad_username",
            "phone_exists": "account.msg.phone_exists",
            "bad_phone": "account.msg.bad_phone",
        }.get(getattr(error, "code", ""))
        return t(key) if key else error.message

    def _account_login_id(self) -> str:
        server_cfg = self.config.get("server", {}) or {}
        return str(
            server_cfg.get("account_login") or server_cfg.get("account_email") or ""
        ).strip()

    def _account_page(self) -> QWidget:
        page, layout = self._page("settings.tab.account", "settings.account.desc")
        layout.setSpacing(18)

        self.smtp_host = QLineEdit()
        self.smtp_host.setPlaceholderText(t("settings.smtp.host"))
        self.smtp_port = QLineEdit()
        self.smtp_port.setPlaceholderText(t("settings.smtp.port"))
        self.smtp_username = QLineEdit()
        self.smtp_username.setPlaceholderText(t("settings.smtp.username"))
        self.smtp_password = QLineEdit()
        self.smtp_password.setEchoMode(QLineEdit.Password)
        self.smtp_password.setPlaceholderText(t("settings.smtp.password"))
        self.smtp_test_btn = QPushButton(t("settings.smtp.test"))
        self.smtp_test_btn.setCursor(Qt.PointingHandCursor)
        self.smtp_test_btn.clicked.connect(self._smtp_test)
        self.smtp_msg = QLabel()
        self.smtp_msg.setObjectName("SettingsPageDesc")
        self.smtp_msg.setWordWrap(True)
        smtp_hint = QLabel(t("settings.smtp.hint"))
        smtp_hint.setObjectName("SettingsPageDesc")
        smtp_hint.setWordWrap(True)
        self.smtp_box = QWidget()
        smtp_layout = QVBoxLayout(self.smtp_box)
        smtp_layout.setContentsMargins(0, 0, 0, 0)
        smtp_layout.setSpacing(12)
        smtp_layout.addWidget(smtp_hint)
        smtp_layout.addWidget(self.smtp_host)
        smtp_layout.addWidget(self.smtp_port)
        smtp_layout.addWidget(self.smtp_username)
        smtp_layout.addWidget(self.smtp_password)
        smtp_layout.addWidget(self.smtp_test_btn)
        smtp_layout.addWidget(self.smtp_msg)
        for field in (self.smtp_host, self.smtp_port, self.smtp_username, self.smtp_password):
            field.textChanged.connect(self._on_smtp_changed)
        self.smtp_card = self._section(t("settings.smtp.section"), self.smtp_box)
        layout.addWidget(self.smtp_card)

        self.account_logged = QWidget()
        logged_layout = QVBoxLayout(self.account_logged)
        logged_layout.setContentsMargins(0, 0, 0, 0)
        logged_layout.setSpacing(12)
        self.account_email_label = QLabel()
        self.account_email_label.setObjectName("SettingsField")
        self.account_email_label.setWordWrap(True)
        logged_layout.addWidget(self.account_email_label)
        logged_row = QHBoxLayout()
        self.account_send_url_btn = QPushButton(t("settings.account.send_url"))
        self.account_send_url_btn.setCursor(Qt.PointingHandCursor)
        self.account_send_url_btn.clicked.connect(self.account_send_url.emit)
        self.account_logout_btn = QPushButton(t("settings.account.logout"))
        self.account_logout_btn.setCursor(Qt.PointingHandCursor)
        self.account_logout_btn.clicked.connect(self._account_logout)
        logged_row.addWidget(self.account_send_url_btn)
        logged_row.addWidget(self.account_logout_btn)
        logged_row.addStretch(1)
        logged_layout.addLayout(logged_row)
        self.account_logged_card = self._section(
            t("settings.account.current"), self.account_logged
        )
        layout.addWidget(self.account_logged_card)

        self.account_phone = QWidget()
        phone_layout = QVBoxLayout(self.account_phone)
        phone_layout.setContentsMargins(0, 0, 0, 0)
        phone_layout.setSpacing(12)
        phone_hint = QLabel(t("settings.account.phone_hint"))
        phone_hint.setObjectName("SettingsPageDesc")
        phone_hint.setWordWrap(True)
        self.account_phone_input = QLineEdit()
        self.account_phone_input.setPlaceholderText(t("settings.account.phone"))
        self.account_phone_btn = QPushButton(t("settings.account.bind_phone"))
        self.account_phone_btn.setObjectName("Primary")
        self.account_phone_btn.setCursor(Qt.PointingHandCursor)
        self.account_phone_btn.clicked.connect(self._account_bind_phone)
        self.account_phone_msg = QLabel()
        self.account_phone_msg.setObjectName("SettingsPageDesc")
        self.account_phone_msg.setWordWrap(True)
        phone_layout.addWidget(phone_hint)
        phone_layout.addWidget(self.account_phone_input)
        phone_layout.addWidget(self.account_phone_btn)
        phone_layout.addWidget(self.account_phone_msg)
        self.account_phone_card = self._section(
            t("settings.account.phone_section"), self.account_phone
        )
        layout.addWidget(self.account_phone_card)

        self.account_bind = QWidget()
        bind_layout = QVBoxLayout(self.account_bind)
        bind_layout.setContentsMargins(0, 0, 0, 0)
        bind_layout.setSpacing(12)
        bind_hint = QLabel(t("settings.account.bind_hint"))
        bind_hint.setObjectName("SettingsPageDesc")
        bind_hint.setWordWrap(True)
        self.account_bind_email = QLineEdit()
        self.account_bind_email.setPlaceholderText(t("settings.account.bind_email"))
        bind_code_row = QHBoxLayout()
        self.account_bind_code = QLineEdit()
        self.account_bind_code.setPlaceholderText(t("settings.account.code"))
        self.account_bind_send_btn = QPushButton(t("settings.account.send_code"))
        self.account_bind_send_btn.setCursor(Qt.PointingHandCursor)
        self.account_bind_send_btn.clicked.connect(self._account_send_bind_code)
        bind_code_row.addWidget(self.account_bind_code, 1)
        bind_code_row.addWidget(self.account_bind_send_btn)
        self.account_bind_btn = QPushButton(t("settings.account.bind"))
        self.account_bind_btn.setObjectName("Primary")
        self.account_bind_btn.setCursor(Qt.PointingHandCursor)
        self.account_bind_btn.clicked.connect(self._account_bind_email)
        self.account_bind_msg = QLabel()
        self.account_bind_msg.setObjectName("SettingsPageDesc")
        self.account_bind_msg.setWordWrap(True)
        bind_layout.addWidget(bind_hint)
        bind_layout.addWidget(self.account_bind_email)
        bind_layout.addLayout(bind_code_row)
        bind_layout.addWidget(self.account_bind_btn)
        bind_layout.addWidget(self.account_bind_msg)
        self.account_bind_card = self._section(
            t("settings.account.bind_section"), self.account_bind
        )
        layout.addWidget(self.account_bind_card)

        self.account_login = QWidget()
        login_layout = QVBoxLayout(self.account_login)
        login_layout.setContentsMargins(0, 0, 0, 0)
        login_layout.setSpacing(12)
        self.account_email = QLineEdit()
        self.account_email.setPlaceholderText(t("settings.account.email_or_username"))
        self.account_password = QLineEdit()
        self.account_password.setEchoMode(QLineEdit.Password)
        self.account_password.setPlaceholderText(t("settings.account.password"))
        self.account_login_btn = QPushButton(t("settings.account.login"))
        self.account_login_btn.setObjectName("Primary")
        self.account_login_btn.setCursor(Qt.PointingHandCursor)
        self.account_login_btn.clicked.connect(self._account_login)
        self.account_login_msg = QLabel()
        self.account_login_msg.setObjectName("SettingsPageDesc")
        self.account_login_msg.setWordWrap(True)
        login_layout.addWidget(self.account_email)
        login_layout.addWidget(self.account_password)
        login_layout.addWidget(self.account_login_btn)
        login_layout.addWidget(self.account_login_msg)
        self.account_login_card = self._section(
            t("settings.account.login_section"), self.account_login
        )
        layout.addWidget(self.account_login_card)

        self.account_local = QWidget()
        local_layout = QVBoxLayout(self.account_local)
        local_layout.setContentsMargins(0, 0, 0, 0)
        local_layout.setSpacing(12)
        self.account_local_username = QLineEdit()
        self.account_local_username.setPlaceholderText(t("settings.account.username"))
        self.account_local_password = QLineEdit()
        self.account_local_password.setEchoMode(QLineEdit.Password)
        self.account_local_password.setPlaceholderText(t("settings.account.password"))
        self.account_local_password2 = QLineEdit()
        self.account_local_password2.setEchoMode(QLineEdit.Password)
        self.account_local_password2.setPlaceholderText(t("settings.account.password2"))
        self.account_local_btn = QPushButton(t("settings.account.create_local"))
        self.account_local_btn.setObjectName("Primary")
        self.account_local_btn.setCursor(Qt.PointingHandCursor)
        self.account_local_btn.clicked.connect(self._account_create_local)
        self.account_local_msg = QLabel()
        self.account_local_msg.setObjectName("SettingsPageDesc")
        self.account_local_msg.setWordWrap(True)
        local_layout.addWidget(self.account_local_username)
        local_layout.addWidget(self.account_local_password)
        local_layout.addWidget(self.account_local_password2)
        local_layout.addWidget(self.account_local_btn)
        local_layout.addWidget(self.account_local_msg)
        self.account_local_card = self._section(
            t("settings.account.local_section"), self.account_local
        )
        layout.addWidget(self.account_local_card)

        self.account_register = QWidget()
        reg_layout = QVBoxLayout(self.account_register)
        reg_layout.setContentsMargins(0, 0, 0, 0)
        reg_layout.setSpacing(12)
        self.account_reg_email = QLineEdit()
        self.account_reg_email.setPlaceholderText(t("settings.account.email"))
        self.account_reg_phone = QLineEdit()
        self.account_reg_phone.setPlaceholderText(t("settings.account.phone"))
        self.account_reg_password = QLineEdit()
        self.account_reg_password.setEchoMode(QLineEdit.Password)
        self.account_reg_password.setPlaceholderText(t("settings.account.password"))
        self.account_reg_password2 = QLineEdit()
        self.account_reg_password2.setEchoMode(QLineEdit.Password)
        self.account_reg_password2.setPlaceholderText(t("settings.account.password2"))
        code_row = QHBoxLayout()
        self.account_reg_code = QLineEdit()
        self.account_reg_code.setPlaceholderText(t("settings.account.code"))
        self.account_send_code_btn = QPushButton(t("settings.account.send_code"))
        self.account_send_code_btn.setCursor(Qt.PointingHandCursor)
        self.account_send_code_btn.clicked.connect(self._account_send_code)
        code_row.addWidget(self.account_reg_code, 1)
        code_row.addWidget(self.account_send_code_btn)
        self.account_register_btn = QPushButton(t("settings.account.register"))
        self.account_register_btn.setObjectName("Primary")
        self.account_register_btn.setCursor(Qt.PointingHandCursor)
        self.account_register_btn.clicked.connect(self._account_register)
        self.account_reg_msg = QLabel()
        self.account_reg_msg.setObjectName("SettingsPageDesc")
        self.account_reg_msg.setWordWrap(True)
        reg_layout.addWidget(self.account_reg_email)
        reg_layout.addWidget(self.account_reg_phone)
        reg_layout.addWidget(self.account_reg_password)
        reg_layout.addWidget(self.account_reg_password2)
        reg_layout.addLayout(code_row)
        reg_layout.addWidget(self.account_register_btn)
        reg_layout.addWidget(self.account_reg_msg)
        self.account_register_card = self._section(
            t("settings.account.register_section"), self.account_register
        )
        layout.addWidget(self.account_register_card)

        layout.addStretch(1)
        return page

    def _phone_page(self) -> QWidget:
        page, layout = self._page("settings.tab.phone", "settings.phone.desc")
        self.phone_status_label = QLabel()
        self.phone_status_label.setObjectName("SettingsField")
        self.phone_status_label.setWordWrap(True)
        self.phone_url_label = QLabel()
        self.phone_url_label.setObjectName("SettingsPageDesc")
        self.phone_url_label.setWordWrap(True)
        self.phone_url_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        buttons = QHBoxLayout()
        self.phone_setup_btn = QPushButton(t("phone.setup"))
        self.phone_setup_btn.setObjectName("Primary")
        self.phone_setup_btn.setCursor(Qt.PointingHandCursor)
        self.phone_setup_btn.clicked.connect(self.configure_phone)
        self.phone_refresh_btn = QPushButton(t("phone.refresh"))
        self.phone_refresh_btn.setCursor(Qt.PointingHandCursor)
        self.phone_refresh_btn.clicked.connect(self._refresh_phone)
        self.phone_copy_btn = QPushButton(t("phone.copy"))
        self.phone_copy_btn.setCursor(Qt.PointingHandCursor)
        self.phone_copy_btn.clicked.connect(self._copy_phone_url)
        buttons.addWidget(self.phone_setup_btn)
        buttons.addWidget(self.phone_refresh_btn)
        buttons.addWidget(self.phone_copy_btn)
        buttons.addStretch(1)
        box = QWidget()
        box_layout = QVBoxLayout(box)
        box_layout.setContentsMargins(0, 0, 0, 0)
        box_layout.setSpacing(12)
        box_layout.addWidget(self.phone_status_label)
        box_layout.addWidget(self.phone_url_label)
        box_layout.addLayout(buttons)
        layout.addWidget(self._section(t("phone.section"), box))
        layout.addStretch(1)
        return page

    def _phone_port(self) -> int:
        try:
            return int((self.config.get("server", {}) or {}).get("port", 8765))
        except (TypeError, ValueError):
            return 8765

    def _refresh_phone(self) -> None:
        from maagent.control import tailscale as ts

        if not ts.is_installed():
            self.phone_status_label.setText(t("phone.status.installed_no"))
        elif ts.is_logged_in():
            self.phone_status_label.setText(t("phone.status.logged_yes"))
        else:
            self.phone_status_label.setText(t("phone.status.logged_no"))
        url = ts.public_url() if (ts.is_installed() and ts.funnel_enabled()) else None
        self.phone_url_label.setProperty("phone_url", url or "")
        self.phone_url_label.setText(
            t("phone.status.url", url=url) if url else t("phone.status.url_none")
        )

    def _copy_phone_url(self) -> None:
        url = str(self.phone_url_label.property("phone_url") or "")
        if url:
            QApplication.clipboard().setText(url)

    def configure_phone(self) -> None:
        from maagent.gui.phone import PhoneRemoteDialog

        dialog = PhoneRemoteDialog(self._phone_port(), self)
        dialog.exec()
        self._refresh_phone()
        if getattr(dialog, "ok", False) and getattr(dialog, "url", ""):
            self.phone_configured.emit(dialog.url)

    def _refresh_account(self) -> None:
        login = self._account_login_id()
        logged = bool(login)
        has_email = bool(
            str((self.config.get("server", {}) or {}).get("account_email") or "").strip()
        )
        self.account_logged_card.setVisible(logged)
        has_phone = bool(
            str((self.config.get("server", {}) or {}).get("account_phone") or "").strip()
        )
        self.account_phone_card.setVisible(logged and not has_phone)
        self.account_bind_card.setVisible(logged and not has_email)
        self.account_login_card.setVisible(not logged)
        self.account_local_card.setVisible(not logged)
        self.account_register_card.setVisible(not logged)
        if logged:
            text = f"{t('settings.account.current')}：{login}"
            phone = str(
                (self.config.get("server", {}) or {}).get("account_phone") or ""
            ).strip()
            if phone:
                text += f"\n{t('settings.account.phone_label')}：{phone}"
            self.account_email_label.setText(text)
        self._refresh_smtp_visibility()

    # -- SMTP / bind email ---------------------------------------------- #
    def _smtp_port(self) -> int:
        try:
            return int(self.smtp_port.text().strip() or 465)
        except ValueError:
            return 465

    def _invalidate_account_service(self) -> None:
        if self._service is not None:
            try:
                self._service.store.close()
            except Exception:
                pass
            self._service = None

    def _on_smtp_changed(self) -> None:
        email_cfg = self.config.setdefault("notify", {}).setdefault("email", {})
        email_cfg["smtp_host"] = self.smtp_host.text().strip() or "smtp.qq.com"
        email_cfg["smtp_port"] = self._smtp_port()
        email_cfg["username"] = self.smtp_username.text().strip()
        email_cfg["password"] = self.smtp_password.text().strip()
        self._invalidate_account_service()
        self._refresh_smtp_visibility()

    def _smtp_configured(self) -> bool:
        email_cfg = (self.config.get("notify", {}) or {}).get("email", {}) or {}
        return bool(
            str(email_cfg.get("username") or "").strip()
            and str(email_cfg.get("password") or "").strip()
        )

    def _refresh_smtp_visibility(self) -> None:
        if not hasattr(self, "smtp_card"):
            return
        has_email = bool(
            str((self.config.get("server", {}) or {}).get("account_email") or "").strip()
        )
        self.smtp_card.setVisible(not (has_email and self._smtp_configured()))

    def _smtp_test(self) -> None:
        email_cfg = (self.config.get("notify", {}) or {}).get("email", {}) or {}
        sender = str(email_cfg.get("username") or "").strip()
        if not sender or not str(email_cfg.get("password") or ""):
            self.smtp_msg.setText(t("account.msg.smtp_need"))
            return
        from maagent.notify.email import EmailNotifier

        ok = EmailNotifier(email_cfg).send_to(
            sender, "Maagent SMTP 测试", "<p>Maagent SMTP 配置正常。</p>"
        )
        self.smtp_msg.setText(t("account.msg.smtp_ok") if ok else t("account.msg.smtp_fail"))

    def _account_send_bind_code(self) -> None:
        from maagent.server.accounts import AccountError

        email = self.account_bind_email.text().strip().lower()
        if not email:
            self.account_bind_msg.setText(t("account.msg.need_fields"))
            return
        try:
            result = self._account_service().send_code(email, "bind_email", enforce_limits=True)
        except AccountError as e:
            self.account_bind_msg.setText(self._account_error_text(e))
            return
        self.account_bind_msg.setText(
            t("account.msg.code_sent") if result["sent"] else t("account.msg.mail_failed")
        )

    def _account_bind_email(self) -> None:
        from maagent.server.accounts import AccountError

        email = self.account_bind_email.text().strip().lower()
        code = self.account_bind_code.text().strip()
        if not email or not code:
            self.account_bind_msg.setText(t("account.msg.need_fields"))
            return
        try:
            account = self._account_service().bind_email(
                self._account_login_id(), email, code
            )
        except AccountError as e:
            self.account_bind_msg.setText(self._account_error_text(e))
            return
        self.account_bind_msg.setText("")
        self.account_bind_code.clear()
        name = str(account.get("username") or account.get("email") or email)
        self.account_changed.emit(
            str(account.get("email") or email), name, str(account.get("phone") or "")
        )
        self._refresh_account()

    def _account_bind_phone(self) -> None:
        from maagent.server.accounts import AccountError

        phone = self.account_phone_input.text().strip()
        if not phone:
            self.account_phone_msg.setText(t("account.msg.need_fields"))
            return
        try:
            account = self._account_service().bind_phone(self._account_login_id(), phone)
        except AccountError as e:
            self.account_phone_msg.setText(self._account_error_text(e))
            return
        self.account_phone_msg.setText(t("account.msg.phone_bound"))
        self.account_phone_input.clear()
        name = str(
            account.get("username") or account.get("email") or self._account_login_id()
        )
        self.account_changed.emit(
            self._account_login_id(), name, str(account.get("phone") or phone)
        )
        self._refresh_account()

    def _account_login(self) -> None:
        from maagent.server.accounts import AccountError

        login_name = self.account_email.text().strip().lower()
        password = self.account_password.text()
        if not login_name or not password:
            self.account_login_msg.setText(t("account.msg.need_fields"))
            return
        try:
            account = self._account_service().login(login_name, password)
        except AccountError as e:
            self.account_login_msg.setText(self._account_error_text(e))
            return
        self.account_login_msg.setText("")
        self.account_password.clear()
        self.account_changed.emit(
            login_name, str(account.get("username") or login_name), str(account.get("phone") or "")
        )
        self._refresh_account()

    def _account_create_local(self) -> None:
        from maagent.server.accounts import AccountError

        username = self.account_local_username.text().strip()
        password = self.account_local_password.text()
        password2 = self.account_local_password2.text()
        if not username or not password:
            self.account_local_msg.setText(t("account.msg.need_fields"))
            return
        if password != password2:
            self.account_local_msg.setText(t("account.msg.password_mismatch"))
            return
        try:
            account = self._account_service().create_local(username, password)
        except AccountError as e:
            self.account_local_msg.setText(self._account_error_text(e))
            return
        self.account_local_msg.setText("")
        self.account_local_password.clear()
        self.account_local_password2.clear()
        name = str(account.get("username") or username)
        self.account_changed.emit(name, name, "")
        self._refresh_account()

    def _account_logout(self) -> None:
        self.account_changed.emit("", "", "")
        self._refresh_account()

    def _account_send_code(self) -> None:
        from maagent.server.accounts import AccountError

        email = self.account_reg_email.text().strip().lower()
        if not email:
            self.account_reg_msg.setText(t("account.msg.need_fields"))
            return
        try:
            result = self._account_service().send_code(email, "register")
        except AccountError as e:
            self.account_reg_msg.setText(self._account_error_text(e))
            return
        self.account_reg_msg.setText(
            t("account.msg.code_sent") if result["sent"] else t("account.msg.mail_failed")
        )

    def _account_register(self) -> None:
        from maagent.server.accounts import AccountError

        email = self.account_reg_email.text().strip().lower()
        phone = self.account_reg_phone.text().strip()
        password = self.account_reg_password.text()
        password2 = self.account_reg_password2.text()
        code = self.account_reg_code.text().strip()
        if not email or not password or not code:
            self.account_reg_msg.setText(t("account.msg.need_fields"))
            return
        if len(password) < 8:
            self.account_reg_msg.setText(t("account.msg.weak_password"))
            return
        if password != password2:
            self.account_reg_msg.setText(t("account.msg.password_mismatch"))
            return
        try:
            account = self._account_service().register(email, phone or None, password, code)
        except AccountError as e:
            self.account_reg_msg.setText(self._account_error_text(e))
            return
        self.account_reg_msg.setText("")
        self.account_changed.emit(
            email, str(account.get("username") or email), str(account.get("phone") or "")
        )
        self._refresh_account()

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
        self.schedule_warning_switch = ToggleSwitch(True, width=42, height=22)
        rows = QWidget()
        rows_layout = QVBoxLayout(rows)
        rows_layout.setContentsMargins(0, 0, 0, 0)
        rows_layout.setSpacing(14)
        rows_layout.addWidget(self._toggle_row(t("settings.autostart"), self.autostart_switch))
        rows_layout.addWidget(self._toggle_row(t("settings.tray"), self.tray_switch))
        rows_layout.addWidget(self._email_log_group())
        rows_layout.addWidget(
            self._toggle_row(t("settings.schedule_warning"), self.schedule_warning_switch)
        )
        layout.addWidget(self._section(t("settings.general.section"), rows))
        layout.addStretch(1)
        return page

    def _email_log_group(self) -> QWidget:
        box = QWidget()
        box_layout = QVBoxLayout(box)
        box_layout.setContentsMargins(0, 0, 0, 0)
        box_layout.setSpacing(10)
        title = QLabel(t("settings.email_log"))
        title.setObjectName("SettingsField")
        box_layout.addWidget(title)
        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(18, 0, 0, 0)
        inner_layout.setSpacing(10)
        self.email_log_switches: dict[str, ToggleSwitch] = {}
        for status in LOG_STATUSES:
            switch = ToggleSwitch(False, width=42, height=22)
            self.email_log_switches[status] = switch
            inner_layout.addWidget(
                self._toggle_row(t(f"settings.email_log.{status}"), switch)
            )
        box_layout.addWidget(inner)
        return box

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
        layout.addSpacing(10)
        hint = QLabel(t("settings.about.clear_hint"))
        hint.setObjectName("SettingsPageDesc")
        hint.setAlignment(Qt.AlignCenter)
        hint.setWordWrap(True)
        layout.addWidget(hint)
        clear_button = QPushButton(t("settings.about.clear"))
        clear_button.setCursor(Qt.PointingHandCursor)
        clear_button.clicked.connect(self.clear_cache_requested.emit)
        clear_row = QHBoxLayout()
        clear_row.addStretch(1)
        clear_row.addWidget(clear_button)
        clear_row.addStretch(1)
        layout.addLayout(clear_row)
        layout.addStretch(2)
        return page

    def _reset_hotkeys(self) -> None:
        self.hotkey_start.set_value("F8")
        self.hotkey_stop.set_value("F9")

    def select_page(self, index: int) -> None:
        if 0 <= index < len(self.nav_buttons):
            self.nav_buttons[index].setChecked(True)
            self.stack.setCurrentIndex(index)

    # -- load / result -------------------------------------------------- #
    def reload(self) -> None:
        app_cfg = self.config.get("app", {}) or {}
        self.lang_group.set_value(app_cfg.get("language", DEFAULT_LANGUAGE))
        self.theme_group.set_value(app_cfg.get("theme", "light"))

        self.autostart_switch.setChecked(bool(app_cfg.get("autostart", False)))
        self.tray_switch.setChecked(bool(app_cfg.get("minimize_to_tray", True)))
        self.schedule_warning_switch.setChecked(bool(app_cfg.get("schedule_warning", True)))
        email = (self.config.get("notify", {}) or {}).get("email", {}) or {}
        when = set(
            normalize_log_statuses(email.get("send_log_on", email.get("send_log", False)))
        )
        for status, switch in self.email_log_switches.items():
            switch.setChecked(status in when)

        smtp_fields = (self.smtp_host, self.smtp_port, self.smtp_username, self.smtp_password)
        for field in smtp_fields:
            field.blockSignals(True)
        self.smtp_host.setText(str(email.get("smtp_host") or "smtp.qq.com"))
        self.smtp_port.setText(str(email.get("smtp_port") or 465))
        self.smtp_username.setText(str(email.get("username") or ""))
        self.smtp_password.setText(str(email.get("password") or ""))
        for field in smtp_fields:
            field.blockSignals(False)

        hotkeys = app_cfg.get("hotkeys", {}) or {}
        self.hotkey_start.set_value(str(hotkeys.get("start", "F8")))
        self.hotkey_stop.set_value(str(hotkeys.get("stop", "F9")))

        self._refresh_account()
        self._refresh_phone()

    def result_settings(self) -> dict:
        return {
            "language": self.lang_group.value(),
            "theme": self.theme_group.value(),
            "autostart": self.autostart_switch.isChecked(),
            "minimize_to_tray": self.tray_switch.isChecked(),
            "schedule_warning": self.schedule_warning_switch.isChecked(),
            "send_log_on": [
                status
                for status, switch in self.email_log_switches.items()
                if switch.isChecked()
            ],
            "smtp": {
                "host": self.smtp_host.text().strip() or "smtp.qq.com",
                "port": self._smtp_port(),
                "username": self.smtp_username.text().strip(),
                "password": self.smtp_password.text().strip(),
            },
            "hotkeys": {
                "start": self.hotkey_start.value(),
                "stop": self.hotkey_stop.value(),
            },
        }
