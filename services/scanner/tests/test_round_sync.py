from datetime import timedelta
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import numpy as np


SCANNER_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = SCANNER_DIR / "src"
sys.path.insert(0, str(SCANNER_DIR))
sys.path.insert(0, str(SRC_DIR))

from database import utc_now  # noqa: E402
from scanner import GreedyScanner  # noqa: E402


class RoundSynchronizationTests(unittest.TestCase):
    def scanner(self) -> GreedyScanner:
        scanner = GreedyScanner.__new__(GreedyScanner)
        scanner.active_round = 284
        scanner.active_round_date = "2026-09-05"
        scanner.active_round_observed_at = utc_now()
        scanner.current_round = 276
        scanner.current_round_date = "2026-09-05"
        scanner.round_interval = 40
        scanner.round_detector = SimpleNamespace(maximum=2500)
        scanner.local_timezone = ZoneInfo("Asia/Bangkok")
        scanner.round_day_boundary_hour = 23
        scanner.round_future = None
        return scanner

    def test_fresh_app_round_wins_over_inferred_increment(self):
        scanner = self.scanner()
        self.assertEqual([284], scanner._rounds_for_shift(1, "2026-09-05"))

    def test_round_from_previous_cycle_falls_back_to_last_recorded_round(self):
        scanner = self.scanner()
        scanner.active_round_observed_at = utc_now() - timedelta(seconds=46)
        self.assertEqual([277], scanner._rounds_for_shift(1, "2026-09-05"))

    def test_multi_shift_ends_at_the_app_round(self):
        scanner = self.scanner()
        self.assertEqual([282, 283, 284], scanner._rounds_for_shift(3, "2026-09-05"))

    def test_recovery_rejects_previous_day_round_from_ocr_and_state(self):
        scanner = self.scanner()
        scanner.logger = MagicMock()
        scanner.active_round = 1854
        scanner.active_round_date = "2026-09-05"
        scanner.active_round_observed_at = utc_now()
        scanner.current_round = 1852
        scanner.current_round_date = "2026-09-05"
        scanner._maximum_plausible_round = MagicMock(return_value=111)

        self.assertEqual(
            [None, None],
            scanner._rounds_for_shift(2, "2026-09-05", observed_round=1854),
        )
        scanner.logger.warning.assert_called()

    def test_recovery_keeps_valid_round_anchor(self):
        scanner = self.scanner()
        scanner.logger = MagicMock()
        scanner._maximum_plausible_round = MagicMock(return_value=111)

        self.assertEqual(
            [81, 82],
            scanner._rounds_for_shift(2, "2026-09-05", observed_round=82),
        )

    def test_popup_uses_the_round_shown_by_the_app(self):
        scanner = self.scanner()
        scanner.current_round = 284
        frame = np.zeros((10, 10, 3), dtype=np.uint8)
        self.assertEqual(284, scanner._round_for_popup_result(frame, "2026-09-05"))

    def test_popup_uses_app_round_when_ocr_advanced_during_confirmation(self):
        scanner = self.scanner()
        scanner.current_round = 283
        frame = np.zeros((10, 10, 3), dtype=np.uint8)
        self.assertEqual(284, scanner._round_for_popup_result(frame, "2026-09-05"))

    def test_stable_large_forward_jump_reanchors_after_three_reads(self):
        scanner = self.scanner()
        scanner.active_round = 5
        scanner.current_round = 109
        scanner.last_result_at = utc_now()
        scanner.round_reanchor_required = 3
        scanner.round_reanchor_candidate = None
        scanner.round_reanchor_candidate_date = None
        scanner.round_reanchor_confirmations = 0
        scanner.popup_candidate_code = None
        scanner.pending_popup_result_code = None
        scanner.database = MagicMock()
        scanner.logger = MagicMock()
        scanner.frame_source = MagicMock(serial="test-device")
        scanner._local_date = lambda: "2026-09-05"
        observed_at = utc_now()

        self.assertFalse(scanner._publish_active_round(157, observed_at))
        self.assertFalse(scanner._publish_active_round(157, observed_at))
        self.assertTrue(scanner._publish_active_round(157, observed_at))
        self.assertEqual(157, scanner.current_round)
        scanner.database.set_states.assert_called_once()

    def test_backward_ocr_cannot_replace_current_round(self):
        scanner = self.scanner()
        scanner.current_round = 50
        scanner.active_round = 50
        scanner.popup_candidate_code = None
        scanner.pending_popup_result_code = None
        scanner.database = MagicMock()
        scanner.logger = MagicMock()
        scanner._local_date = lambda: "2026-09-05"

        self.assertFalse(scanner._publish_active_round(5, utc_now()))
        self.assertEqual(50, scanner.current_round)
        scanner.database.set_states.assert_not_called()

    def test_stable_real_ocr_repairs_impossible_5000_reference(self):
        scanner = self.scanner()
        scanner.current_round = 5667
        scanner.active_round = 5667
        scanner.last_result_at = utc_now()
        scanner.round_reanchor_required = 3
        scanner.round_reanchor_candidate = None
        scanner.round_reanchor_candidate_date = None
        scanner.round_reanchor_confirmations = 0
        scanner.popup_candidate_code = None
        scanner.pending_popup_result_code = None
        scanner.database = MagicMock()
        scanner.logger = MagicMock()
        scanner.frame_source = MagicMock(serial="test-device")
        scanner._local_date = lambda: "2026-09-05"
        observed_at = utc_now()

        self.assertFalse(scanner._publish_active_round(669, observed_at))
        self.assertFalse(scanner._publish_active_round(669, observed_at))
        self.assertTrue(scanner._publish_active_round(669, observed_at))
        self.assertEqual(669, scanner.current_round)
        scanner.database.set_states.assert_called_once()


if __name__ == "__main__":
    unittest.main()
