from __future__ import annotations

import ctypes
import json
import re
import subprocess
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from loguru import logger

from maagent.control.maaend import (
    GAME_PROCESS,
    PROCESS_NAME,
    START_HOTKEY,
    STOP_HOTKEY,
    MaaEndUI,
    press_hotkey,
    process_running,
)
from maagent.control.process import close_maa
from maagent.control.maaend_api import DEFAULT_PORT, MaaEndApi
from maagent.core.orchestrator import WorkflowStopped
from maagent.notify.email import EmailNotifier
from maagent.report.generator import RunReport, format_duration

GAME_NAME = "明日方舟：终末地"
ERROR_TYPES = {"error", "warning", "warn"}
ERROR_KEYWORDS = ("失败", "错误", "异常", "出错", "无法", "超时")

# 终末地理智：7 分 12 秒恢复 1 点（24 小时 200 点）
SANITY_RECOVER_SECONDS = 7 * 60 + 12
SHOP_ITEMS = {
    "item_gachabyproducts_weapongold": "武库配额",
    "item_diamond": "嵌晶玉",
}

SW_SHOWNORMAL = 1
ERROR_ELEVATION_REQUIRED = 740


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def is_elevated() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def shell_execute_runas(program: str, args: str = "", cwd: str | None = None) -> bool:
    try:
        res = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", program, args or None, cwd, SW_SHOWNORMAL
        )
        return int(res) > 32
    except Exception as e:
        logger.warning("提权启动失败 {}: {}", program, e)
        return False


def spawn(program: str, args: str = "", cwd: str | None = None,
          prefer_elevated: bool = False) -> str | None:
    """Launch a program, escalating via UAC only when really needed."""
    if prefer_elevated and not is_elevated():
        return "runas" if shell_execute_runas(program, args, cwd) else None
    try:
        cmd = [program] + (args.split() if args else [])
        subprocess.Popen(cmd, cwd=cwd)
        return "normal"
    except OSError as e:
        if getattr(e, "winerror", None) == ERROR_ELEVATION_REQUIRED:
            logger.info("{} 需要管理员权限，请求提权...", Path(program).name)
            return "runas" if shell_execute_runas(program, args, cwd) else None
        logger.warning("启动 {} 失败: {}", program, e)
        return None


