from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import cv2
import numpy as np

SCANNER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCANNER))
from scanner import GreedyScanner, create_frame_source, load_config
from diagnostics import check_capture
from screen_geometry import FrameNormalizer


class ScannerSourceIntegrationTests(unittest.TestCase):
    def config(self, temp):
        config = json.loads((SCANNER / "config.phone.json").read_text())
        config["paths"] = {"database": str(temp / "test.db"),
                           "templates": str(SCANNER / "assets/templates"),
                           "captures": str(temp / "captures"), "logs": str(temp / "logs")}
        return config

    def test_auto_device_is_resolved_before_restoring_its_round_state(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            config = self.config(temp)
            path = temp / "config.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            source = MagicMock(serial="", output_size=(720, 1600))
            source.connect.side_effect = lambda: setattr(source, "serial", "phone")
            with patch("scanner.create_frame_source", return_value=source):
                scanner = GreedyScanner(path)
            try:
                scanner.database.set_state("current_round:phone", "472")
                scanner.database.set_state("current_round:127.0.0.1:5555", "999")
                with patch.object(scanner, "scan_once", return_value={"status": "waiting"}) as scan:
                    scanner.run(once=True)
                scan.assert_called_once()
                self.assertEqual(472, scanner.current_round)
                self.assertEqual("phone", scanner.popup_input_serial)
                self.assertEqual("phone", scanner.database.get_state("scanner_source_serial"))
            finally:
                scanner.close()

    def test_popup_tap_targets_captured_phone_in_native_coordinates(self):
        scanner = GreedyScanner.__new__(GreedyScanner)
        scanner.popup_dismiss_enabled = True
        scanner.popup_dismiss_config = {"reference_width": 720, "reference_height": 1500,
                                        "scale_mode": "width", "anchor_y": "bottom"}
        scanner.popup_input_serial = "phone"
        scanner.popup_tap_x, scanner.popup_tap_y = 360, 750
        scanner.last_popup_dismiss_monotonic = 0
        scanner.popup_cooldown = 0
        scanner.logger = MagicMock()
        normalizer = FrameNormalizer()
        frame = normalizer.normalize(np.zeros((2400, 1080, 3), np.uint8))
        scanner.frame_source = MagicMock(adb_path="adb")
        scanner.frame_source.map_input_point.side_effect = normalizer.to_native
        with patch("scanner.subprocess.run", return_value=MagicMock(returncode=0)) as run:
            self.assertTrue(scanner._dismiss_result_popup(frame, assume_visible=True))
        self.assertEqual(["adb", "-s", "phone", "shell", "input", "tap", "540", "1275"], run.call_args.args[0])

    def test_check_uses_no_database_or_input_and_reads_fixture(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            config = self.config(temp)
            frame = cv2.imread(str(SCANNER.parents[1] / "artifacts/capture_5555_latest.png"))
            frame = cv2.resize(frame, (720, 1500), interpolation=cv2.INTER_LANCZOS4)
            with patch("diagnostics.capture_frame", return_value=(frame, frame, "phone")), patch("scanner.ScannerDatabase") as database, patch("scanner.subprocess.run") as adb, redirect_stdout(StringIO()):
                exit_code = check_capture(config, temp / "config.json", temp / "check", None)
            self.assertEqual(0, exit_code)
            database.assert_not_called()
            adb.assert_not_called()
            self.assertFalse((temp / "test.db").exists())
            report = json.loads((temp / "check/report.json").read_text())
            self.assertEqual(8, len(report["history"]))
            self.assertEqual(472, report["round"]["value"])
            self.assertEqual(15, report["countdown"]["value"])

    def test_phone_profile_rejects_a_separate_popup_input_serial(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            config = self.config(temp)
            config["popup_dismiss"]["input_serial"] = "127.0.0.1:5555"
            path = temp / "config.json"
            path.write_text(json.dumps(config))
            with patch("adb_capture.resolve_adb_path", return_value="adb"):
                with self.assertRaisesRegex(ValueError, "remove popup_dismiss.input_serial"):
                    GreedyScanner(path)
            self.assertFalse((temp / "test.db").exists())

    def test_legacy_config_selects_bluestacks_capture(self):
        config = json.loads((SCANNER / "config.json").read_text())
        with patch("scanner.BlueStacksFrameSource") as source:
            create_frame_source(config)
        source.assert_called_once_with(config["emulator"])

    def test_phone_config_selects_scrcpy_video_capture(self):
        config = json.loads((SCANNER / "config.phone.json").read_text())
        with patch("scrcpy_capture.ScrcpyFrameSource") as source:
            create_frame_source(config)
        source.assert_called_once_with(config["source"], config["screen"])

    def test_pinning_wifi_serial_keeps_automatic_network_reconnect(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            config = self.config(temp)
            config["source"]["connect_address"] = "10.0.0.9:5555"
            path = temp / "config.json"
            path.write_text(json.dumps(config))
            self.assertEqual("10.0.0.9:5555", load_config(path, "10.0.0.9:5555")["source"]["connect_address"])
            self.assertEqual("", load_config(path, "usb-phone")["source"]["connect_address"])


if __name__ == "__main__":
    unittest.main()
