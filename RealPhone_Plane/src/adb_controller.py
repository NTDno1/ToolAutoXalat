"""
ADB Controller cho RealPhone Plane (Điện thoại thật USB).
Auto-detect device, auto-scale tọa độ, health check.
"""

import subprocess
import time
import random
import io
import numpy as np
from PIL import Image
from typing import Optional, Tuple, List

from src.logger import GameLogger


class RealPhoneAdbController:
    """
    Điều khiển ADB cho điện thoại thật qua USB Cable.
    Tự động phát hiện thiết bị, scale tọa độ theo resolution.
    """

    def __init__(self, config: dict, logger: GameLogger):
        self.adb_path = config.get("adb_path", "adb")
        self.device_id = config.get("device_id", "")
        self.auto_detect = config.get("auto_detect", True)
        self.auto_scale = config.get("auto_scale", True)
        self.base_resolution = (
            config.get("base_resolution", {}).get("w", 720),
            config.get("base_resolution", {}).get("h", 1280),
        )
        self.logger = logger

        # Anti-detection config
        self._anti_detection = config.get("anti_detection_enabled", True)
        self._min_delay = config.get("min_delay_ms", 50)
        self._max_delay = config.get("max_delay_ms", 200)
        self._random_offset = config.get("random_offset_px", 3)

        self._connected = False
        self._resolution: Optional[Tuple[int, int]] = None
        self._scale_x: float = 1.0
        self._scale_y: float = 1.0

    def _run_adb(self, args: list, timeout: int = 10) -> subprocess.CompletedProcess:
        """Chạy lệnh ADB cho thiết bị đã chọn."""
        cmd = [self.adb_path]
        if self.device_id:
            cmd += ["-s", self.device_id]
        cmd += args
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=timeout)
            return result
        except subprocess.TimeoutExpired:
            self.logger.error(f"ADB timeout: {' '.join(cmd)}")
            raise
        except FileNotFoundError:
            self.logger.critical(f"Không tìm thấy ADB tại: {self.adb_path}")
            raise

    def detect_devices(self) -> List[str]:
        """Liệt kê tất cả thiết bị USB đang kết nối."""
        try:
            result = subprocess.run(
                [self.adb_path, "devices"],
                capture_output=True, text=True, timeout=10
            )
            lines = result.stdout.strip().split("\n")
            devices = []
            for line in lines[1:]:  # Bỏ dòng header
                parts = line.strip().split("\t")
                if len(parts) >= 2 and parts[1] == "device":
                    # Lọc bỏ emulator (127.0.0.1, emulator-)
                    serial = parts[0]
                    if not serial.startswith("127.0.0.1") and \
                       not serial.startswith("emulator"):
                        devices.append(serial)
            return devices
        except Exception as e:
            self.logger.error(f"Lỗi detect devices: {e}")
            return []

    def connect(self, device_id: str = "") -> bool:
        """
        Kết nối với điện thoại thật.
        USB không cần 'adb connect', chỉ cần verify device.
        """
        if device_id:
            self.device_id = device_id

        if self.auto_detect and not self.device_id:
            devices = self.detect_devices()
            if not devices:
                self.logger.error(
                    "Không tìm thấy điện thoại USB! "
                    "Kiểm tra: cắm cáp + bật USB Debugging"
                )
                return False
            self.device_id = devices[0]
            if len(devices) > 1:
                self.logger.warning(
                    f"Phát hiện {len(devices)} thiết bị, "
                    f"dùng thiết bị đầu tiên: {self.device_id}"
                )

        self.logger.state("CONNECT", f"Kết nối USB: {self.device_id}...")

        # Verify thiết bị hoạt động
        if not self.is_connected():
            self.logger.error(f"Thiết bị {self.device_id} không phản hồi!")
            return False

        # Lấy resolution & tính scale
        self._resolution = self.get_resolution()
        if self._resolution and self.auto_scale:
            self._calculate_scale_factor()

        # Lấy thông tin thiết bị
        model = self._get_prop("ro.product.model")
        android_ver = self._get_prop("ro.build.version.release")
        battery = self.get_battery_level()

        self.logger.set_device_name(model or self.device_id)
        self.logger.device_info(
            model=model or "Unknown",
            android_ver=android_ver or "?",
            resolution=f"{self._resolution}" if self._resolution else "?",
            battery=battery
        )

        self._connected = True
        self.logger.state(
            "CONNECT",
            f"✅ Đã kết nối {model} | Scale: ({self._scale_x:.2f}, {self._scale_y:.2f})"
        )
        return True

    def disconnect(self):
        """Ngắt kết nối."""
        self._connected = False
        self.logger.state("DISCONNECT", f"Ngắt kết nối {self.device_id}")

    def is_connected(self) -> bool:
        """Kiểm tra thiết bị USB còn kết nối không."""
        try:
            result = subprocess.run(
                [self.adb_path, "devices"],
                capture_output=True, text=True, timeout=5
            )
            lines = result.stdout.strip().split("\n")
            for line in lines:
                if self.device_id in line and "device" in line.split("\t")[-1]:
                    self._connected = True
                    return True
            self._connected = False
            return False
        except Exception:
            self._connected = False
            return False

    def reconnect(self, max_retries: int = 5, wait_s: int = 3) -> bool:
        """Thử kết nối lại khi mất USB."""
        self.logger.state("RECONNECT", "Đang thử kết nối lại...")
        for attempt in range(1, max_retries + 1):
            self.logger.info(f"🔄 Lần thử {attempt}/{max_retries}...")
            time.sleep(wait_s)
            if self.is_connected():
                self.logger.state("RECONNECT", "✅ Kết nối lại thành công!")
                return True
        self.logger.error("❌ Không thể kết nối lại!")
        return False

    def get_resolution(self) -> Optional[Tuple[int, int]]:
        """Lấy resolution màn hình điện thoại."""
        try:
            result = self._run_adb(["shell", "wm", "size"])
            output = result.stdout.decode().strip()
            if "x" in output:
                parts = output.split(":")[-1].strip().split("x")
                w, h = int(parts[0]), int(parts[1])
                self._resolution = (w, h)
                return (w, h)
        except Exception as e:
            self.logger.error(f"Không đọc được resolution: {e}")
        return None

    def _calculate_scale_factor(self):
        """Tính tỷ lệ scale từ base_resolution → actual resolution."""
        if not self._resolution:
            return
        actual_w, actual_h = self._resolution
        base_w, base_h = self.base_resolution
        self._scale_x = actual_w / base_w
        self._scale_y = actual_h / base_h
        self.logger.debug(
            f"Scale factor: ({self._scale_x:.3f}, {self._scale_y:.3f}) | "
            f"Base: {base_w}x{base_h} → Actual: {actual_w}x{actual_h}"
        )

    def _scale_coord(self, x: int, y: int) -> Tuple[int, int]:
        """Scale tọa độ từ base → actual resolution."""
        if not self.auto_scale:
            return (x, y)
        scaled_x = int(x * self._scale_x)
        scaled_y = int(y * self._scale_y)
        return (scaled_x, scaled_y)

    def _add_random_offset(self, x: int, y: int) -> Tuple[int, int]:
        """Thêm offset ngẫu nhiên để chống detect."""
        if not self._anti_detection or self._random_offset <= 0:
            return (x, y)
        dx = random.randint(-self._random_offset, self._random_offset)
        dy = random.randint(-self._random_offset, self._random_offset)
        return (x + dx, y + dy)

    def _anti_detection_delay(self):
        """Delay ngẫu nhiên giữa các thao tác."""
        if not self._anti_detection:
            return
        delay = random.randint(self._min_delay, self._max_delay) / 1000.0
        time.sleep(delay)

    def _get_prop(self, prop: str) -> str:
        """Đọc system property từ thiết bị."""
        try:
            result = self._run_adb(["shell", "getprop", prop], timeout=5)
            return result.stdout.decode().strip()
        except Exception:
            return ""

    def get_battery_level(self) -> int:
        """Đọc mức pin thiết bị."""
        try:
            result = self._run_adb(
                ["shell", "dumpsys", "battery"],
                timeout=5
            )
            output = result.stdout.decode()
            for line in output.split("\n"):
                if "level:" in line:
                    return int(line.split(":")[-1].strip())
        except Exception:
            pass
        return -1

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
            return False

    def wake_screen(self):
        """Bật màn hình nếu đang tắt."""
        if not self.is_screen_on():
            self.logger.info("📱 Bật màn hình...")
            self._run_adb(["shell", "input", "keyevent", "KEYCODE_WAKEUP"])
            time.sleep(0.5)

    def keep_screen_on(self):
        """Giữ màn hình luôn sáng khi sạc."""
        try:
            self._run_adb(["shell", "svc", "power", "stayon", "true"])
            self.logger.info("📱 Đã bật Stay Awake")
        except Exception as e:
            self.logger.warning(f"Không bật được Stay Awake: {e}")

    def screencap(self) -> Optional[np.ndarray]:
        """
        Chụp màn hình điện thoại, trả về numpy array (BGR).
        Tự động scale về base resolution nếu auto_scale=True.
        """
        try:
            # Health check trước
            if not self._connected:
                return None

            result = self._run_adb(
                ["shell", "screencap", "-p"],
                timeout=5
            )
            if result.returncode != 0:
                self.logger.error("Screencap thất bại")
                return None

            img_data = result.stdout
            img_data = img_data.replace(b"\r\n", b"\n")

            img = Image.open(io.BytesIO(img_data))

            # Scale về base resolution nếu cần
            if self.auto_scale and self._resolution:
                base_w, base_h = self.base_resolution
                if img.size != (base_w, base_h):
                    img = img.resize((base_w, base_h), Image.LANCZOS)

            frame = np.array(img)
            if frame.shape[2] == 4:
                frame = frame[:, :, :3]
            frame = frame[:, :, ::-1]  # RGB → BGR

            return frame

        except Exception as e:
            self.logger.error(f"Lỗi screencap: {e}")
            return None

    def tap(self, x: int, y: int, delay_ms: int = 50):
        """
        Click vào tọa độ (base resolution).
        Tự động scale + random offset + anti-detection delay.
        """
        try:
            # Scale tọa độ sang actual resolution
            sx, sy = self._scale_coord(x, y)
            # Thêm random offset
            sx, sy = self._add_random_offset(sx, sy)

            self._run_adb(
                ["shell", "input", "tap", str(sx), str(sy)],
                timeout=5
            )

            # Anti-detection delay
            self._anti_detection_delay()

        except Exception as e:
            self.logger.error(f"Lỗi tap ({x}, {y}) → scaled ({sx}, {sy}): {e}")

    def multi_tap(self, taps: list, delay_between_ms: int = 80):
        """
        Thực hiện nhiều thao tác tap.
        taps: List of (x, y, count) hoặc (x, y)
        Tọa độ base resolution, tự động scale.
        """
        for tap_info in taps:
            if len(tap_info) == 3:
                x, y, count = tap_info
            else:
                x, y = tap_info
                count = 1

            for _ in range(count):
                self.tap(x, y, delay_ms=delay_between_ms)

    def health_check(self) -> dict:
        """
        Kiểm tra sức khỏe thiết bị.
        Trả về dict: {connected, battery, screen_on}
        """
        connected = self.is_connected()
        battery = self.get_battery_level() if connected else -1
        screen_on = self.is_screen_on() if connected else False

        self.logger.health_check(connected, battery, screen_on)

        return {
            "connected": connected,
            "battery": battery,
            "screen_on": screen_on,
        }

    @property
    def resolution(self) -> Optional[Tuple[int, int]]:
        return self._resolution

    @property
    def device_serial(self) -> str:
        return self.device_id
