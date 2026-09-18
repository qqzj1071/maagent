import argparse
import sys
from pathlib import Path

from loguru import logger

from maagent.adapters.maa import MaaAdapter
from maagent.config import load_config
from maagent.control.popup import MaaPopupMonitor
from maagent.control.process import close_all
from maagent.core.maaend import MaaEndOrchestrator
from maagent.core.orchestrator import Orchestrator
from maagent.log.logger import setup_logger


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="maagent - 二游日常助手")
    parser.add_argument("--config", default=None, help="config.yaml 路径")
    parser.add_argument("--daily", action="store_true", help="完整工作流：启动→关弹窗→LinkStart→监控→报告→邮件")
    parser.add_argument("--maaend", action="store_true", help="终末地工作流：启动 MaaEnd→开始任务→等待结束→报告→邮件")
    parser.add_argument("--workflow", action="store_true", help="按配置顺序立即执行日常工作流")
    parser.add_argument("--run", action="store_true", help="仅启动 MAA 并触发 Link Start")
    parser.add_argument("--launch", action="store_true", help="仅启动 MAA，不触发 Link Start")
    parser.add_argument("--monitor", action="store_true", help="监控并自动关闭 MAA 弹窗")
    parser.add_argument("--close", action="store_true", help="关闭 MAA 与模拟器")
    parser.add_argument("--seconds", type=float, default=60, help="监控时长（秒）")
    args = parser.parse_args(argv)

    default_config = Path(__file__).resolve().parent.parent / "config" / "config.yaml"
    config_path = args.config or str(default_config)
    cfg = load_config(config_path)
    setup_logger(log_dir=cfg.get("app", {}).get("log_dir", "logs"))
    logger.info("已加载配置 {}", config_path)

    maa_cfg = cfg["adapters"]["maa"]

    if args.close:
        close_all(maa_cfg.get("emulator", {}))
        return 0

    if args.workflow:
        chain = (cfg.get("workflow", {}) or {}).get("chain", {}) or {}
        tasks = chain.get("sequential_tasks")
        if tasks is None:
            tasks = chain.get("tasks") or []
        software: list[str] = []
        for item in tasks:
            if (
                isinstance(item, dict)
                and item.get("software")
                and item.get("enabled", True)
                and item["software"] not in software
            ):
                software.append(item["software"])
        if not software:
            logger.warning("日常工作流没有已启用的任务")
            return 1
        logger.info("日常工作流: {}", " → ".join(software))
        for sw in software:
            runner = MaaEndOrchestrator(cfg) if sw == "maaend" else Orchestrator(cfg)
            report = runner.run_daily()
            logger.info("{} 工作流结束，状态: {}", sw, report.status_label)
        return 0

    if args.daily:
        report = Orchestrator(cfg).run_daily()
        logger.info("工作流结束，状态: {}", report.status_label)
        return 0 if report.status == "success" else 1

    if args.maaend:
        report = MaaEndOrchestrator(cfg).run_daily()
        logger.info("终末地工作流结束，状态: {}", report.status_label)
        return 0 if report.status == "success" else 1

    if args.run or args.launch:
        adapter = MaaAdapter()
        adapter.start(maa_cfg)
        if args.run:
            result = adapter.run({"name": "明日方舟日常"})
            logger.info("执行结果: {} - {}", result.status, result.message)
        else:
            logger.info("启动结果: {}", adapter.launch())

    if args.monitor:
        pm_cfg = maa_cfg.get("popup_monitor", {})
        monitor = MaaPopupMonitor(
            debug_dir=pm_cfg.get("debug_dir"),
            dismiss_checkbox=pm_cfg.get("dismiss_checkbox", False),
        )
        closed = monitor.monitor(args.seconds, interval=pm_cfg.get("interval", 2.0))
        logger.info("共关闭弹窗 {} 个: {}", len(closed), closed)

    if not (args.daily or args.maaend or args.workflow or args.run or args.launch or args.monitor):
        parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
