from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

import cv2
import numpy as np


SCANNER_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = SCANNER_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from betting_signal_detector import BettingSignalDetector  # noqa: E402


class BettingSignalDetectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        config = json.loads(
            (SCANNER_DIR / "config.redmi-k30.json").read_text(encoding="utf-8")
        )
        cls.detector = BettingSignalDetector(config["betting_signals"])

    def make_frame(self, scale: float = 1.0) -> np.ndarray:
        return np.zeros((round(1600 * scale), round(720 * scale), 3), dtype=np.uint8)

    def draw_coin(self, frame: np.ndarray, x: int, y: int, scale: float = 1.0) -> None:
        cv2.circle(
            frame,
            (round(x * scale), round(y * scale)),
            round(11 * scale),
            (0, 220, 255),
            -1,
        )

    def draw_hot(self, frame: np.ndarray, x: int, y: int, scale: float = 1.0) -> None:
        cv2.rectangle(
            frame,
            (round(x * scale), round(y * scale)),
            (round((x + 55) * scale), round((y + 36) * scale)),
            (20, 20, 235),
            -1,
        )

    def test_detects_hot_badge_and_each_coin_level(self) -> None:
        frame = self.make_frame()
        self.draw_coin(frame, 360, 423)
        self.draw_coin(frame, 515, 500)
        self.draw_coin(frame, 545, 500)
        self.draw_coin(frame, 570, 500)
        self.draw_hot(frame, 550, 360)

        result = self.detector.detect(frame)
        items = {item.item_code: item for item in result.items}

        self.assertEqual("XIEN", result.hot_item_code)
        self.assertEqual(1, items["BANH_MI"].coin_count)
        self.assertEqual(3, items["XIEN"].coin_count)
        self.assertEqual(0, items["CAI"].coin_count)

    def test_reference_layout_scales_to_a_larger_phone(self) -> None:
        scale = 1.5
        frame = self.make_frame(scale)
        self.draw_coin(frame, 115, 671, scale)
        self.draw_hot(frame, 130, 530, scale)

        result = self.detector.detect(frame)
        items = {item.item_code: item for item in result.items}

        self.assertEqual("CAI", result.hot_item_code)
        self.assertEqual(1, items["CAI"].coin_count)

    def test_wheel_border_sized_red_patch_is_not_hot(self) -> None:
        frame = self.make_frame()
        cv2.rectangle(frame, (370, 280), (395, 300), (0, 0, 235), -1)

        result = self.detector.detect(frame)

        self.assertIsNone(result.hot_item_code)


if __name__ == "__main__":
    unittest.main()
