"""
Device Manager cho RealPhone Plane.
Quản lý thiết bị thật: detect, health check, battery, BIGO app.
CHỈ CÓ Ở REALPHONE PLANE.
"""

import subprocess
import time
from typing import Optional, List, Dict

from src.logger import GameLogger


class DeviceManager:
    """
    Quản lý thiết bị Android thật qua USB.
    - Tự động phát hiện thiết bị
    - Kiểm tra USB Debugging
    - Monitor battery & screen
    - Kiểm tra app BIGO
    """

    BIGO_PACKAGE = "sg.bigo.live"

    def __init__(self, adb_path: str = "adb", logger: GameLogger = None):
        self.adb_path = adb_path
        self.logger = logger
        self._selected_device: Optional[str] = None

    def _run_adb(self, args: list, device: str = "",
                 timeout: int = 10) -> subprocess.CompletedProcess:
        """Chạy lệnh ADB."""
        cmd = [self.adb_path]
        if device:
            cmd += ["-s", device]
        elif self._selected_device:
            cmd += ["-s", self._selected_device]
        cmd += args
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)

    def list_devices(self) -> List[Dict[str, str]]:
        """
        Liệt kê tất cả thiết bị USB đang kết nối.
        Trả về list of {"serial": ..., "status": ..., "type": ...}
        """
        devices = []
        try:
            result = subprocess.run(
                [self.adb_path, "devices", "-l"],
                capture_output=True, text=True, timeout=10
            )
            lines = result.stdout.strip().split("\n")

            for line in lines[1:]:
                parts = line.strip().split()
                if len(parts) >= 2:
                    serial = parts[0]
                    status = parts[1]

                    # Phân loại thiết bị
                    if serial.startswith("127.0.0.1") or serial.startswith("emulator"):
                        dev_type = "emulator"
                    else:
                        dev_type = "real_phone"

                    # Lấy model name nếu có
                    model = ""
                    for p in parts[2:]:
                        if p.startswith("model:"):
                            model = p.split(":")[1]
                            break

                    devices.append({
                        "serial": serial,
                        "status": status,
                        "type": dev_type,
                        "model": model,
                    })

        except Exception as e:
            if self.logger:
                self.logger.error(f"Lỗi liệt kê devices: {e}")

        return devices

    def list_real_phones(self) -> List[Dict[str, str]]:
        """Chỉ lấy danh sách điện thoại thật (bỏ emulator)."""
        return [d for d in self.list_devices() if d["type"] == "real_phone"]

    def select_device(self, device_id: str) -> bool:
        """Chọn thiết bị để thao tác."""
        devices = self.list_devices()
        for d in devices:
            if d["serial"] == device_id and d["status"] == "device":
                self._selected_device = device_id
                if self.logger:
                    self.logger.state("DEVICE",
                                      f"Đã chọn: {device_id} ({d.get('model', 'Unknown')})")
                return True

        if self.logger:
            self.logger.error(f"Thiết bị {device_id} không tìm thấy hoặc chưa sẵn sàng!")
        return False

    def auto_select_phone(self) -> Optional[str]:
        """Tự động chọn điện thoại thật đầu tiên tìm được."""
        phones = self.list_real_phones()
        if not phones:
            if self.logger:
                self.logger.error(
                    "❌ Không tìm thấy điện thoại thật!\n"
                    "   → Kiểm tra: cắm cáp USB + bật USB Debugging"
                )
            return None

        phone = phones[0]
        self._selected_device = phone["serial"]

        if self.logger:
            self.logger.state(
                "DEVICE",
                f"Tự động chọn: {phone['serial']} ({phone.get('model', 'Unknown')})"
            )
            if len(phones) > 1:
                self.logger.warning(
                    f"⚠️ Phát hiện {len(phones)} điện thoại. "
                    f"Dùng: {phone['serial']}"
                )

        return phone["serial"]

    def get_device_info(self) -> Dict[str, str]:
        """Lấy thông tin chi tiết thiết bị."""
        info = {
            "serial": self._selected_device or "",
            "model": self._get_prop("ro.product.model"),
            "manufacturer": self._get_prop("ro.product.manufacturer"),
            "android_version": self._get_prop("ro.build.version.release"),
            "sdk_version": self._get_prop("ro.build.version.sdk"),
            "resolution": "",
            "battery": str(self.get_battery_level()),
        }

        # Resolution
        try:
            result = self._run_adb(["shell", "wm", "size"])
            output = result.stdout.strip()
            if ":" in output:
                info["resolution"] = output.split(":")[-1].strip()
        except Exception:
            pass

        return info

    def _get_prop(self, prop: str) -> str:
        """Đọc system property."""
        try:
            result = self._run_adb(["shell", "getprop", prop], timeout=5)
            return result.stdout.strip()
        except Exception:
            return ""

    def get_battery_level(self) -> int:
        """Đọc mức pin."""
        try:
            result = self._run_adb(["shell", "dumpsys", "battery"], timeout=5)
            for line in result.stdout.split("\n"):
                if "level:" in line:
                    return int(line.split(":")[-1].strip())
        except Exception:
            pass
        return -1

    def is_charging(self) -> bool:
        """Kiểm tra đang sạc không."""
        try:
            result = self._run_adb(["shell", "dumpsys", "battery"], timeout=5)
            for line in result.stdout.split("\n"):
                if "status:" in line:
                    status = int(line.split(":")[-1].strip())
                    # 2 = Charging, 5 = Full
                    return status in (2, 5)
        except Exception:
            pass
        return False

    def is_screen_on(self) -> bool:
        """Kiểm tra màn hình sáng."""
        try:
            result = self._run_adb(
                ["shell", "dumpsys", "power"], timeout=5
            )
            return "mHoldingDisplaySuspendBlocker=true" in result.stdout
        except Exception:
            return False

    def wake_screen(self):
        """Bật màn hình."""
        if not self.is_screen_on():
            self._run_adb(["shell", "input", "keyevent", "KEYCODE_WAKEUP"])
            time.sleep(0.5)

    def keep_screen_on(self):
        """Giữ màn hình sáng khi sạc."""
        try:
            self._run_adb(["shell", "svc", "power", "stayon", "true"])
            if self.logger:
                self.logger.info("📱 Đã bật Stay Awake")
        except Exception:
            pass

    def check_bigo_running(self) -> bool:
        """Kiểm tra app BIGO có đang chạy không."""
        try:
            result = self._run_adb(
                ["shell", "pidof", self.BIGO_PACKAGE], timeout=5
            )
            pid = result.stdout.strip()
            return len(pid) > 0
        except Exception:
            return False

    def launch_bigo(self):
        """Mở app BIGO."""
        try:
            self._run_adb([
                "shell", "monkey", "-p", self.BIGO_PACKAGE,
                "-c", "android.intent.category.LAUNCHER", "1"
            ], timeout=10)
            if self.logger:
                self.logger.info("📱 Đang mở app BIGO...")
            time.sleep(3)
        except Exception as e:
            if self.logger:
                self.logger.error(f"Không mở được BIGO: {e}")

    def check_usb_debugging(self) -> bool:
        """Verify USB debugging hoạt động bằng cách chạy lệnh đơn giản."""
        try:
            result = self._run_adb(["shell", "echo", "ok"], timeout=5)
            return "ok" in result.stdout
        except Exception:
            return False

    def pre_flight_check(self) -> Dict[str, bool]:
        """
        Kiểm tra toàn diện trước khi chạy.
        Trả về dict các kết quả check.
        """
        checks = {}

        if self.logger:
            self.logger.separator("═", 55)
            self.logger.info("🔍 PRE-FLIGHT CHECK")
            self.logger.separator("─", 55)

        # 1. USB Debugging
        usb_ok = self.check_usb_debugging()
        checks["usb_debugging"] = usb_ok
        if self.logger:
            icon = "✅" if usb_ok else "❌"
            self.logger.info(f"  {icon} USB Debugging: {'OK' if usb_ok else 'FAIL'}")

        if not usb_ok:
            return checks

        # 2. Device info
        info = self.get_device_info()
        checks["device_detected"] = bool(info.get("model"))
        if self.logger:
            self.logger.info(
                f"  📱 Model: {info.get('model', 'Unknown')} | "
                f"Android {info.get('android_version', '?')}"
            )
            self.logger.info(f"  📐 Resolution: {info.get('resolution', '?')}")

        # 3. Battery
        battery = self.get_battery_level()
        checks["battery_ok"] = battery > 20
        charging = self.is_charging()
        if self.logger:
            icon = "✅" if battery > 20 else "⚠️"
            charge_str = " (đang sạc)" if charging else ""
            self.logger.info(f"  {icon} 🔋 Pin: {battery}%{charge_str}")

        # 4. Screen
        screen_on = self.is_screen_on()
        checks["screen_on"] = screen_on
        if self.logger:
            icon = "✅" if screen_on else "⚠️"
            self.logger.info(f"  {icon} Màn hình: {'Sáng' if screen_on else 'Tắt'}")
            if not screen_on:
                self.logger.info("      → Đang bật màn hình...")
                self.wake_screen()

        # 5. BIGO app
        bigo_running = self.check_bigo_running()
        checks["bigo_running"] = bigo_running
        if self.logger:
            icon = "✅" if bigo_running else "⚠️"
            self.logger.info(
                f"  {icon} App BIGO: {'Đang chạy' if bigo_running else 'Chưa mở'}"
            )

        if self.logger:
            self.logger.separator("═", 55)
            all_ok = all(checks.values())
            self.logger.info(
                f"  {'🎉 SẴN SÀNG!' if all_ok else '⚠️ Cần kiểm tra lại'}"
            )

        return checks

    @property
    def selected_device(self) -> Optional[str]:
        return self._selected_device
