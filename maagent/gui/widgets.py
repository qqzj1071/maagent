from __future__ import annotations

from uuid import uuid4

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QObject,
    QPoint,
    QPropertyAnimation,
    QTime,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTimeEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from maagent.core.scheduler import parse_hhmm
from maagent.i18n import t

WEEKDAY_LABELS = ["一", "二", "三", "四", "五", "六", "日"]


def set_button_role(button, role: str) -> None:
    """Swap a QPushButton's objectName (Primary/Danger/Ghost) and restyle it."""
    button.setObjectName(role)
    button.style().unpolish(button)
    button.style().polish(button)


class LogBridge(QObject):
    """Bridges loguru records into a Qt signal for the log view."""

    message = Signal(str)


class SoftwareCard(QFrame):
    clicked = Signal(str)

    def __init__(self, key: str, name: str, desc: str, enabled: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.key = key
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
        self.state_label = QLabel(t("card.enabled") if enabled else t("card.disabled"))
        self.state_label.setObjectName("CardState")
        self.state_label.setStyleSheet("color: #4f46e5;" if enabled else "color: #9ca3af;")
        layout.addWidget(self.state_label)

        self.setProperty("inactive", not enabled)
        self.setProperty("selected", False)

    def set_selected(self, value: bool) -> None:
        self.setProperty("selected", value)
        self.style().unpolish(self)
        self.style().polish(self)

    def set_state_text(self, text: str, active: bool = True) -> None:
        """Update only the state label/dot colour, keeping the card clickable."""
        self.state_label.setText(text)
        self.state_label.setStyleSheet("color: #4f46e5;" if active else "color: #9ca3af;")
        self.dot.setStyleSheet("color: #4f46e5;" if active else "color: #d1d5db;")

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

    def add_layout(self, layout) -> None:
        self.body.addLayout(layout)


class WeekdayPicker(QWidget):
    """A compact row of 7 toggle chips (Mon..Sun)."""

    changed = Signal()

    def __init__(
        self,
        selected: list[int] | None = None,
        large: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.buttons: list[QPushButton] = []
        selected_set = set(selected or [])
        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6 if large else 4)
        for i, label in enumerate(WEEKDAY_LABELS):
            btn = QPushButton(label)
            btn.setObjectName("DayChip")
            btn.setCheckable(True)
            btn.setChecked(i in selected_set)
            btn.setCursor(Qt.PointingHandCursor)
            if large:
                btn.setMinimumWidth(40)
                btn.setFixedHeight(32)
            btn.clicked.connect(self.changed.emit)
            self.buttons.append(btn)
            layout.addWidget(btn, 0, i)

    def selected_days(self) -> list[int]:
        return [i for i, btn in enumerate(self.buttons) if btn.isChecked()]


class TimeListEditor(QWidget):
    """Editable chain start slots: each row is a time plus its own run days."""

    changed = Signal()

    def __init__(self, slots: list | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows: list[tuple[QWidget, QTimeEdit, WeekdayPicker]] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._rows_box = QWidget()
        self._rows_layout = QVBoxLayout(self._rows_box)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(6)
        layout.addWidget(self._rows_box)

        self.add_button = QPushButton("＋ 添加时间")
        self.add_button.setObjectName("Ghost")
        self.add_button.setCursor(Qt.PointingHandCursor)
        self.add_button.clicked.connect(self.add_slot)
        layout.addWidget(self.add_button, 0, Qt.AlignLeft)

        for slot in (slots or [{"time": "08:00", "days": list(range(7))}]):
            self._append_row(slot)

    def _append_row(self, slot) -> None:
        if isinstance(slot, dict):
            time_value = slot.get("time")
            days = slot.get("days")
        else:
            time_value, days = slot, None
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(8)
        edit = QTimeEdit()
        edit.setDisplayFormat("HH:mm")
        minutes = parse_hhmm(time_value)
        if minutes is None:
            minutes = 8 * 60
        edit.setTime(QTime(minutes // 60, minutes % 60))
        edit.timeChanged.connect(lambda _=None: self.changed.emit())
        picker = WeekdayPicker(days, large=True)
        picker.changed.connect(self.changed.emit)
        remove = QPushButton("✕")
        remove.setObjectName("Ghost")
        remove.setFixedWidth(28)
        remove.setCursor(Qt.PointingHandCursor)
        remove.clicked.connect(lambda _=False, target=row: self._remove_row(target))
        row_layout.addWidget(edit)
        row_layout.addWidget(picker)
        row_layout.addStretch(1)
        row_layout.addWidget(remove)
        self._rows_layout.addWidget(row)
        self._rows.append((row, edit, picker))

    def _remove_row(self, row: QWidget) -> None:
        if len(self._rows) <= 1:
            return
        for index, (widget, _edit, _picker) in enumerate(self._rows):
            if widget is row:
                self._rows.pop(index)
                self._rows_layout.removeWidget(widget)
                widget.deleteLater()
                break
        self.changed.emit()

    def add_slot(self) -> None:
        self._append_row({"time": "08:00", "days": list(range(7))})
        self.changed.emit()

    def slots(self) -> list[dict]:
        return [
            {"time": edit.time().toString("HH:mm"), "days": picker.selected_days()}
            for _row, edit, picker in self._rows
        ]


class DragHandle(QLabel):
    """The grip that starts a drag-reorder.

    It handles its own mouse events and reports global cursor positions; the
    panel turns those into an insertion indicator and the final reorder. Qt's
    QDrag/drop is deliberately not used: a child label never gives the parent
    row the implicit mouse grab, which made the drag unreliable.
    """

    drag_started = Signal(str, QPoint)
    drag_moved = Signal(str, QPoint)
    drag_ended = Signal(str, QPoint)

    def __init__(self, payload: str, parent: QWidget | None = None) -> None:
        super().__init__("≡", parent)
        self.payload = payload
        self.setObjectName("DragHandle")
        self.setToolTip("按住拖动排序")
        self.setCursor(Qt.OpenHandCursor)
        self.setFixedWidth(16)
        self._press_pos = None
        self._active = False

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._press_pos = event.position().toPoint()
            self._active = False
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._press_pos is None or not (event.buttons() & Qt.LeftButton):
            return
        global_pos = event.globalPosition().toPoint()
        if not self._active:
            distance = (event.position().toPoint() - self._press_pos).manhattanLength()
            if distance < QApplication.startDragDistance():
                return
            self._active = True
            self.setCursor(Qt.ClosedHandCursor)
            self.drag_started.emit(self.payload, global_pos)
        self.drag_moved.emit(self.payload, global_pos)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._active:
            self.drag_ended.emit(self.payload, event.globalPosition().toPoint())
            self._active = False
            self.setCursor(Qt.OpenHandCursor)
        self._press_pos = None
        super().mouseReleaseEvent(event)


class WorkflowTaskRow(QFrame):
    """One reorderable daily-work entry inside the 日常工作流 panel."""

    changed = Signal()
    copy_requested = Signal(str)  # task id
    delete_requested = Signal(str)  # task id

    def __init__(
        self,
        software: str,
        name: str,
        desc: str,
        task: dict | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        task = task or {}
        self.software = software
        self.task_id = str(task.get("id") or f"{software}-{uuid4().hex[:6]}")
        self.setObjectName("TaskRow")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 8, 10, 8)
        outer.setSpacing(6)

        top = QHBoxLayout()
        top.setSpacing(8)
        self.handle = DragHandle(self.task_id)
        top.addWidget(self.handle)
        self.order_label = QLabel("1")
        self.order_label.setObjectName("TaskOrder")
        self.order_label.setFixedWidth(18)
        top.addWidget(self.order_label)
        title = QLabel(name)
        title.setObjectName("CardName")
        top.addWidget(title)
        desc_label = QLabel(desc)
        desc_label.setObjectName("CardDesc")
        top.addWidget(desc_label)
        top.addStretch(1)

        enable_label = QLabel("启用")
        enable_label.setObjectName("ToggleLabel")
        top.addWidget(enable_label)
        self.enable_switch = ToggleSwitch(bool(task.get("enabled", True)), width=38, height=20)
        self.enable_switch.toggled.connect(lambda _=False: self.changed.emit())
        top.addWidget(self.enable_switch)

        self.btn_copy = QPushButton("复制")
        self.btn_copy.setObjectName("Ghost")
        self.btn_copy.setCursor(Qt.PointingHandCursor)
        self.btn_copy.setToolTip("复制该条目，可单独设置时间")
        self.btn_copy.clicked.connect(lambda: self.copy_requested.emit(self.task_id))
        top.addWidget(self.btn_copy)

        self.btn_delete = QPushButton("✕")
        self.btn_delete.setObjectName("Ghost")
        self.btn_delete.setFixedWidth(28)
        self.btn_delete.setCursor(Qt.PointingHandCursor)
        self.btn_delete.setToolTip("删除该条目")
        self.btn_delete.clicked.connect(lambda: self.delete_requested.emit(self.task_id))
        top.addWidget(self.btn_delete)

        outer.addLayout(top)

        self.schedule_box = QWidget()
        sched = QVBoxLayout(self.schedule_box)
        sched.setContentsMargins(26, 0, 0, 0)
        sched.setSpacing(6)
        time_row = QHBoxLayout()
        time_row.setSpacing(8)
        time_label = QLabel("定时")
        time_label.setObjectName("ToggleLabel")
        time_row.addWidget(time_label)
        self.time_edit = QTimeEdit()
        self.time_edit.setDisplayFormat("HH:mm")
        minutes = parse_hhmm(task.get("time"))
        if minutes is None:
            minutes = 8 * 60
        self.time_edit.setTime(QTime(minutes // 60, minutes % 60))
        self.time_edit.timeChanged.connect(lambda _=None: self.changed.emit())
        time_row.addWidget(self.time_edit)
        time_row.addStretch(1)
        sched.addLayout(time_row)
        self.days = WeekdayPicker(task.get("days"))
        self.days.changed.connect(self.changed.emit)
        sched.addWidget(self.days)
        outer.addWidget(self.schedule_box)

    def set_order(self, index: int) -> None:
        self.order_label.setText(str(index))

    def set_mode(self, mode: str) -> None:
        scheduled = mode == "scheduled"
        self.schedule_box.setVisible(scheduled)
        self.btn_copy.setVisible(scheduled)
        self.btn_delete.setVisible(scheduled)

    def is_enabled(self) -> bool:
        return self.enable_switch.isChecked()

    def time_str(self) -> str:
        return self.time_edit.time().toString("HH:mm")

    def selected_days(self) -> list[int]:
        return self.days.selected_days()

    def set_dragging(self, value: bool) -> None:
        self.setProperty("dragging", bool(value))
        self.style().unpolish(self)
        self.style().polish(self)


class TaskListEditor(QWidget):
    """A reorderable list of daily-work rows for one workflow mode.

    ``scheduled`` lists allow duplicated entries (copy/delete/add), each with its
    own time and days; chain (non-scheduled) lists keep one entry per software and
    only allow reordering. Reordering is a manual drag with an insertion line, so
    the user can see whether a task lands before or after the hovered row.
    """

    changed = Signal()

    def __init__(
        self,
        scheduled: bool,
        meta: dict[str, tuple[str, str]],
        softwares: list[str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.scheduled = bool(scheduled)
        self.meta = dict(meta)
        self.softwares = list(softwares)
        self.rows: list[WorkflowTaskRow] = []
        self._drag_payload: str | None = None
        self._drag_source: WorkflowTaskRow | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.rows_box = QWidget()
        self.rows_layout = QVBoxLayout(self.rows_box)
        self.rows_layout.setContentsMargins(0, 6, 0, 6)
        self.rows_layout.setSpacing(8)
        layout.addWidget(self.rows_box)

        self.indicator = QFrame(self.rows_box)
        self.indicator.setObjectName("DropIndicator")
        self.indicator.setFixedHeight(3)
        self.indicator.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.indicator.hide()

        self.add_box: QWidget | None = None
        if self.scheduled:
            self.add_box = QWidget()
            add_row = QHBoxLayout(self.add_box)
            add_row.setContentsMargins(0, 0, 0, 0)
            add_row.setSpacing(6)
            hint = QLabel(t("wf.add_entry"))
            hint.setObjectName("ToggleLabel")
            add_row.addWidget(hint)
            for key in self.softwares:
                name = self.meta.get(key, (key.upper(), ""))[0]
                button = QPushButton(f"＋ {name}")
                button.setObjectName("Ghost")
                button.setCursor(Qt.PointingHandCursor)
                button.clicked.connect(lambda _=False, sw=key: self.add_task(sw))
                add_row.addWidget(button)
            add_row.addStretch(1)
            layout.addWidget(self.add_box)

    # -- data ----------------------------------------------------------- #
    @staticmethod
    def _new_id(software: str) -> str:
        return f"{software}-{uuid4().hex[:6]}"

    def _make_row(self, task: dict) -> WorkflowTaskRow:
        key = task["software"]
        name, desc = self.meta.get(key, (key.upper(), ""))
        row = WorkflowTaskRow(key, name, desc, task)
        row.set_mode("scheduled" if self.scheduled else "sequential")
        row.changed.connect(self.changed.emit)
        row.copy_requested.connect(self.duplicate_task)
        row.delete_requested.connect(self.delete_task)
        row.handle.drag_started.connect(self._on_drag_started)
        row.handle.drag_moved.connect(self._on_drag_moved)
        row.handle.drag_ended.connect(self._on_drag_ended)
        return row

    def set_tasks(self, tasks: list[dict]) -> None:
        for row in self.rows:
            self.rows_layout.removeWidget(row)
            row.deleteLater()
        self.rows.clear()
        for task in tasks:
            row = self._make_row(task)
            self.rows.append(row)
            self.rows_layout.addWidget(row)
        self._relayout()

    def collect(self) -> list[dict]:
        tasks = []
        for row in self.rows:
            task = {"id": row.task_id, "software": row.software, "enabled": row.is_enabled()}
            if self.scheduled:
                task["time"] = row.time_str()
                task["days"] = row.selected_days()
            tasks.append(task)
        return tasks

    def enabled_softwares(self, unique: bool = True) -> list[str]:
        result: list[str] = []
        for row in self.rows:
            if not row.is_enabled():
                continue
            if unique and row.software in result:
                continue
            result.append(row.software)
        return result

    # -- mutations ------------------------------------------------------ #
    def _index(self, task_id: str) -> int | None:
        return next((i for i, row in enumerate(self.rows) if row.task_id == task_id), None)

    def _relayout(self) -> None:
        for row in self.rows:
            self.rows_layout.removeWidget(row)
        for i, row in enumerate(self.rows):
            self.rows_layout.insertWidget(i, row)
            row.set_order(i + 1)
        self._update_delete_buttons()

    def _update_delete_buttons(self) -> None:
        counts: dict[str, int] = {}
        for row in self.rows:
            counts[row.software] = counts.get(row.software, 0) + 1
        for row in self.rows:
            allowed = self.scheduled and counts[row.software] > 1
            row.btn_delete.setEnabled(allowed)
            if self.scheduled:
                name = self.meta.get(row.software, (row.software, ""))[0]
                row.btn_delete.setToolTip(
                    "删除该条目" if allowed else f"{name} 至少保留一个条目"
                )

    def add_task(self, software: str) -> None:
        if not self.scheduled:
            return
        task = {
            "id": self._new_id(software),
            "software": software,
            "enabled": True,
            "time": "08:00",
            "days": list(range(7)),
        }
        row = self._make_row(task)
        self.rows.append(row)
        self.rows_layout.addWidget(row)
        self._relayout()
        self.changed.emit()

    def duplicate_task(self, task_id: str) -> None:
        index = self._index(task_id)
        if index is None:
            return
        source = self.rows[index]
        task = {
            "id": self._new_id(source.software),
            "software": source.software,
            "enabled": source.is_enabled(),
            "time": source.time_str(),
            "days": source.selected_days(),
        }
        row = self._make_row(task)
        self.rows.insert(index + 1, row)
        self.rows_layout.insertWidget(index + 1, row)
        self._relayout()
        self.changed.emit()

    def delete_task(self, task_id: str) -> None:
        index = self._index(task_id)
        if index is None:
            return
        row = self.rows[index]
        if sum(1 for r in self.rows if r.software == row.software) <= 1:
            return
        self.rows.pop(index)
        self.rows_layout.removeWidget(row)
        row.deleteLater()
        self._relayout()
        self.changed.emit()

    # -- drag reorder --------------------------------------------------- #
    def _compute_drop_index(self, global_pos) -> tuple[int, int]:
        pos = self.rows_box.mapFromGlobal(global_pos)
        if not self.rows:
            return 0, 0
        half = self.rows_layout.spacing() // 2
        for i, row in enumerate(self.rows):
            geo = row.geometry()
            if pos.y() < geo.top():
                return i, geo.top() - half
            if pos.y() <= geo.bottom():
                if pos.y() < geo.center().y():
                    return i, geo.top() - half
                return i + 1, geo.bottom() + half
        last = self.rows[-1].geometry()
        return len(self.rows), last.bottom() + half

    def _update_indicator(self, global_pos) -> None:
        if self._drag_payload is None:
            return
        _, y = self._compute_drop_index(global_pos)
        max_y = max(0, self.rows_box.height() - 3)
        self.indicator.setGeometry(0, max(0, min(y, max_y)), self.rows_box.width(), 3)
        self.indicator.raise_()
        self.indicator.show()

    def _on_drag_started(self, payload: str, global_pos) -> None:
        index = self._index(payload)
        if index is None:
            return
        self._drag_payload = payload
        self._drag_source = self.rows[index]
        self._drag_source.set_dragging(True)
        self._update_indicator(global_pos)

    def _on_drag_moved(self, payload: str, global_pos) -> None:
        if self._drag_payload != payload:
            return
        self._update_indicator(global_pos)

    def _on_drag_ended(self, payload: str, global_pos) -> None:
        if self._drag_payload != payload:
            return
        insert_index, _ = self._compute_drop_index(global_pos)
        if self._drag_source is not None:
            self._drag_source.set_dragging(False)
        self._drag_payload = None
        self._drag_source = None
        self.indicator.hide()
        self._apply_reorder(payload, insert_index)

    def _apply_reorder(self, payload: str, insert_index: int) -> None:
        source_index = self._index(payload)
        if source_index is None or insert_index in (source_index, source_index + 1):
            return
        row = self.rows.pop(source_index)
        if insert_index > source_index:
            insert_index -= 1
        insert_index = max(0, min(insert_index, len(self.rows)))
        self.rows.insert(insert_index, row)
        self._relayout()
        self.changed.emit()