class MaaEndOrchestrator:
    """Runs 明日方舟：终末地 dailies by letting MaaEnd do the work.

    maagent only opens MaaEnd and triggers its 开始任务 (F10, or a click as a
    fallback). MaaEnd then runs its preActions — launching Endfield itself —
    and the configured tasks. maagent watches MaaEnd's HTTP API for progress
    and builds the report from its structured logs.

    Injecting the hotkey/click needs maagent to run elevated, because MaaEnd
    runs elevated too (Windows UIPI blocks a lower-integrity process).
    """

    def __init__(
        self, config: dict[str, Any], stop_event: threading.Event | None = None
    ) -> None:
        self.config = config
        self._stop = stop_event
        self.cfg = (config.get("adapters", {}) or {}).get("maaend", {}) or {}

    def _check_stop(self) -> None:
        if self._stop is not None and self._stop.is_set():
            raise WorkflowStopped()

    def _api(self) -> MaaEndApi:
        return MaaEndApi(port=int(self.cfg.get("web_port", DEFAULT_PORT)))

    def run_daily(self) -> RunReport:
        started = time.time()
        report = RunReport(game=GAME_NAME, started_at=_now())
        try:
            self._run(report)
        except WorkflowStopped:
            logger.warning("收到停止请求，已中止终末地工作流")
            self._stop_task()
            report.status = "stopped"
            report.errors.append("用户停止，工作流已中止")
        except Exception as e:
            logger.exception("终末地工作流异常: {}", e)
            report.status = "failed"
            report.errors.append(f"异常: {e}")
        if not report.finished_at:
            report.finished_at = _now()
        if not report.duration:
            report.duration = format_duration(time.time() - started)
        self._finish(report)
        return report

    # ------------------------------------------------------------------ #
    def _run(self, report: RunReport) -> None:
        cfg = self.cfg
        logger.info("=== 终末地 步骤 1/5: 打开 MaaEnd ===")
        if not process_running(PROCESS_NAME):
            logger.info("MaaEnd 未运行，正在启动...")
            if self._launch() is None:
                report.status = "failed"
                report.errors.append("启动 MaaEnd 失败")
                return
        api = self._api()
        if not self._wait_api(api, float(cfg.get("wait_seconds", 30))):
            report.status = "failed"
            report.errors.append("MaaEnd HTTP API 不可用")
            return
        instance_id = api.instance_id()
        if not instance_id:
            report.status = "failed"
            report.errors.append("未找到 MaaEnd 实例")
            return
        logger.info("MaaEnd 实例: {}", instance_id)
        self._ensure_global_hotkeys(api)
        log_dir = cfg.get("log_dir")
        if not self._wait_hotkeys(log_dir, 30):
            logger.warning("终末地：未等到 MaaEnd 全局热键注册，稍后仍会尝试")
        self._check_stop()

        logger.info("=== 终末地 步骤 2/5: 触发「开始任务」（由 MaaEnd 启动游戏）===")
        since = self._last_log_timestamp(api, instance_id)
        start_timeout = float(cfg.get("task_start_timeout", 120))
        if not self._trigger_start(api, instance_id, start_timeout):
            report.status = "failed"
            report.errors.append("未能触发 MaaEnd 开始任务")
            return

        logger.info("=== 终末地 步骤 3/5: 等待任务完成 ===")
        result = self._wait_done(
            api, instance_id, since,
            float(cfg.get("game_start_timeout", 240)),
            float(cfg.get("task_timeout_seconds", 3600)),
        )

        logger.info("=== 终末地 步骤 4/5: 汇总日志并生成报告 ===")
        time.sleep(2)
        entries = [
            e for e in ((api.logs() or {}).get(instance_id) or [])
            if str(e.get("timestamp", "")) > since
        ]
        self._populate(report, entries, cfg.get("log_dir"))

        if result == "timeout":
            report.status = "timeout"
            report.errors.append(
                f"终末地任务超过 {cfg.get('task_timeout_seconds', 3600)} 秒未结束"
            )
        elif result == "not_started":
            report.status = "failed"
            report.errors.append("任务未能启动（控制器未连接或提交失败）")
        elif report.errors:
            report.status = "failed"
        elif report.warnings:
            report.status = "warning"
        else:
            report.status = "success"

    # ------------------------------------------------------------------ #
    def _launch(self) -> str | None:
        exe = self.cfg.get("executable")
        if not exe:
            logger.warning("终末地：未配置 executable")
            return None
        mode = spawn(exe, cwd=self.cfg.get("path") or str(Path(exe).parent))
        if mode is None:
            logger.error("启动 MaaEnd 失败")
        return mode

    def _wait_api(self, api: MaaEndApi, timeout: float) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            self._check_stop()
            if api.ping():
                return True
            time.sleep(2.0)
        return False

    @staticmethod
    def _newest_app_log(log_dir: str | None) -> Path | None:
        if not log_dir:
            return None
        root = Path(log_dir)
        if not root.is_dir():
            return None
        files = [
            p for p in root.glob("*.log")
            if re.match(r"\d{4}-\d{2}-\d{2}-\d+\.log$", p.name)
        ]
        return max(files, key=lambda p: p.stat().st_mtime) if files else None

    def _wait_hotkeys(self, log_dir: str | None, timeout: float) -> bool:
        """MaaEnd registers its global hotkeys a few seconds after launch."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            self._check_stop()
            path = self._newest_app_log(log_dir)
            if path:
                try:
                    if "全局快捷键已注册" in path.read_text(encoding="utf-8", errors="replace"):
                        logger.info("终末地：MaaEnd 全局热键已注册")
                        return True
                except Exception:
                    pass
            time.sleep(1.0)
        return False

    def _ensure_global_hotkeys(self, api: MaaEndApi) -> None:
        try:
            cfg = api.config()
            settings = cfg.setdefault("settings", {})
            hotkeys = settings.setdefault("hotkeys", {})
            if hotkeys.get("globalEnabled") and hotkeys.get("startTasks"):
                return
            hotkeys["startTasks"] = hotkeys.get("startTasks") or START_HOTKEY
            hotkeys["stopTasks"] = hotkeys.get("stopTasks") or STOP_HOTKEY
            hotkeys["globalEnabled"] = True
            api.save_config(cfg)
            logger.info("终末地：已启用 MaaEnd 全局热键 {} / {}",
                        hotkeys["startTasks"], hotkeys["stopTasks"])
            time.sleep(1.5)
        except Exception as e:
            logger.warning("终末地：启用全局热键失败: {}", e)

    def _trigger_start(self, api: MaaEndApi, instance_id: str, timeout: float) -> bool:
        since = self._last_log_timestamp(api, instance_id)
        logger.info("终末地：发送热键 {}", START_HOTKEY)
        press_hotkey(START_HOTKEY)
        if self._wait_started(api, instance_id, since, min(timeout, 30)):
            return True
        logger.info("终末地：热键未生效，尝试点击「开始任务」按钮")
        since = self._last_log_timestamp(api, instance_id)
        if MaaEndUI().press_start() and self._wait_started(api, instance_id, since, 30):
            return True
        return False

    @staticmethod
    def _new_messages(api: MaaEndApi, instance_id: str, since: str) -> list[str]:
        try:
            entries = (api.logs() or {}).get(instance_id) or []
        except Exception:
            return []
        return [
            str(e.get("message") or "")
            for e in entries
            if str(e.get("timestamp", "")) > since
        ]

    def _wait_started(
        self, api: MaaEndApi, instance_id: str, since: str, timeout: float
    ) -> bool:
        """MaaEnd logs 检测到快捷键 / 正在执行前置程序 as soon as it accepts the start."""
        markers = ("检测到快捷键", "正在执行前置程序", "任务开始")
        deadline = time.time() + timeout
        while time.time() < deadline:
            self._check_stop()
            if any(any(k in m for k in markers) for m in self._new_messages(api, instance_id, since)):
                logger.info("终末地：MaaEnd 已接受开始任务")
                return True
            time.sleep(2.0)
        return False

    def _wait_done(
        self, api: MaaEndApi, instance_id: str, since: str,
        start_timeout: float, timeout: float,
    ) -> str:
        """The game must boot (preAction → window → connect → resource) before the
        task actually runs, which can take a minute or two."""
        start_deadline = time.time() + start_timeout
        running = False
        while time.time() < start_deadline:
            self._check_stop()
            if any("结束进程" in m and "完成" in m for m in self._new_messages(api, instance_id, since)):
                logger.info("终末地：任务已结束")
                return "complete"
            try:
                if api.instance_state(instance_id).get("is_running"):
                    running = True
                    break
            except Exception:
                pass
            time.sleep(3.0)
        if not running:
            logger.warning("终末地：等待任务进入运行状态超时")
            return "not_started"

        logger.info("终末地：任务运行中，等待结束...")
        deadline = time.time() + timeout
        while time.time() < deadline:
            self._check_stop()
            try:
                if not api.instance_state(instance_id).get("is_running"):
                    logger.info("终末地：任务已结束")
                    return "complete"
            except Exception:
                pass
            time.sleep(5.0)
        return "timeout"

    @staticmethod
    def _last_log_timestamp(api: MaaEndApi, instance_id: str) -> str:
        try:
            entries = (api.logs() or {}).get(instance_id) or []
            return str(entries[-1].get("timestamp", "")) if entries else ""
        except Exception:
            return ""

    @staticmethod
    def _collect_details(log_dir: str | None) -> dict[str, Any]:
        """Read MaaEnd's agent/framework logs for the values we report."""
        details: dict[str, Any] = {"sanity": None, "essence": None, "purchases": {}}
        if not log_dir:
            return details
        root = Path(log_dir)
        if not root.is_dir():
            return details
        purchases: dict[str, int] = {}
        gosvc = root / "go-service.log"
        if gosvc.exists():
            for line in gosvc.read_text(encoding="utf-8", errors="replace").splitlines():
                if '"component":"AddItemData"' in line and '"item_id"' in line:
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    item_id = obj.get("item_id")
                    if item_id in SHOP_ITEMS:
                        purchases[item_id] = purchases.get(item_id, 0) + int(obj.get("delta", 0))
                elif '"component":"EssenceFilter"' in line and '"matched_total"' in line:
                    try:
                        details["essence"] = int(json.loads(line)["matched_total"])
                    except Exception:
                        pass
        details["purchases"] = purchases
        for pattern in ("*.log", "*.bak.*.log", "cpp-algo/debug/*.log"):
            for path in root.glob(pattern):
                try:
                    text = path.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    continue
                for match in re.finditer(r"当前理智\s*(\d+)\s*/\s*(\d+)", text):
                    details["sanity"] = (int(match.group(1)), int(match.group(2)))
                # fallback source for the 未来可期 count
                for match in re.finditer(r"未来可期」命中：\s*(\d+)\s*个", text):
                    details["essence"] = int(match.group(1))
        return details

    @classmethod
    def _populate(
        cls, report: RunReport, entries: list[dict[str, Any]], log_dir: str | None
    ) -> None:
        errors: list[str] = []      # fatal: the run itself could not proceed
        warnings: list[str] = []    # a single sub-task hiccup; the run still completed
        for entry in entries:
            message = str(entry.get("message") or "").strip()
            if not message:
                continue
            message = message.replace("\n", " ")
            task = re.match(r"任务(?:失败|出错)[:：]\s*(.+)", message)
            if task:
                warnings.append(f"{task.group(1).strip()} 执行异常")
            elif "任务失败" in message or "任务出错" in message:
                warnings.append(message)
            elif entry.get("type") in ERROR_TYPES or any(k in message for k in ERROR_KEYWORDS):
                errors.append(message)
        report.errors = errors[:20]
        report.warnings = warnings[:20]
        report.logs = [f"{e.get('timestamp','')} {e.get('message','')}" for e in entries]

        details = cls._collect_details(log_dir)
        extra: list[tuple[str, str]] = []

        # 信用点购物：武库配额 / 嵌晶玉
        purchases = details.get("purchases") or {}
        bought = [
            f"{label} +{purchases[item_id]}"
            for item_id, label in SHOP_ITEMS.items()
            if purchases.get(item_id, 0) > 0
        ]
        extra.append(("信用点购物", "，".join(bought) if bought else "本次未购买武库配额与嵌晶玉"))

        # 基质刷取：未来可期
        essence = details.get("essence")
        extra.append((
            "基质刷取",
            f"未来可期 {essence} 个" if essence is not None else "未来可期 未获取到数据",
        ))

        # 理智：不足 160 时用理智药剂（每个 +40）补到能双倍领取为止
        sanity = details.get("sanity")
        if sanity:
            current, maximum = sanity
            potions = 0 if current >= 160 else -(-(160 - current) // 40)
            effective = current + 40 * potions
            remaining = max(0, effective - 160)
            note = f"（使用 {potions} 个药剂恢复理智）" if potions else ""
            report.sanity = f"{remaining}/{maximum}{note}"
            next_dt = datetime.now() + timedelta(
                seconds=(maximum - remaining) * SANITY_RECOVER_SECONDS
            )
            report.next_deadline = next_dt.strftime("%m-%d %H:%M")

        report.extra = extra

    def _stop_task(self) -> None:
        try:
            press_hotkey(STOP_HOTKEY)
            logger.info("终末地：已发送热键 {} 停止任务", STOP_HOTKEY)
        except Exception as e:
            logger.warning("终末地：停止任务失败: {}", e)

    def _finish(self, report: RunReport) -> None:
        logger.info("=== 终末地：生成报告并发送邮件 ===")
        email_cfg = self.config.get("notify", {}).get("email", {})
        EmailNotifier(email_cfg).send(
            f"[maagent] {report.game}日常 - {report.status_label}", report.to_html()
        )
        logger.info("报告:\n{}", report.to_text())

        if self.config.get("workflow", {}).get("auto_close"):
            logger.info("任务完成，关闭 MaaEnd 与终末地")
            try:
                close_maa(PROCESS_NAME)
                close_maa(GAME_PROCESS)
            except Exception as e:
                logger.warning("关闭 MaaEnd 失败: {}", e)
