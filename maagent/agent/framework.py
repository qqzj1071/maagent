from __future__ import annotations

import subprocess
import time
from pathlib import Path

from loguru import logger
from PIL import Image

from maa.controller import AdbController


def _first_adb_serial(adb_path: str | Path, timeout: float = 30.0) -> str | None:
    try:
        out = subprocess.run(
            [str(adb_path), "devices"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
        ).stdout
    except Exception as e:
        logger.warning("adb devices 失败: {}", e)
        return None
    for line in out.splitlines()[1:]:
        line = line.strip()
        if not line or "\t" not in line:
            continue
        serial, state = line.split("\t", 1)
        if state.strip() == "device":
            return serial.strip()
    return None


def resolve_mumu_serial(
    manager_path: str | Path,
    adb_path: str | Path,
    vmindex: int = 0,
    timeout: float = 30.0,
) -> str | None:
    """Ask MuMuManager to connect adb, then return the device serial.

    MuMu's adb port is dynamic, so we let MuMuManager wire it up and read the
    resulting serial (e.g. ``127.0.0.1:16384``) back from ``adb devices``.
    """
    try:
        subprocess.run(
            [str(manager_path), "adb", "-v", str(vmindex), "connect"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
        )
    except Exception as e:
        logger.warning("MuMuManager adb connect 失败: {}", e)
    for _ in range(5):
        serial = _first_adb_serial(adb_path, timeout)
        if serial:
            return serial
        time.sleep(1.0)
    return None


class MaaGameController:
    """Low-level game control (screencap / touch) via MaaFramework's adb controller."""

    def __init__(
        self,
        adb_path: str | Path,
        address: str,
        screenshot_dir: str | Path | None = None,
        screenshot_long_side: int | None = None,
        raw_size: bool = True,
    ) -> None:
        self.adb_path = str(adb_path)
        self.address = address
        self.controller = AdbController(self.adb_path, address)
        if screenshot_long_side:
            self.controller.set_screenshot_target_long_side(int(screenshot_long_side))
        if raw_size:
            # 让截图尺寸等于设备原始分辨率，使截图坐标与点击坐标 1:1 对应
            self.controller.set_screenshot_use_raw_size(True)
        self.screenshot_dir = Path(screenshot_dir) if screenshot_dir else None
        if self.screenshot_dir:
            self.screenshot_dir.mkdir(parents=True, exist_ok=True)

    def connect(self) -> bool:
        job = self.controller.post_connection()
        job.wait()
        if not job.succeeded:
            logger.error("连接模拟器失败: {}", self.address)
            return False
        logger.info("已连接 {} 原始分辨率 {}", self.address, self.resolution)
        return True

    @property
    def connected(self) -> bool:
        return bool(self.controller.connected)

    @property
    def resolution(self) -> tuple[int, int]:
        return self.controller.resolution

    def screenshot(self, save: bool = False) -> Image.Image:
        job = self.controller.post_screencap()
        job.wait()
        arr = job.get()
        if arr is None or arr.size == 0:
            raise RuntimeError("截图失败：空图像")
        img = Image.fromarray(arr[..., ::-1])  # MaaFw 返回 BGR
        if save and self.screenshot_dir:
            path = self.screenshot_dir / f"screen_{int(time.time() * 1000)}.png"
            img.save(path)
            logger.info("已保存截图 {}", path)
        return img

    def click(self, x: int, y: int) -> None:
        self.controller.post_click(int(x), int(y)).wait()

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration: int = 300) -> None:
        self.controller.post_swipe(int(x1), int(y1), int(x2), int(y2), int(duration)).wait()

    def press_key(self, key: int) -> None:
        self.controller.post_press_key(int(key)).wait()

    def shell(self, cmd: str) -> str | None:
        job = self.controller.post_shell(cmd)
        job.wait()
        return job.get()

    def ocr(self, image: Image.Image | None = None):
        from maagent.control.popup import recognize

        return recognize(image if image is not None else self.screenshot())
