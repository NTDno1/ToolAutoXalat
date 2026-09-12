from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import MagicMock, patch

import numpy as np

SCANNER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCANNER))
sys.path.insert(0, str(SCANNER / "src"))

from scanner import GreedyScanner


class ConnectionMonitoringTests(unittest.TestCase):
    def scanner(self):
        scanner = GreedyScanner.__new__(GreedyScanner)
        scanner.connection_failures = 0
        scanner.connection_alert_sent = False
        scanner.reconnect_alert_after = 5
        scanner.failure_cooldown = 30
        scanner.frame_source = SimpleNamespace(serial="phone", reconnect_interval=5)
        scanner._save_runtime_state = MagicMock()
        scanner._notify_failure = MagicMock()
        scanner.logger = MagicMock()
        scanner.database = MagicMock()
        return scanner

    def test_connection_webhook_event_is_raised_once_after_five_failures(self):
        scanner = self.scanner()
        for attempt in range(1, 7):
            scanner._record_connection_failure(RuntimeError("device offline"))
            self.assertEqual(attempt, scanner.connection_failures)

        scanner._notify_failure.assert_called_once()
        args = scanner._notify_failure.call_args.args
        self.assertEqual("PHONE_CONNECTION_FAILED", args[0])
        self.assertEqual(5, args[2]["attempts"])
        self.assertEqual(5, args[2]["reconnectIntervalSeconds"])
        self.assertEqual(6, scanner._save_runtime_state.call_count)

    def test_successful_frame_resets_failures_and_records_recovery(self):
        scanner = self.scanner()
        scanner.connection_failures = 5
        scanner.connection_alert_sent = True

        scanner._record_connection_restored()

        self.assertEqual(0, scanner.connection_failures)
        self.assertFalse(scanner.connection_alert_sent)
        event = scanner.database.insert_event.call_args.args
        self.assertEqual("INFO", event[0])
        self.assertEqual("PHONE_RECONNECTED", event[1])
        self.assertEqual(5, event[3]["failedAttempts"])

    def test_scan_marks_connection_restored_as_soon_as_a_frame_arrives(self):
        scanner = self.scanner()
        frame = np.zeros((1600, 720, 3), dtype=np.uint8)
        scanner.frame_source = MagicMock()
        scanner.frame_source.capture.return_value = frame
        scanner._record_connection_restored = MagicMock()
        scanner._collect_countdown_detection = MagicMock()
        scanner._collect_round_detection = MagicMock()
        scanner._schedule_countdown_detection = MagicMock()
        scanner._schedule_round_detection = MagicMock()
        scanner.popup_dismiss_config = {}
        scanner.capture_failures = 3
        scanner.no_result_attempts = 2
        scanner._handle_result_popup = MagicMock(return_value={"status": "popup"})

        with patch("scanner.is_result_popup", return_value=True):
            outcome = scanner.scan_once()

        self.assertEqual("popup", outcome["status"])
        scanner._record_connection_restored.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
