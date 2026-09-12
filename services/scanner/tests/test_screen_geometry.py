from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest

import cv2
import numpy as np

SCANNER = Path(__file__).resolve().parents[1]
ROOT = SCANNER.parents[1]
sys.path.insert(0, str(SCANNER / "src"))
from detector import HistoryDetector
from diagnostics import save_profile
from screen_geometry import FrameNormalizer, ReferenceLayout


class GeometryTests(unittest.TestCase):
    def test_native_status_and_navigation_bars_are_excluded_and_tap_is_reversible(self):
        normalizer = FrameNormalizer({"viewport": {"x": 0, "y": 96/2400, "width": 1, "height": 2250/2400}})
        frame = normalizer.normalize(np.zeros((2400, 1080, 3), np.uint8))
        self.assertEqual((1500, 720, 3), frame.shape)
        self.assertEqual((540, 1221), normalizer.to_native(360, 750))
        self.assertEqual((0, 96), normalizer.to_native(0, 0))

    def test_width_scaling_preserves_top_and_bottom_anchors(self):
        for native_w, native_h in [(720, 1280), (1080, 1920), (1080, 2400), (1440, 3200)]:
            with self.subTest(size=(native_w, native_h)):
                top = ReferenceLayout({"scale_mode": "width"})
                bottom = ReferenceLayout({"scale_mode": "width", "anchor_y": "bottom"})
                scale = native_w / 720
                self.assertEqual((round(450*scale), round(135*scale)), top.point((native_h, native_w), 450, 135))
                self.assertEqual((round(178*scale), round(native_h-200*scale)), bottom.point((native_h, native_w), 178, 1300))

    def test_rotation_invalidates_previous_tap_mapping(self):
        normalizer = FrameNormalizer()
        normalizer.normalize(np.zeros((2400, 1080, 3), np.uint8))
        with self.assertRaisesRegex(RuntimeError, "rotated"):
            normalizer.normalize(np.zeros((1080, 2400, 3), np.uint8))
        with self.assertRaises(RuntimeError):
            normalizer.to_native(20, 20)

    def test_invalid_viewports_and_outside_regions_are_rejected(self):
        with self.assertRaises(ValueError):
            FrameNormalizer({"viewport": {"x": .5, "y": 0, "width": 1, "height": 1}})
        with self.assertRaises(RuntimeError):
            ReferenceLayout({}).crop(np.zeros((1500, 720, 3), np.uint8), {"x": -1, "y": 0, "width": 20, "height": 20})

    def test_saved_calibration_keeps_resource_paths_when_moved(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source" / "config.json"
            output = Path(temp) / "profiles" / "phone.json"
            config = {"paths": {"templates": "assets/templates", "database": "../../data/test.db"}}
            before = {key: (source.parent / value).resolve() for key, value in config["paths"].items()}
            save_profile(config, source, output)
            saved = json.loads(output.read_text())
            for key, value in saved["paths"].items():
                self.assertEqual(before[key], (output.parent / value).resolve())


class MultiResolutionDetectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = json.loads((SCANNER / "config.phone.json").read_text())
        reference = cv2.imread(str(ROOT / "artifacts/capture_5555_latest.png"))
        cls.reference = cv2.resize(reference, (720, 1500), interpolation=cv2.INTER_LANCZOS4)
        cls.expected = ["CAI", "CA_CHUA", "CA_CHUA", "CA_ROT", "CAI", "NGO", "NGO", "CA_ROT"]
        cls.detector = HistoryDetector(SCANNER / "assets/templates", cls.config["history"], cls.config["detection"])

    def test_history_templates_work_across_resolution_and_aspect_ratio(self):
        # Simulate the same UI with history anchored to the bottom. These are
        # geometric regressions, not claims of live validation on these phones.
        for w, h in [(720, 1280), (1080, 1920), (1080, 2400), (1440, 3200), (720, 1500)]:
            with self.subTest(size=(w, h)):
                logical_h = round(h*720/w)
                logical = np.zeros((logical_h, 720, 3), np.uint8)
                logical[:1000] = self.reference[:1000]
                logical[-500:] = self.reference[-500:]
                native = cv2.resize(logical, (w, h), interpolation=cv2.INTER_CUBIC)
                normalized = FrameNormalizer().normalize(native)
                self.assertEqual(self.expected, self.detector.detect(normalized).sequence)

    def test_matching_also_scales_templates_for_nondefault_output_width(self):
        config = deepcopy(self.config["history"])
        config["scale_mode"] = "stretch"
        detector = HistoryDetector(SCANNER / "assets/templates", config, self.config["detection"])
        large = cv2.resize(self.reference, (1080, 2250), interpolation=cv2.INTER_CUBIC)
        self.assertEqual(self.expected, detector.detect(large).sequence)


if __name__ == "__main__":
    unittest.main()
