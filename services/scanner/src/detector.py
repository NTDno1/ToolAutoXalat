from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np


ITEMS: dict[str, dict[str, Any]] = {
    "BANH_MI": {
        "name": "Bánh mì",
        "category": "MEAT",
        "templates": ["IconListMi.png", "iconMi.png"],
    },
    "XIEN": {
        "name": "Xiên",
        "category": "MEAT",
        "templates": ["iconListXien.png", "IconXien.png"],
    },
    "DUI": {
        "name": "Đùi",
        "category": "MEAT",
        "templates": ["IconDui.png"],
    },
    "BO": {
        "name": "Bò",
        "category": "MEAT",
        "templates": ["iconBo.png"],
    },
    "CA_ROT": {
        "name": "Cà rốt",
        "category": "VEGETABLE",
        "templates": ["IconListRot.png", "iconRot.png"],
    },
    "NGO": {
        "name": "Ngô",
        "category": "VEGETABLE",
        "templates": ["IconListNgo.png", "iconNgo.png"],
    },
    "CAI": {
        "name": "Cải",
        "category": "VEGETABLE",
        "templates": ["IconListCai.png", "iconCai.png"],
    },
    "CA_CHUA": {
        "name": "Cà chua",
        "category": "VEGETABLE",
        "templates": ["IconListChua.png", "iconChua.png"],
    },
    "PIZZA": {
        "name": "Nổ Pizza",
        "category": "SPECIAL",
        "templates": ["PizzaIcon.png"],
    },
    "SALAD": {
        "name": "Nổ Xà lách",
        "category": "SPECIAL",
        "templates": ["SalatIcon.png"],
    },
}


@dataclass(frozen=True)
class SlotDetection:
    index: int
    code: str
    name: str
    category: str
    confidence: float
    margin: float
    center_x: int
    center_y: int
    scores: dict[str, float]


@dataclass(frozen=True)
class HistoryDetection:
    slots: list[SlotDetection]
    new_marker_score: float

    @property
    def sequence(self) -> list[str]:
        return [slot.code for slot in self.slots]

    @property
    def minimum_confidence(self) -> float:
        return min(slot.confidence for slot in self.slots)


class DetectionError(RuntimeError):
    pass


