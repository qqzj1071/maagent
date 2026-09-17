import json
import sys
import time
from pathlib import Path

import win32gui

sys.stdout.reconfigure(errors="replace")

from maagent.control.logmonitor import MaaLogMonitor
from maagent.control.popup import MaaPopupMonitor, main_window
from maagent.core.orchestrator import Orchestrator
from maagent.report.generator import RunReport

STATE = Path("logs/daily_logs.json")
POPUPS = Path("logs/daily_popups.json")


def main() -> int:
    duration = int(sys.argv[1]) if len(sys.argv) > 1 else 1800
    hwnd = main_window()
    if not hwnd:
        print("MAA 主窗口未找到")
        return 1

    lm = MaaLogMonitor(hwnd)
    mon = MaaPopupMonitor(debug_dir="logs/popups")
    if STATE.exists():
        lm.logs = json.loads(STATE.read_text(encoding="utf-8"))
        print(f"载入历史日志 {len(lm.logs)} 行")

    start = time.time()
    result = "timeout"
    while time.time() - start < duration:
        mon.scan_once()
        lm.collect_logs()
        STATE.write_text(json.dumps(lm.logs, ensure_ascii=False, indent=2), encoding="utf-8")
        POPUPS.write_text(json.dumps(mon.closed, ensure_ascii=False, indent=2), encoding="utf-8")
        if not win32gui.IsWindow(hwnd):
            result = "exited"
            break
        state = lm.button_state()
        print(f"[{int(time.time() - start)}s] button={state} logs={len(lm.logs)} popups={len(mon.closed)}")
        if lm.detect_complete():
            result = "complete"
            break
        if state == "idle":
            result = "idle"
            break
        time.sleep(8)

    report = RunReport(
        game="明日方舟",
        status="success" if result in ("complete", "idle", "exited") else "timeout",
        logs=lm.logs,
        popups_closed=mon.closed,
        errors=lm.detect_errors(),
    )
    Orchestrator._populate_from_logs(report)
    Path("logs/report.txt").write_text(report.to_text(), encoding="utf-8")
    Path("logs/report.html").write_text(report.to_html(), encoding="utf-8")
    print("result:", result)
    print(report.to_text())
    return 0


if __name__ == "__main__":
    sys.exit(main())
