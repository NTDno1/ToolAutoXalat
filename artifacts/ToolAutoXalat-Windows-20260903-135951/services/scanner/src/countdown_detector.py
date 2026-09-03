from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

import cv2
import numpy as np
import pytesseract


class CountdownDetectionError(RuntimeError):
    pass


@dataclass(frozen=True)
class CountdownDetection:
    seconds: int
    raw_text: str
    crop: np.ndarray


def parse_countdown_text(raw_text: str, minimum: int = 0, maximum: int = 30) -> int:
    """Parse the game's small `Ns` label, including common OCR mistakes for `s`."""
    compact = re.sub(r"\s+", "", raw_text).lower()
    digit_groups = re.findall(r"\d+", compact)
    if digit_groups:
        candidate_text = max(digit_groups, key=len)
        candidate = int(candidate_text)
        # The white suffix `s` is frequently read as a final digit 5, e.g.
        # `23s` -> `235` and `13s` -> `135`.
        while candidate > maximum and candidate_text.endswith("5"):
            candidate_text = candidate_text[:-1]
            if not candidate_text:
                break
            candidate = int(candidate_text)
        if minimum <= candidate <= maximum:
            return candidate

    # At this font size Tesseract occasionally reads the complete `8s` as
    # `Ss`; this mapping is safe because the crop contains no other text.
    if compact in {"s", "ss"} and minimum <= 8 <= maximum:
        return 8
    raise CountdownDetectionError(f"Countdown not found; OCR={raw_text!r}")


class CountdownDetector:
    """Read the 0..30 second label from the center of a normalized game frame."""

    def __init__(self, config: dict):
        self.reference_width = int(config["reference_width"])
        self.reference_height = int(config["reference_height"])
        self.crop_config = dict(config["crop"])
        self.minimum = int(config.get("minimum", 0))
        self.maximum = int(config.get("maximum", 30))
        self.white_value_min = int(config.get("white_value_min", 175))
        self.white_saturation_max = int(config.get("white_saturation_max", 100))
        tesseract_path = Path(config.get("tesseract_path", ""))
        if tesseract_path.is_file():
            pytesseract.pytesseract.tesseract_cmd = str(tesseract_path)

    def crop(self, frame: np.ndarray) -> np.ndarray:
        if frame is None or frame.ndim != 3:
            raise CountdownDetectionError("Countdown frame is empty or invalid")
        height, width = frame.shape[:2]
        scale_x = width / self.reference_width
        scale_y = height / self.reference_height
        x = round(int(self.crop_config["x"]) * scale_x)
        y = round(int(self.crop_config["y"]) * scale_y)
        crop_width = round(int(self.crop_config["width"]) * scale_x)
        crop_height = round(int(self.crop_config["height"]) * scale_y)
        output = frame[y : y + crop_height, x : x + crop_width].copy()
        if output.size == 0:
            raise CountdownDetectionError("Countdown crop is outside the frame")
        return output

    def detect(self, frame: np.ndarray) -> CountdownDetection:
        crop = self.crop(frame)
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        white_text = cv2.inRange(
            hsv,
            np.array([0, 0, self.white_value_min], dtype=np.uint8),
            np.array([180, self.white_saturation_max, 255], dtype=np.uint8),
        )
        enlarged = cv2.resize(
            white_text, None, fx=5, fy=5, interpolation=cv2.INTER_NEAREST
        )
        try:
            raw = pytesseract.image_to_string(
                enlarged,
                config="--psm 7 -c tessedit_char_whitelist=0123456789sS",
                timeout=3,
            ).strip()
        except (RuntimeError, pytesseract.TesseractError) as exc:
            raise CountdownDetectionError(f"Tesseract failed: {exc}") from exc
        return CountdownDetection(
            seconds=parse_countdown_text(raw, self.minimum, self.maximum),
            raw_text=raw,
            crop=crop,
        )