class HistoryDetector:
    """Classifies all eight history slots using multi-scale template matching."""

    def __init__(self, template_dir: Path, history_config: dict, detection_config: dict):
        self.template_dir = Path(template_dir)
        self.reference_width = int(history_config["reference_width"])
        self.reference_height = int(history_config["reference_height"])
        self.centers_x = [int(value) for value in history_config["slot_centers_x"]]
        self.center_y = int(history_config["slot_center_y"])
        self.half_size = int(history_config["slot_half_size"])
        self.history_crop = dict(history_config["crop"])
        self.new_marker = dict(history_config["new_marker"])

        self.minimum_confidence = float(detection_config["minimum_confidence"])
        self.minimum_margin = float(detection_config["minimum_margin"])
        self.high_confidence_override = float(detection_config["high_confidence_override"])
        self.large_template_max_edge = int(
            detection_config.get("large_template_max_edge", 48)
        )
        configured_scales = np.linspace(
            float(detection_config["template_scale_min"]),
            float(detection_config["template_scale_max"]),
            int(detection_config["template_scale_steps"]),
        )
        # Always retain the source icon at its native size. The configured
        # linspace does not necessarily include 1.0, which previously reduced
        # the score even when the on-screen Pizza/Salad icon was an exact match.
        self.scales = np.unique(np.append(configured_scales, 1.0))
        self.templates = self._load_templates()

    def _load_templates(self) -> dict[str, list[np.ndarray]]:
        loaded: dict[str, list[np.ndarray]] = {}
        missing: list[str] = []
        for code, item in ITEMS.items():
            variants: list[np.ndarray] = []
            for filename in item["templates"]:
                path = self.template_dir / filename
                image = cv2.imread(str(path), cv2.IMREAD_COLOR)
                if image is not None:
                    longest_edge = max(image.shape[:2])
                    if longest_edge > 96:
                        scale = self.large_template_max_edge / longest_edge
                        image = cv2.resize(
                            image,
                            None,
                            fx=scale,
                            fy=scale,
                            interpolation=cv2.INTER_AREA,
                        )
                    seen_shapes: set[tuple[int, int]] = set()
                    for scale in self.scales:
                        resized = cv2.resize(
                            image,
                            None,
                            fx=float(scale),
                            fy=float(scale),
                            interpolation=cv2.INTER_CUBIC,
                        )
                        shape = resized.shape[:2]
                        if shape not in seen_shapes:
                            variants.append(resized)
                            seen_shapes.add(shape)
            if not variants:
                missing.extend(item["templates"])
            loaded[code] = variants
        if missing:
            raise FileNotFoundError(
                "Missing detection templates: " + ", ".join(sorted(set(missing)))
            )
        return loaded

    @staticmethod
    def _scaled_rect(rect: dict, scale_x: float, scale_y: float) -> tuple[int, int, int, int]:
        x = round(int(rect["x"]) * scale_x)
        y = round(int(rect["y"]) * scale_y)
        width = round(int(rect["width"]) * scale_x)
        height = round(int(rect["height"]) * scale_y)
        return x, y, width, height

    def _best_template_score(self, slot: np.ndarray, variants: list[np.ndarray]) -> float:
        best = -1.0
        for template in variants:
            if template.shape[0] > slot.shape[0] or template.shape[1] > slot.shape[1]:
                continue
            score = float(
                cv2.matchTemplate(slot, template, cv2.TM_CCOEFF_NORMED).max()
            )
            best = max(best, score)
        return best

    def _classify_slot(
        self,
        slot: np.ndarray,
        index: int,
        center_x: int,
        center_y: int,
    ) -> SlotDetection:
        scores = {
            code: self._best_template_score(slot, variants)
            for code, variants in self.templates.items()
        }
        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        best_code, confidence = ranked[0]
        second_score = ranked[1][1]
        margin = confidence - second_score
        if confidence < self.minimum_confidence:
            raise DetectionError(
                f"Slot {index + 1}: confidence {confidence:.3f} below "
                f"{self.minimum_confidence:.3f}; best={best_code}"
            )
        if margin < self.minimum_margin and confidence < self.high_confidence_override:
            raise DetectionError(
                f"Slot {index + 1}: ambiguous {best_code}; "
                f"confidence={confidence:.3f}, margin={margin:.3f}"
            )
        item = ITEMS[best_code]
        return SlotDetection(
            index=index,
            code=best_code,
            name=item["name"],
            category=item["category"],
            confidence=confidence,
            margin=margin,
            center_x=center_x,
            center_y=center_y,
            scores=scores,
        )

    def detect(self, frame: np.ndarray) -> HistoryDetection:
        if frame is None or frame.ndim != 3:
            raise DetectionError("Frame is empty or has an invalid shape")
        height, width = frame.shape[:2]
        scale_x = width / self.reference_width
        scale_y = height / self.reference_height
        half_x = max(8, round(self.half_size * scale_x))
        half_y = max(8, round(self.half_size * scale_y))

        slots: list[SlotDetection] = []
        for index, reference_x in enumerate(self.centers_x):
            center_x = round(reference_x * scale_x)
            center_y = round(self.center_y * scale_y)
            left = max(0, center_x - half_x)
            top = max(0, center_y - half_y)
            right = min(width, center_x + half_x)
            bottom = min(height, center_y + half_y)
            slot = frame[top:bottom, left:right]
            if slot.size == 0:
                raise DetectionError(f"Slot {index + 1} is outside the frame")
            slots.append(self._classify_slot(slot, index, center_x, center_y))

        marker_x, marker_y, marker_width, marker_height = self._scaled_rect(
            self.new_marker, scale_x, scale_y
        )
        marker = frame[
            marker_y : marker_y + marker_height,
            marker_x : marker_x + marker_width,
        ]
        marker_score = 0.0
        if marker.size:
            hsv = cv2.cvtColor(marker, cv2.COLOR_BGR2HSV)
            yellow = cv2.inRange(hsv, (18, 110, 120), (42, 255, 255))
            marker_score = float(cv2.countNonZero(yellow)) / float(yellow.size)

        return HistoryDetection(slots=slots, new_marker_score=marker_score)

    def crop_history(self, frame: np.ndarray) -> np.ndarray:
        height, width = frame.shape[:2]
        scale_x = width / self.reference_width
        scale_y = height / self.reference_height
        x, y, crop_width, crop_height = self._scaled_rect(
            self.history_crop, scale_x, scale_y
        )
        return frame[y : y + crop_height, x : x + crop_width].copy()

    def annotate(self, frame: np.ndarray, detection: HistoryDetection | None) -> np.ndarray:
        output = frame.copy()
        scale_x = frame.shape[1] / self.reference_width
        scale_y = frame.shape[0] / self.reference_height
        half_x = round(self.half_size * scale_x)
        half_y = round(self.half_size * scale_y)
        for index, reference_x in enumerate(self.centers_x):
            center_x = round(reference_x * scale_x)
            center_y = round(self.center_y * scale_y)
            label = "?"
            color = (0, 0, 255)
            if detection and index < len(detection.slots):
                slot = detection.slots[index]
                label = f"{slot.code} {slot.confidence:.2f}"
                color = (40, 220, 40)
            cv2.rectangle(
                output,
                (center_x - half_x, center_y - half_y),
                (center_x + half_x, center_y + half_y),
                color,
                2,
            )
            cv2.putText(
                output,
                label,
                (center_x - half_x, center_y - half_y - 5),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.35,
                color,
                1,
                cv2.LINE_AA,
            )
        return output


def find_sequence_shift(previous: list[str], current: list[str]) -> int:
    """Return the number of newly prepended items, or zero when unaligned.

    History is newest-first. For one new result:
    current[1:] == previous[:7]. Supporting shifts up to seven rounds also lets
    the scanner recover after a temporary capture/backend outage.
    """
    if len(previous) != 8 or len(current) != 8:
        return 0
    if current == previous:
        return 0
    # More than four shifts leave fewer than four overlapping slots and are
    # too easy to match accidentally when outcomes repeat.
    for shift in range(1, 5):
        if current[shift:] == previous[: 8 - shift]:
            return shift
    return 0
