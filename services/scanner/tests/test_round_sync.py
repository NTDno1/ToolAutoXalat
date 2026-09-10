from datetime import timedelta
from pathlib import Path
import sys
import unittest

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


if __name__ == "__main__":
    unittest.main()
