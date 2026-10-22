from __future__ import annotations

import subprocess
import time
import io
from pathlib import Path

from loguru import logger
from PIL import Image
from maagent.agent.budget import remaining



class AdbGameController:
    """Bounded ADB operations; no native controller connection wait in frozen apps."""

    def __init__(self, adb_path, address, screenshot_dir=None):
        self.adb_path, self.address = str(adb_path), address
        self.screenshot_dir = Path(screenshot_dir) if screenshot_dir else None
        self.resolution = (0, 0)

    def _run(self, *args, binary=False):
        result = subprocess.run([self.adb_path, '-s', self.address, *args],
                                capture_output=True, timeout=remaining(25))
        if result.returncode:
            raise RuntimeError(result.stderr.decode('utf-8', errors='replace')[:300])
        return result.stdout if binary else result.stdout.decode('utf-8', errors='replace')

    def connect(self):
        return self._run('get-state').strip() == 'device'

    def screenshot(self, save=False):
        image = Image.open(io.BytesIO(self._run('exec-out', 'screencap', '-p', binary=True))).convert('RGB')
        self.resolution = image.size
        if save and self.screenshot_dir:
            self.screenshot_dir.mkdir(parents=True, exist_ok=True)
            image.save(self.screenshot_dir / f'screen_{time.time_ns()}.png')
        return image

    def click(self, x, y):
        self._run('shell', 'input', 'tap', str(int(x)), str(int(y)))

    def swipe(self, x1, y1, x2, y2, duration=300):
        self._run('shell', 'input', 'swipe', *map(str, map(int, (x1, y1, x2, y2, duration))))

    def press_key(self, key):
        self._run('shell', 'input', 'keyevent', str(int(key)))

    def shell(self, cmd):
        return self._run('shell', cmd)


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
        from maa.controller import AdbController
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
