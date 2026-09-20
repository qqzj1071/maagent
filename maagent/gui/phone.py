from __future__ import annotations

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from loguru import logger

from maagent.i18n import t


class PhoneRemoteWorker(QThread):
    """Runs the Tailscale install/login/funnel setup off the UI thread."""

    progress = Signal(str)
    completed = Signal(bool, str)

    def __init__(self, port: int, parent=None) -> None:
        super().__init__(parent)
        self.port = port

    def run(self) -> None:
        from maagent.control import tailscale as ts

        try:
            ok, message = ts.setup(self.port, self.progress.emit)
        except Exception as e:  # pragma: no cover - defensive
            logger.exception("手机远程配置异常: {}", e)
            ok, message = False, str(e)
        self.completed.emit(ok, message)


class PhoneRemoteDialog(QDialog):
    def __init__(self, port: int, parent=None) -> None:
        super().__init__(parent)
        self.port = port
        self.url = ""
        self.ok = False
        self._worker: PhoneRemoteWorker | None = None

        self.setWindowTitle(t("phone.dialog.title"))
        self.setMinimumWidth(540)
        layout = QVBoxLayout(self)
        desc = QLabel(t("phone.dialog.desc"))
        desc.setWordWrap(True)
        layout.addWidget(desc)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMinimumHeight(180)
        layout.addWidget(self.log_view)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(10)
        layout.addWidget(self.progress)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self._progress_timer = QTimer(self)
        self._progress_timer.setInterval(400)
        self._progress_timer.timeout.connect(self._tick_progress)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.close_btn = QPushButton(t("phone.dialog.close"))
        self.close_btn.setCursor(Qt.PointingHandCursor)
        self.close_btn.clicked.connect(self.reject)
        self.start_btn = QPushButton(t("phone.dialog.start"))
        self.start_btn.setObjectName("Primary")
        self.start_btn.setCursor(Qt.PointingHandCursor)
        self.start_btn.clicked.connect(self.start_setup)
        buttons.addWidget(self.close_btn)
        buttons.addWidget(self.start_btn)
        layout.addLayout(buttons)

    # Cosmetic step weights so the bar advances at each stage.
    _PROGRESS_HINTS = (
        ("检测 Tailscale", 5),
        ("下载", 18),
        ("安装", 38),
        ("登录", 62),
        ("Funnel", 82),
        ("手机网页地址", 100),
    )

    def _tick_progress(self) -> None:
        if self.progress.value() < 95:
            self.progress.setValue(self.progress.value() + 1)

    def _bump_progress(self, text: str) -> None:
        for hint, value in self._PROGRESS_HINTS:
            if hint in text and self.progress.value() < value:
                self.progress.setValue(value)
                break

    def _append(self, text: str) -> None:
        self.log_view.appendPlainText(text)
        logger.info("手机远程: {}", text)
        self._bump_progress(text)

    def start_setup(self) -> None:
        self.start_btn.setEnabled(False)
        self.close_btn.setEnabled(False)
        self.progress.setValue(0)
        self.status.setText(t("phone.dialog.running"))
        self._progress_timer.start()
        self._worker = PhoneRemoteWorker(self.port, self)
        self._worker.progress.connect(self._append)
        self._worker.completed.connect(self._on_done)
        self._worker.start()

    def _on_done(self, ok: bool, message: str) -> None:
        self._progress_timer.stop()
        self._append(message)
        self.ok = ok
        self.url = message if ok else ""
        if ok:
            self.progress.setValue(100)
        self.status.setText(t("phone.dialog.ok") if ok else t("phone.dialog.fail"))
        self.start_btn.setEnabled(not ok)
        self.close_btn.setEnabled(True)

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._worker is not None and self._worker.isRunning():
            event.ignore()
            return
        super().closeEvent(event)
