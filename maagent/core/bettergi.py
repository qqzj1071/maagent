from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from loguru import logger

from maagent.control.bettergi import (
    GAME_PROCESSES,
    PROCESS_NAME,
    BetterGiUI,
)
from maagent.control.process import close_maa, process_running, spawn
from maagent.core.base import BaseWorkflow
from maagent.report.generator import RunReport

GAME_NAME = "原神"

# 日志标记：证明「一条龙」已真正开始 / 结束（与 MAA/MaaEnd 一致，用日志判定而非按钮状态）。
RUNNING_MARKERS = ("启用一条龙配置", "一条龙任务执行", "任务启动")
DONE_MARKERS = ("一条龙和配置组任务结束",)
CANCEL_MARKERS = ("任务被取消", "没有配置", "退出执行")

_TASK_TOTAL_RE = re.compile(r"启用任务总数量[:：]\s*(\d+)")
_TASK_PROGRESS_RE = re.compile(r"一条龙任务执行[:：]\s*(\d+)/(\d+)")
_GROUP_LOAD_RE = re.compile(r'配置组\s*"([^"]+)"\s*加载完成，共(\d+)个脚本')
_TASK_NAME_RE = re.compile(r'→ "([^"]+)"\s*开始')


def parse_one_dragon_summary(lines: list[str]) -> dict[str, Any]:
    """从 BetterGI 主日志片段中提取「一条龙」任务的执行摘要。"""
    joined = "\n".join(lines)
    total: int | None = None
    match = _TASK_TOTAL_RE.search(joined)
    if match:
        total = int(match.group(1))

    progress: tuple[int, int] | None = None
    for match in _TASK_PROGRESS_RE.finditer(joined):
        progress = (int(match.group(1)), int(match.group(2)))

    task_names: list[str] = []
    for match in _TASK_NAME_RE.finditer(joined):
        name = match.group(1)
        if name not in task_names:
            task_names.append(name)

    groups: list[tuple[str, int]] = []
    for match in _GROUP_LOAD_RE.finditer(joined):
        groups.append((match.group(1), int(match.group(2))))

    return {
        "total": total,
        "progress": progress,
        "task_names": task_names,
        "groups": groups,
        "done": "一条龙和配置组任务结束" in joined,
        "cancelled": ("任务被取消" in joined) or ("没有配置" in joined),
    }


class LogTailer:
    """按字节偏移增量读取 BetterGI 的主日志（文件被占用，需共享读）。"""

    def __init__(self, log_dir: str | None) -> None:
        self.log_dir = Path(log_dir) if log_dir else None
        self._file: Path | None = None
        self._pos = 0
        self._snap()

    def _newest(self) -> Path | None:
        if self.log_dir is None or not self.log_dir.is_dir():
            return None
        files = sorted(
            self.log_dir.glob("better-genshin-impact*.log"),
            key=lambda p: p.stat().st_mtime,
        )
        return files[-1] if files else None

    def _snap(self) -> None:
        f = self._newest()
        self._file = f
        self._pos = 0
        if f is not None:
            try:
                self._pos = f.stat().st_size
            except OSError:
                self._pos = 0

    def read_new(self) -> list[str]:
        f = self._newest()
        if f is None:
            return []
        if self._file is None or f != self._file:
            # 首次读取（此前无日志）或日志滚动到新文件：从文件头开始读
            self._file, self._pos = f, 0
        try:
            size = f.stat().st_size
        except OSError:
            return []
        if size < self._pos:
            self._pos = 0
        if size <= self._pos:
            return []
        try:
            with open(f, "r", encoding="utf-8", errors="replace") as fh:
                fh.seek(self._pos)
                data = fh.read(size - self._pos)
                self._pos = size
        except Exception as e:  # noqa: BLE001 - transient lock
            logger.debug("BetterGI：读取日志失败: {}", e)
            return []
        return [ln for ln in data.splitlines() if ln.strip()]


