from __future__ import annotations

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QObject,
    QPropertyAnimation,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


class LogBridge(QObject):
    """Bridges loguru records into a Qt signal for the log view."""

    message = Signal(str)


class SoftwareCard(QFrame):
    clicked = Signal(str)

    def __init__(self, key: str, name: str, desc: str, enabled: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.key = key
        self._enabled = enabled
        self._selected = False
        self.setObjectName("Card")
        self.setFixedSize(164, 88)
        self.setCursor(Qt.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 9, 12, 9)
        layout.setSpacing(2)

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
        self.header.setChecked(False)
        self.header.setArrowType(Qt.RightArrow)
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
        self.body.setVisible(False)
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
