"""
ADB Controller cho Emulator Plane (BlueStacks).
Kết nối qua localhost với port cấu hình, tọa độ cố định.
"""

import subprocess
import time
import io
import numpy as np
from PIL import Image
from typing import Optional, Tuple

from src.bluestacks_capture import BlueStacksWindowCapture
from src.logger import GameLogger


class EmulatorAdbController:
    """
    Điều khiển ADB cho BlueStacks emulator.
    Kết nối qua 127.0.0.1:{port}, không cần scale resolution.
    """

    def __init__(self, config: dict, logger: GameLogger):
        self.host = config.get("adb_host", "127.0.0.1")
        self.port = config.get("adb_port", 5555)
        self.adb_path = config.get("adb_path", "adb")
        self.auto_connect = config.get("auto_connect", True)
        self.capture_method = config.get("capture_method", "auto").lower()
        self.window_title = config.get("window_title", "")
        self.normalize_capture_size = config.get("normalize_capture_size", True)
        self.logger = logger
        self._device_serial = f"{self.host}:{self.port}"
        self._connected = False
        self._resolution: Optional[Tuple[int, int]] = None
        self._window_capture: Optional[BlueStacksWindowCapture] = None
        self._active_capture_method: Optional[str] = None

    def _run_adb(self, args: list, timeout: int = 10) -> subprocess.CompletedProcess:
        """Chạy lệnh ADB và trả về kết quả."""
        cmd = [self.adb_path, "-s", self._device_serial] + args
        try:
            result = subprocess.run(
                cmd, capture_output=True, timeout=timeout
            )
            return result
        except subprocess.TimeoutExpired:
            self.logger.error(f"ADB timeout: {' '.join(cmd)}")
            raise
        except FileNotFoundError:
            self.logger.critical(f"Không tìm thấy ADB tại: {self.adb_path}")
            raise

    def connect(self) -> bool:
        """Kết nối đến BlueStacks emulator qua ADB."""
        self.logger.state("CONNECT", f"Kết nối {self._device_serial}...")

        try:
            # Thử connect
            result = subprocess.run(
                [self.adb_path, "connect", self._device_serial],
                capture_output=True, text=True, timeout=10
            )
            output = result.stdout.strip()

            if "connected" in output.lower():
                self._connected = True
                self._resolution = self.get_resolution()
                self.logger.state(
                    "CONNECT",
                    f"✅ Đã kết nối BlueStacks ({self._device_serial}) | "
                    f"Resolution: {self._resolution}"
                )
                return True
            else:
                self.logger.error(f"Kết nối thất bại: {output}")
                return False

        except Exception as e:
            self.logger.error(f"Lỗi kết nối: {e}")
            return False

    def disconnect(self):
        """Ngắt kết nối ADB."""
        try:
            subprocess.run(
                [self.adb_path, "disconnect", self._device_serial],
                capture_output=True, timeout=5
            )
        except Exception:
            pass
        self._connected = False
        self.logger.state("DISCONNECT", f"Ngắt kết nối {self._device_serial}")

    def is_connected(self) -> bool:
        """Kiểm tra emulator còn kết nối không."""
        try:
            result = subprocess.run(
                [self.adb_path, "devices"],
                capture_output=True, text=True, timeout=5
            )
            lines = result.stdout.strip().split("\n")
            for line in lines:
                if self._device_serial in line and "device" in line:
                    self._connected = True
                    return True
            self._connected = False
            return False
        except Exception:
            self._connected = False
            return False

    def get_resolution(self) -> Optional[Tuple[int, int]]:
        """Lấy resolution màn hình emulator."""
        try:
            result = self._run_adb(["shell", "wm", "size"])
            output = result.stdout.decode().strip()
            # Output: "Physical size: 720x1280"
            if "x" in output:
                parts = output.split(":")[-1].strip().split("x")
                w, h = int(parts[0]), int(parts[1])
                self._resolution = (w, h)
                return (w, h)
        except Exception as e:
            self.logger.error(f"Không đọc được resolution: {e}")
        return None

    @staticmethod
    def _usable_frame(frame: Optional[np.ndarray]) -> bool:
        return (
            frame is not None
            and frame.ndim == 3
            and frame.shape[0] > 10
            and frame.shape[1] > 10
            and float(frame.std()) >= 1.0
        )

    def _adb_screencap(self) -> Optional[np.ndarray]:
        try:
            result = self._run_adb(
                ["shell", "screencap", "-p"],
                timeout=5
            )
            if result.returncode != 0 or not result.stdout:
                return None

            img_data = result.stdout
            img_data = img_data.replace(b"\r\n", b"\n")

            with Image.open(io.BytesIO(img_data)) as image:
                frame = np.array(image.convert("RGB"))[:, :, ::-1].copy()
            return frame if self._usable_frame(frame) else None

        except Exception:
            return None

    def _window_screencap(self) -> Optional[np.ndarray]:
        try:
            if self._window_capture is None:
                self._window_capture = BlueStacksWindowCapture(
                    adb_port=self.port,
                    window_title=self.window_title,
                )
            frame = self._window_capture.capture()

            if self.normalize_capture_size and self._resolution:
                target_width, target_height = self._resolution
                if frame.shape[1] != target_width or frame.shape[0] != target_height:
                    rgb = frame[:, :, ::-1]
                    image = Image.fromarray(rgb).resize(
                        (target_width, target_height),
                        Image.Resampling.LANCZOS,
                    )
                    frame = np.array(image)[:, :, ::-1].copy()
            return frame if self._usable_frame(frame) else None
        except Exception as e:
            self.logger.error(f"BlueStacks background capture failed: {e}")
            self._window_capture = None
            return None

    def screencap(self) -> Optional[np.ndarray]:
        """Capture a BGR frame without focusing or interacting with BlueStacks.

        ``adb`` is fastest when the app allows screenshots. ``window`` uses
        the Win32 PrintWindow API and bypasses Android FLAG_SECURE. ``auto``
        tries ADB once and switches to the window backend if it is blocked.
        """
        method = self.capture_method
        if method not in {"auto", "adb", "window"}:
            self.logger.error(f"Unknown capture_method: {method}")
            return None

        if method == "window" or self._active_capture_method == "window":
            frame = self._window_screencap()
            if frame is not None:
                self._active_capture_method = "window"
            return frame

        frame = self._adb_screencap()
        if frame is not None:
            self._active_capture_method = "adb"
            return frame

        if method == "adb":
            self.logger.error("ADB screencap failed or returned a protected frame")
            return None

        self.logger.warning(
            "ADB screencap is blocked (FLAG_SECURE); switching to background "
            "BlueStacks window capture"
        )
        frame = self._window_screencap()
        if frame is not None:
            self._active_capture_method = "window"
        return frame

    def tap(self, x: int, y: int, delay_ms: int = 50):
        """Click vào tọa độ trên emulator."""
        try:
            self._run_adb(
                ["shell", "input", "tap", str(x), str(y)],
                timeout=5
            )
            if delay_ms > 0:
                time.sleep(delay_ms / 1000.0)
        except Exception as e:
            self.logger.error(f"Lỗi tap ({x}, {y}): {e}")

    def multi_tap(self, taps: list, delay_between_ms: int = 80):
        """
        Thực hiện nhiều thao tác tap liên tiếp.
        taps: List of (x, y, count) hoặc (x, y)
        """
        for tap_info in taps:
            if len(tap_info) == 3:
                x, y, count = tap_info
            else:
                x, y = tap_info
                count = 1

            for _ in range(count):
                self.tap(x, y, delay_ms=delay_between_ms)

    def swipe(self, x1: int, y1: int, x2: int, y2: int,
              duration_ms: int = 300):
        """Vuốt từ (x1,y1) đến (x2,y2)."""
        try:
            self._run_adb(
                ["shell", "input", "swipe",
                 str(x1), str(y1), str(x2), str(y2), str(duration_ms)],
                timeout=5
            )
        except Exception as e:
            self.logger.error(f"Lỗi swipe: {e}")

    def is_screen_on(self) -> bool:
        """Kiểm tra màn hình có sáng không."""
        try:
            result = self._run_adb(
                ["shell", "dumpsys", "power"],
                timeout=5
            )
            output = result.stdout.decode()
            return "mHoldingDisplaySuspendBlocker=true" in output
        except Exception:
            return True  # Giả sử emulator luôn sáng

    @property
    def resolution(self) -> Optional[Tuple[int, int]]:
        return self._resolution

    @property
    def device_serial(self) -> str:
        return self._device_serial

    @property
    def capture_backend(self) -> Optional[str]:
        return self._active_capture_method
