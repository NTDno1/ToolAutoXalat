from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from adb_capture import (AdbDevice, AdbFrameSource, CaptureBlockedError, list_devices,
                         secure_focused_window, select_device)


def result(stdout=b"", returncode=0, stderr=b""):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


SECURE_DUMP = """
  mCurrentFocus=Window{123 u0 sg.bigo.live/.WebProcessActivity}
  Window #1 Window{123 u0 sg.bigo.live/.WebProcessActivity}:
    mAttrs={(0,0)(fillxfill)
      fl=LAYOUT_IN_SCREEN SECURE HARDWARE_ACCELERATED
      pfl=FORCE_DRAW_STATUS_BAR_BACKGROUND}
    mHasSurface=true
  Window #2 Window{456 u0 other/.MainActivity}:
      fl=LAYOUT_IN_SCREEN HARDWARE_ACCELERATED
"""


class DeviceSelectionTests(unittest.TestCase):
    def test_phone_selection_excludes_bluestacks_and_offline_devices(self):
        devices = [AdbDevice("127.0.0.1:5555", "device", kind="emulator"),
                   AdbDevice("offline", "offline"), AdbDevice("phone", "device")]
        self.assertEqual("phone", select_device(devices).serial)

    def test_multiple_phones_require_explicit_serial(self):
        devices = [AdbDevice("a", "device"), AdbDevice("b", "device")]
        with self.assertRaisesRegex(RuntimeError, "Multiple"):
            select_device(devices)
        self.assertEqual("b", select_device(devices, "b").serial)

    def test_unauthorized_phone_is_not_selected(self):
        devices = [AdbDevice("phone", "unauthorized")]
        with self.assertRaisesRegex(RuntimeError, "unauthorized"):
            select_device(devices, "phone")

    def test_explicit_phone_profile_does_not_accept_emulator(self):
        with self.assertRaisesRegex(RuntimeError, "emulator"):
            select_device([AdbDevice("emulator-5554", "device", kind="emulator")], "emulator-5554")

    def test_wireless_phone_and_remote_emulator_are_distinguished(self):
        listing = b"List of devices attached\n10.0.0.2:5555 device model:Pixel\n10.0.0.3:5555 device model:Emu\n"
        with patch("adb_capture.run_adb", side_effect=[result(listing), result(b"0\n"), result(b"1\n")]):
            devices = list_devices("adb")
        self.assertEqual(["phone", "emulator"], [d.kind for d in devices])


class CaptureTests(unittest.TestCase):
    def source(self):
        with patch("adb_capture.resolve_adb_path", return_value="adb"):
            return AdbFrameSource({"serial": "phone", "reconnect_interval_seconds": 1})

    def test_png_is_decoded_without_text_newline_conversion(self):
        source = self.source()
        native = np.random.default_rng(1).integers(0, 255, (2400, 1080, 3), dtype=np.uint8)
        png = cv2.imencode(".png", native)[1].tobytes()
        with patch("adb_capture.list_devices", return_value=[AdbDevice("phone", "device")]), patch("adb_capture.run_adb", return_value=result(png)) as run:
            frame = source.capture()
        self.assertEqual((1600, 720, 3), frame.shape)
        self.assertEqual((540, 1200), source.map_input_point(360, 800))
        self.assertEqual(["-s", "phone", "exec-out", "screencap", "-p"], run.call_args.args[1])
        source.close()
        with self.assertRaises(RuntimeError):
            source.map_input_point(360, 800)

    def test_reconnect_remains_pinned_to_original_phone(self):
        source = self.source()
        with patch("adb_capture.list_devices", return_value=[AdbDevice("phone", "device")]):
            source.connect()
        source.connected = False
        source.last_connect_attempt = -100
        with patch("adb_capture.list_devices", return_value=[AdbDevice("different", "device")]):
            with self.assertRaisesRegex(RuntimeError, "phone is not connected"):
                source.capture()
        self.assertEqual("phone", source.serial)

    def test_empty_screenshot_reports_foreground_capture_protection(self):
        source = self.source()
        source.connected = True
        with patch("adb_capture.run_adb", side_effect=[result(), result(SECURE_DUMP.encode())]):
            with self.assertRaisesRegex(CaptureBlockedError, "FLAG_SECURE"):
                source.capture()
        with self.assertRaises(RuntimeError):
            source.map_input_point(10, 10)

    def test_inactive_secure_window_does_not_block_current_app(self):
        dump = SECURE_DUMP.replace("mCurrentFocus=Window{123", "mCurrentFocus=Window{456")
        self.assertIsNone(secure_focused_window(dump))

    def test_timeout_invalidates_input_mapping(self):
        source = self.source()
        source.connected = True
        with patch("adb_capture.run_adb", side_effect=[RuntimeError("timeout"), result()]):
            with self.assertRaisesRegex(RuntimeError, "timeout"):
                source.capture()
        self.assertFalse(source.connected)
        with self.assertRaises(RuntimeError):
            source.map_input_point(10, 10)

    def test_connect_address_does_not_select_another_ready_phone(self):
        with patch("adb_capture.resolve_adb_path", return_value="adb"):
            source = AdbFrameSource({"connect_address": "10.0.0.9:5555"})
        with patch("adb_capture.run_adb", return_value=result()), patch("adb_capture.list_devices", return_value=[AdbDevice("usb-phone", "device")]):
            with self.assertRaisesRegex(RuntimeError, "10.0.0.9:5555 is not connected"):
                source.connect()


if __name__ == "__main__":
    unittest.main()
