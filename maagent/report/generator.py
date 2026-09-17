from __future__ import annotations

from dataclasses import dataclass, field

STATUS_LABEL = {
    "success": "✅ 成功",
    "failed": "❌ 失败",
    "timeout": "⏱️ 超时",
    "unknown": "❓ 未知",
}


@dataclass
class RunReport:
    game: str
    status: str = "unknown"
    started_at: str = ""
    finished_at: str = ""
    duration: str = ""
    errors: list[str] = field(default_factory=list)
    sanity: str = ""
    next_deadline: str = ""
    annihilation: str = ""
    monthly: str = ""
    logs: list[str] = field(default_factory=list)
    popups_closed: list[dict] = field(default_factory=list)

    @property
    def status_label(self) -> str:
        return STATUS_LABEL.get(self.status, self.status)

    def to_text(self) -> str:
        err = "无" if not self.errors else "；".join(self.errors)
        lines = [
            f"【maagent 日常报告】{self.game}（{self.status_label}）",
            f"开始: {self.started_at or '未知'}    结束: {self.finished_at or '未知'}    耗时: {self.duration or '未知'}",
            f"报错: {err}",
            f"剩余理智: {self.sanity or '未知'}",
            f"下次任务最晚开始: {self.next_deadline or '未知'}",
        ]
        if self.annihilation:
            lines.append(f"剿灭作战: {self.annihilation}")
        if self.monthly:
            lines.append(f"月常购买: {self.monthly}")
        return "\n".join(lines)

    def to_html(self) -> str:
        err = "无" if not self.errors else "<br>".join(self.errors)
        color = {"success": "#2e7d32", "failed": "#c62828", "timeout": "#ef6c00"}.get(self.status, "#555")
        if self.annihilation:
            warn = self.annihilation.startswith("⚠️")
            cell = (
                f'<td style="color:#c62828;font-weight:600">{self.annihilation}</td>'
                if warn else f"<td>{self.annihilation}</td>"
            )
            anni_row = f"<tr><td><b>剿灭作战</b></td>{cell}</tr>"
        else:
            anni_row = ""
        monthly_row = (
            f'<tr><td><b>月常购买</b></td><td>{self.monthly}</td></tr>' if self.monthly else ""
        )
        return f"""<html><body style="font-family:sans-serif;font-size:14px">
<h3>【maagent 日常报告】{self.game} <span style="color:{color}">{self.status_label}</span></h3>
<table cellpadding="4" style="border-collapse:collapse">
<tr><td><b>开始</b></td><td>{self.started_at or '未知'}</td></tr>
<tr><td><b>结束</b></td><td>{self.finished_at or '未知'}</td></tr>
<tr><td><b>耗时</b></td><td>{self.duration or '未知'}</td></tr>
<tr><td><b>报错</b></td><td>{err}</td></tr>
<tr><td><b>剩余理智</b></td><td>{self.sanity or '未知'}</td></tr>
<tr><td><b>下次最晚开始</b></td><td>{self.next_deadline or '未知'}</td></tr>
{anni_row}
{monthly_row}
</table>
</body></html>"""
