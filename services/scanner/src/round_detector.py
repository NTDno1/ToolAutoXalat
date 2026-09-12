from __future__ import annotations

from dataclasses import dataclass
import re
from pathlib import Path

import cv2
import numpy as np
import pytesseract

from screen_geometry import ReferenceLayout


class RoundDetectionError(RuntimeError):
    pass


@dataclass(frozen=True)
class RoundDetection:
    number: int
    raw_text: str
    crop: np.ndarray


class RoundDetector:
    """Read the `Today's N Round` label from a normalized Android frame."""

    def __init__(self, config: dict):
        self.layout = ReferenceLayout(config)
        self.reference_width = int(config["reference_width"])
        self.reference_height = int(config["reference_height"])
        self.crop_config = dict(config["crop"])
        self.minimum = int(config.get("minimum", 1))
        self.maximum = int(config.get("maximum", 999999))
        tesseract_path = Path(config.get("tesseract_path", ""))
        if tesseract_path.is_file():
            pytesseract.pytesseract.tesseract_cmd = str(tesseract_path)

    def crop(self, frame: np.ndarray) -> np.ndarray:
        if frame is None or frame.ndim != 3:
            raise RoundDetectionError("Round frame is empty or invalid")
        try:
            return self.layout.crop(frame, self.crop_config)
        except RuntimeError as exc:
            raise RoundDetectionError(str(exc)) from exc

    def detect(self, frame: np.ndarray) -> RoundDetection:
        crop = self.crop(frame)
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        binary = cv2.threshold(
            gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
        )[1]
        try:
            raw = pytesseract.image_to_string(
                binary,
                config="--psm 7 -c tessedit_char_whitelist=0123456789",
                timeout=8,
            ).strip()
        except (RuntimeError, pytesseract.TesseractError) as exc:
            raise RoundDetectionError(f"Tesseract failed: {exc}") from exc
        digits = re.findall(r"\d+", raw)
        if not digits:
            raise RoundDetectionError(f"Round number not found; OCR={raw!r}")
        number = int(max(digits, key=len))
        if number < self.minimum or number > self.maximum:
            raise RoundDetectionError(
                f"Round {number} outside {self.minimum}..{self.maximum}"
            )
        return RoundDetection(number=number, raw_text=raw, crop=crop)
