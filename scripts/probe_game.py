"""Phase 0 probe: connect MuMu via MaaFramework, screenshot, OCR, optional click.

Usage:
    .venv\\Scripts\\python.exe scripts\\probe_game.py --save
    .venv\\Scripts\\python.exe scripts\\probe_game.py --click 640,360
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import yaml
from loguru import logger

from maagent.agent.framework import MaaGameController, resolve_mumu_serial

DEFAULT_MANAGER = "D:/tools/MuMuPlayer/nx_main/MuMuManager.exe"
DEFAULT_ADB = "D:/tools/MuMuPlayer/nx_main/adb.exe"


def _load_emulator(config_path: Path) -> dict:
    for candidate in (config_path, Path("config/config.example.yaml")):
        if candidate.exists():
            with open(candidate, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            emu = ((cfg.get("adapters") or {}).get("maa") or {}).get("emulator") or {}
            return {
                "manager": emu.get("manager_path") or DEFAULT_MANAGER,
                "adb": emu.get("adb_path") or DEFAULT_ADB,
                "vmindex": int(emu.get("vmindex") or 0),
            }
    return {"manager": DEFAULT_MANAGER, "adb": DEFAULT_ADB, "vmindex": 0}


def main() -> int:
    parser = argparse.ArgumentParser(description="MaaFw 感知-动作探针")
    parser.add_argument("--config", default="config/config.yaml")
    parser.add_argument("--manager", default=None, help="MuMuManager.exe 路径")
    parser.add_argument("--adb", default=None, help="adb.exe 路径")
    parser.add_argument("--vmindex", type=int, default=None)
    parser.add_argument("--address", default=None, help="设备地址，默认经 MuMuManager 自动获取")
    parser.add_argument("--click", default=None, help="点击坐标 x,y")
    parser.add_argument("--save", action="store_true", help="保存截图到 logs/probe")
    args = parser.parse_args()

    emu = _load_emulator(Path(args.config))
    manager = args.manager or emu["manager"]
    adb = args.adb or emu["adb"]
    vmindex = args.vmindex if args.vmindex is not None else emu["vmindex"]

    address = args.address or resolve_mumu_serial(manager, adb, vmindex)
    if not address:
        logger.error("未发现模拟器设备，请先启动 MuMu 实例 {} 后重试", vmindex)
        return 1
    logger.info("设备地址: {}", address)

    game = MaaGameController(adb, address, screenshot_dir="logs/probe")
    if not game.connect():
        return 1

    img = game.screenshot(save=args.save)
    logger.info("截图尺寸: {} 原始分辨率: {}", img.size, game.resolution)

    items = game.ocr(img)
    logger.info("OCR 识别到 {} 条文本", len(items))
    for item in items[:15]:
        logger.info("  {:.2f} {} @{}", item.score, item.text, item.center)

    if args.click:
        x, y = (int(v) for v in args.click.split(","))
        game.click(x, y)
        logger.info("已点击 ({}, {})", x, y)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
