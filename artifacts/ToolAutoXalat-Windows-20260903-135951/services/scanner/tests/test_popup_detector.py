from pathlib import Path
import sys
import unittest

import numpy as np


SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from popup_detector import is_result_popup  # noqa: E402


CONFIG = {
    "reference_width": 720,
    "reference_height": 1500,
    "max_dim_brightness": 135,
    "bright_pixel_min": 200,
    "min_dialog_bright_fraction": 0.35,
    "dim_region": {"x": 45, "y": 120, "width": 630, "height": 110},
    "dialog_region": {"x": 0, "y": 850, "width": 720, "height": 650},
}


class PopupDetectorTests(unittest.TestCase):
    def test_normal_bright_game_is_not_popup(self):
        frame = np.full((1500, 720, 3), 210, dtype=np.uint8)
        self.assertFalse(is_result_popup(frame, CONFIG))

    def test_dimmed_game_with_bright_bottom_dialog_is_popup(self):
        frame = np.full((1500, 720, 3), 70, dtype=np.uint8)
        frame[850:1500, :, :] = 225
        self.assertTrue(is_result_popup(frame, CONFIG))

    def test_dimmed_transition_without_dialog_is_not_popup(self):
        frame = np.full((1500, 720, 3), 70, dtype=np.uint8)
        self.assertFalse(is_result_popup(frame, CONFIG))


if __name__ == "__main__":
    unittest.main()
