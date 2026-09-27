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
    method: str = "label"


class RoundDetector:
    """Read the `Today's N Round` label from a normalized Android frame."""

    def __init__(self, config: dict):
        self.layout = ReferenceLayout(config)
        self.reference_width = int(config["reference_width"])
        self.reference_height = int(config["reference_height"])
        self.crop_config = dict(config["crop"])
        self.label_crop_config = dict(config.get("label_crop", self.crop_config))
        self.minimum = int(config.get("minimum", 1))
        self.maximum = int(config.get("maximum", 999999))
        tesseract_path = Path(config.get("tesseract_path", ""))
        if tesseract_path.is_file():
            pytesseract.pytesseract.tesseract_cmd = str(tesseract_path)

    def crop(self, frame: np.ndarray) -> np.ndarray:
        if frame is None or frame.ndim != 3:
            raise RoundDetectionError("Round frame is empty or invalid")
        try:
            return self.layout.crop(frame, self.label_crop_config)
        except RuntimeError as exc:
            raise RoundDetectionError(str(exc)) from exc

    @staticmethod
    def parse_label(raw: str) -> int | None:
        """Return only a number anchored inside the complete round label.

        Reading an isolated numeric crop is unsafe here: letters at either edge
        can be interpreted as extra digits (for example 602 becoming 5602).
        """
        normalized = re.sub(r"\s+", " ", raw.replace("’", "'")).strip()
        match = re.search(
            r"\b(?:today|taday)'?s\D{0,8}(\d{1,4})\D{0,8}round\b",
            normalized,
            flags=re.IGNORECASE,
        )
        return int(match.group(1)) if match else None

    def detect(self, frame: np.ndarray) -> RoundDetection:
        crop = self.crop(frame)
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        binary = cv2.threshold(
            gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
        )[1]
        enlarged = cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        try:
            readings = [
                pytesseract.image_to_string(image, config="--psm 7", timeout=8).strip()
                for image in (gray, binary, enlarged)
            ]
        except (RuntimeError, pytesseract.TesseractError) as exc:
            raise RoundDetectionError(f"Tesseract failed: {exc}") from exc
        parsed = [number for raw in readings if (number := self.parse_label(raw)) is not None]
        unique = set(parsed)
        if not parsed or len(unique) != 1:
            raise RoundDetectionError(f"Round label not stable; OCR={readings!r}")
        number = parsed[0]
        if number < self.minimum or number > self.maximum:
            raise RoundDetectionError(
                f"Round {number} outside {self.minimum}..{self.maximum}"
            )
        return RoundDetection(number=number, raw_text=" | ".join(readings), crop=crop)
