from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml
from loguru import logger
from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from maagent.control.process import close_all
from maagent.core.orchestrator import Orchestrator
from maagent.log.logger import setup_logger


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
        self._build_ui()
        self.bridge.message.connect(self.append_log)

    def _build_ui(self) -> None:
        self.setWindowTitle("maagent - 二游日常助手")
        self.resize(860, 660)
        central = QWidget()
        layout = QVBoxLayout(central)

        self.status_label = QLabel("状态: 空闲")
        layout.addWidget(self.status_label)

        btn_row = QHBoxLayout()
        self.btn_start = QPushButton("开始日常")
        self.btn_start.clicked.connect(self.start_daily)
        self.btn_close = QPushButton("关闭 MAA / 模拟器")
        self.btn_close.clicked.connect(self.do_close_all)
        self.btn_config = QPushButton("打开配置目录")
        self.btn_config.clicked.connect(self.open_config_dir)
        for b in (self.btn_start, self.btn_close, self.btn_config):
            btn_row.addWidget(b)
        layout.addLayout(btn_row)

        log_box = QGroupBox("运行日志")
        log_layout = QVBoxLayout(log_box)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        log_layout.addWidget(self.log_view)
        layout.addWidget(log_box, 3)

        rep_box = QGroupBox("任务报告")
        rep_layout = QVBoxLayout(rep_box)
        self.report_view = QPlainTextEdit()
        self.report_view.setReadOnly(True)
        rep_layout.addWidget(self.report_view)
        layout.addWidget(rep_box, 2)

        self.setCentralWidget(central)

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

    def closeEvent(self, event) -> None:
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