class BetterGiOrchestrator(BaseWorkflow):
    """Runs 原神 dailies by driving BetterGI (更好的原神).

    maagent 只负责打开 BetterGI、点「启动」拉起原神、切到「一条龙」并点击开始
    三角按钮；之后 BetterGI 自己完成一条龙任务并在结束时自动关闭原神，maagent
    通过日志（一条龙和配置组任务结束）与游戏进程退出判断完成，最后关闭 BetterGI。

    BetterGI 提权运行（需注入游戏），因此 maagent 也必须提权（--uac-admin）。
    """

    steps = 5

    def __init__(
        self, config: dict[str, Any], stop_event: threading.Event | None = None
    ) -> None:
        super().__init__(config, stop_event)
        self.cfg = (config.get("adapters", {}) or {}).get("bgi", {}) or {}
        self._ui = BetterGiUI(PROCESS_NAME, self.cfg)
        self._tailer = LogTailer(self.cfg.get("log_dir"))
        self._lines: list[str] = []
        self._run_start: datetime | None = None

    def _game_name(self) -> str:
        return GAME_NAME

    def _auto_close_enabled(self) -> bool:
        return bool(self.cfg.get("auto_close", False))

    def _auto_close(self) -> None:
        # 原神已由 BetterGI 自行关闭，这里只关 BetterGI。
        logger.info("任务完成，关闭 BetterGI")
        close_maa(PROCESS_NAME)

    def _on_stop(self) -> None:
        # BetterGI 默认取消任务热键是 F12；但跨进程发送键盘可能失败，这里仅关 BetterGI。
        logger.info("停止原神日常，关闭 BetterGI")
        close_maa(PROCESS_NAME)

    # ------------------------------------------------------------------ #
    def _run(self, report: RunReport) -> None:
        cfg = self.cfg
        self._run_start = datetime.now()

        logger.info("=== 原神 步骤 1/5: 打开 BetterGI ===")
        if not process_running(PROCESS_NAME):
            logger.info("BetterGI 未运行，正在启动...")
            if not self._launch():
                report.status = "failed"
                report.errors.append("启动 BetterGI 失败")
                return
        if not self._wait_window(float(cfg.get("wait_seconds", 40))):
            report.status = "failed"
            report.errors.append("未找到 BetterGI 主窗口")
            return
        self._check_stop()

        logger.info("=== 原神 步骤 2/5: 点击「启动」拉起原神 ===")
        game_running = any(process_running(p) for p in GAME_PROCESSES)
        if not game_running:
            if not self._click_start_game(float(cfg.get("start_click_timeout", 60))):
                report.status = "failed"
                report.errors.append("未能点击 BetterGI 的「启动」按钮")
                return
            if not self._wait_game(float(cfg.get("game_start_timeout", 300))):
                report.status = "failed"
                report.errors.append("原神进程未启动")
                return
        else:
            logger.info("原神已在运行，跳过「启动」")
        self._check_stop()

        logger.info("=== 原神 步骤 3/5: 切换到「一条龙」并点击开始 ===")
        if not self._click_one_dragon(float(cfg.get("one_dragon_timeout", 40))):
            report.status = "failed"
            report.errors.append("未能切换到「一条龙」栏目")
            return
        time.sleep(1.0)
        if not self._click_task_start(float(cfg.get("task_start_timeout", 40))):
            report.status = "failed"
            report.errors.append("未能点击「一条龙」任务开始按钮")
            return

        logger.info("=== 原神 步骤 4/5: 等待一条龙任务完成 ===")
        result = self._wait_done(float(cfg.get("task_timeout_seconds", 3600)))

        logger.info("=== 原神 步骤 5/5: 汇总日志并生成报告 ===")
        # 等最后一批脚本的 ExecutionRecords 落盘，避免漏读
        time.sleep(2)
        self._populate(report)

        if result == "timeout":
            report.status = "timeout"
            report.errors.append(
                f"一条龙任务超过 {cfg.get('task_timeout_seconds', 3600)} 秒未结束"
            )
        elif result == "cancelled":
            report.status = "failed"
            report.errors.append("一条龙任务被取消或没有配置")
        elif result == "not_started":
            report.status = "failed"
            report.errors.append("一条龙任务未能启动")
        elif report.errors:
            report.status = "failed"
        elif report.warnings:
            report.status = "warning"
        else:
            report.status = "success"

    # ------------------------------------------------------------------ #
    def _launch(self) -> bool:
        exe = self.cfg.get("executable")
        if not exe:
            logger.warning("原神：未配置 BetterGI executable")
            return False
        mode = spawn(exe, cwd=self.cfg.get("path") or str(Path(exe).parent))
        if mode is None:
            logger.error("启动 BetterGI 失败")
            return False
        return True

    def _wait_window(self, timeout: float) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            self._check_stop()
            if self._ui.hwnd() is not None:
                return True
            time.sleep(2.0)
        return False

    def _click_start_game(self, timeout: float) -> bool:
        """点「启动」，随后原神进程应出现（含重试）。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            self._check_stop()
            if self._ui.click_start_game():
                return True
            time.sleep(3.0)
        return False

    def _wait_game(self, timeout: float) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            self._check_stop()
            if any(process_running(p) for p in GAME_PROCESSES):
                return True
            time.sleep(5.0)
        return False

    def _click_one_dragon(self, timeout: float) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            self._check_stop()
            if self._ui.click_one_dragon():
                return True
            time.sleep(3.0)
        return False

    def _click_task_start(self, timeout: float) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            self._check_stop()
            if self._ui.click_task_start():
                return True
            time.sleep(3.0)
        return False

    def _wait_done(self, timeout: float) -> str:
        deadline = time.time() + timeout
        running = False
        saw_game = any(process_running(p) for p in GAME_PROCESSES)
        while time.time() < deadline:
            self._check_stop()
            lines = self._tailer.read_new()
            if lines:
                self._lines.extend(lines)
            joined = "\n".join(lines)
            if any(k in joined for k in DONE_MARKERS):
                logger.info("原神：一条龙任务已结束")
                return "complete"
            if any(k in joined for k in CANCEL_MARKERS):
                logger.warning("原神：一条龙任务被取消")
                return "cancelled"
            if any(k in joined for k in RUNNING_MARKERS):
                running = True
            game_running = any(process_running(p) for p in GAME_PROCESSES)
            if running and saw_game and not game_running:
                # 任务结束 BetterGI 会关闭原神；日志尚未刷出结束标记时以此兜底。
                logger.info("原神：游戏进程已退出，判定任务结束")
                return "complete"
            if game_running:
                saw_game = True
            time.sleep(5.0)
        return "not_started" if not running else "timeout"

    def _read_execution_results(self) -> list[dict[str, Any]]:
        """读取 BetterGI 的 ExecutionRecords，筛出本次运行期间执行的脚本记录。

        每条记录含 ``is_successful``，用于确认任务是否真正顺利完成（部分
        地图追踪脚本可能失败但一条龙仍会继续跑完）。
        """
        log_dir = self.cfg.get("log_dir")
        if not log_dir:
            return []
        path = Path(log_dir) / "ExecutionRecords" / f"{datetime.now():%Y%m%d}.json"
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return []
        records = data.get("execution_records") or []
        run_start = self._run_start or datetime.now() - timedelta(seconds=30)
        floor = run_start - timedelta(seconds=30)
        result: list[dict[str, Any]] = []
        for record in records:
            try:
                start = datetime.fromisoformat(str(record.get("start_time") or ""))
                if start.tzinfo is not None:
                    start = start.replace(tzinfo=None)
            except (TypeError, ValueError):
                continue
            if start >= floor:
                result.append(record)
        return result

    def _populate(self, report: RunReport) -> None:
        summary = parse_one_dragon_summary(self._lines)
        total = summary.get("total")
        progress = summary.get("progress")
        groups: list[tuple[str, int]] = summary.get("groups") or []

        # 一条龙任务整体完成情况
        if summary.get("done"):
            report.tasks = f"一条龙 {total} 项任务全部完成" if total else "一条龙任务全部完成"
        elif summary.get("cancelled"):
            report.tasks = f"一条龙 {total} 项任务被取消" if total else "一条龙任务被取消"
        elif progress:
            report.tasks = f"一条龙 {progress[0]}/{progress[1]} 项任务"
        elif total:
            report.tasks = f"一条龙 {total} 项任务"

        # 配置组（地图追踪等脚本组）
        if groups:
            report.extra.append(
                ("配置组", "、".join(f"{name}({count}脚本)" for name, count in groups))
            )

        # 脚本级成功/失败（ExecutionRecords）
        records = self._read_execution_results()
        if records:
            ok = sum(1 for r in records if r.get("is_successful"))
            report.extra.append(("脚本执行", f"{ok}/{len(records)} 成功"))
            for record in records:
                if not record.get("is_successful"):
                    name = record.get("project_name") or record.get("name") or "未知脚本"
                    report.warnings.append(f"{name} 执行失败")

        report.logs = self._lines[-50:]
