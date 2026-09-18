from __future__ import annotations

from dataclasses import dataclass, field

STATUS_LABEL = {
    "success": "✅ 成功",
    "warning": "⚠️ 完成（子任务报错）",
    "failed": "❌ 失败",
    "timeout": "⏱️ 超时",
    "stopped": "⏹️ 已急停",
    "unknown": "❓ 未知",
}

STATUS_COLOR = {
    "success": "#2e7d32",
    "warning": "#ef6c00",
    "failed": "#c62828",
    "timeout": "#ef6c00",
    "stopped": "#6b7280",
}


def format_duration(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h} 小时 {m} 分 {s} 秒"
    if m:
        return f"{m} 分 {s} 秒"
    return f"{s} 秒"


@dataclass
class RunReport:
    game: str
    status: str = "unknown"
    started_at: str = ""
    finished_at: str = ""
    duration: str = ""
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    sanity: str = ""
    next_deadline: str = ""
    annihilation: str = ""
    monthly: str = ""
    tasks: str = ""
    extra: list[tuple[str, str]] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)
    popups_closed: list[dict] = field(default_factory=list)

    @property
    def status_label(self) -> str:
        return STATUS_LABEL.get(self.status, self.status)

    def _rows(self) -> list[tuple[str, str, bool]]:
        """(label, value, emphasize) rows shared by text/html output."""
        rows: list[tuple[str, str, bool]] = [
            ("开始", self.started_at or "未知", False),
            ("结束", self.finished_at or "未知", False),
            ("耗时", self.duration or "未知", False),
            ("报错", "无" if not self.errors else "；".join(self.errors), bool(self.errors)),
        ]
        if self.warnings:
            rows.append(("提示", "；".join(self.warnings), False))
        rows.extend((label, value, False) for label, value in self.extra)
        if self.tasks:
            rows.append(("完成任务", self.tasks, False))
        if self.sanity:
            rows.append(("剩余理智", self.sanity, False))
        if self.next_deadline:
            rows.append(("下次最晚开始", self.next_deadline, False))
        if self.annihilation:
            rows.append(("剿灭作战", self.annihilation, self.annihilation.startswith("⚠️")))
        if self.monthly:
            rows.append(("月常购买", self.monthly, False))
        return rows

    def to_text(self) -> str:
        lines = [f"【maagent 日常报告】{self.game}（{self.status_label}）"]
        rows = self._rows()
        lines.append(
            f"开始: {rows[0][1]}    结束: {rows[1][1]}    耗时: {rows[2][1]}"
        )
        for label, value, _ in rows[3:]:
            lines.append(f"{label}: {value}")
        return "\n".join(lines)

    def to_html(self) -> str:
        color = STATUS_COLOR.get(self.status, "#555")
        body = []
        for label, value, emphasize in self._rows():
            if emphasize:
                body.append(
                    f'<tr><td><b>{label}</b></td>'
                    f'<td style="color:#c62828;font-weight:600">{value}</td></tr>'
                )
            else:
                body.append(f"<tr><td><b>{label}</b></td><td>{value}</td></tr>")
        rows_html = "\n".join(body)
        return f"""<html><body style="font-family:sans-serif;font-size:14px">
<h3>【maagent 日常报告】{self.game} <span style="color:{color}">{self.status_label}</span></h3>
<table cellpadding="4" style="border-collapse:collapse">
{rows_html}
</table>
</body></html>"""
