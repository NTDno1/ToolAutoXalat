from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from screen_geometry import ReferenceLayout


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


@dataclass(frozen=True)
class PopupResultDetection:
    code: str
    name: str
    category: str
    confidence: float
    margin: float
    crop: np.ndarray
    scores: dict[str, float]


class DetectionError(RuntimeError):
    pass


class HistoryDetector:
    """Classifies all eight history slots using multi-scale template matching."""

    def __init__(self, template_dir: Path, history_config: dict, detection_config: dict):
        self.template_dir = Path(template_dir)
        self.layout = ReferenceLayout(history_config)
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
        self.pizza_override_confidence = float(
            detection_config.get("pizza_override_confidence", 0.58)
        )
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
        best_code, confidence, second_score, special_override = self._rank_scores(scores)
        margin = confidence - second_score
        if confidence < self.minimum_confidence:
            raise DetectionError(
                f"Slot {index + 1}: confidence {confidence:.3f} below "
                f"{self.minimum_confidence:.3f}; best={best_code}"
            )
        if (
            not special_override
            and margin < self.minimum_margin
            and confidence < self.high_confidence_override
        ):
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

    def _rank_scores(
        self, scores: dict[str, float]
    ) -> tuple[str, float, float, bool]:
        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        best_code, confidence = ranked[0]
        second_score = ranked[1][1]
        pizza_score = scores.get("PIZZA", -1.0)
        if pizza_score >= self.pizza_override_confidence:
            strongest_non_pizza = max(
                score for code, score in scores.items() if code != "PIZZA"
            )
            return "PIZZA", pizza_score, strongest_non_pizza, True
        return best_code, confidence, second_score, False

    def detect(self, frame: np.ndarray) -> HistoryDetection:
        if frame is None or frame.ndim != 3:
            raise DetectionError("Frame is empty or has an invalid shape")
        height, width = frame.shape[:2]
        scale_x, scale_y, offset_y = self.layout.transform(frame.shape)
        half_x = max(8, round(self.half_size * scale_x))
        half_y = max(8, round(self.half_size * scale_y))

        slots: list[SlotDetection] = []
        for index, reference_x in enumerate(self.centers_x):
            center_x = round(reference_x * scale_x)
            center_y = round(self.center_y * scale_y + offset_y)
            if center_x - half_x < 0 or center_y - half_y < 0 or center_x + half_x > width or center_y + half_y > height:
                raise DetectionError(f"Slot {index + 1} is outside the frame; calibrate the screen profile")
            left = max(0, center_x - half_x)
            top = max(0, center_y - half_y)
            right = min(width, center_x + half_x)
            bottom = min(height, center_y + half_y)
            slot = frame[top:bottom, left:right]
            if slot.size == 0:
                raise DetectionError(f"Slot {index + 1} is outside the frame")
            if scale_x != 1 or scale_y != 1:
                slot = cv2.resize(slot, (max(1, round(slot.shape[1] / scale_x)), max(1, round(slot.shape[0] / scale_y))), interpolation=cv2.INTER_AREA)
            slots.append(self._classify_slot(slot, index, center_x, center_y))

        marker_x, marker_y, marker_width, marker_height = self.layout.rect(frame.shape, self.new_marker)
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

    def detect_popup_result(
        self, frame: np.ndarray, popup_config: dict
    ) -> PopupResultDetection:
        """Classify the primary result icon while the five-second popup is visible."""
        if frame is None or frame.ndim != 3:
            raise DetectionError("Popup result frame is empty or invalid")

        rect = popup_config.get("result_icon")
        if not isinstance(rect, dict):
            raise DetectionError("Popup result icon region is not configured")

        layout = ReferenceLayout({"reference_width": self.reference_width,
                                  "reference_height": self.reference_height, **popup_config})
        try:
            crop = layout.crop(frame, rect)
        except RuntimeError as exc:
            raise DetectionError(str(exc)) from exc
        scale_x, scale_y, _ = layout.transform(frame.shape)

        # Popup icons are rendered much larger than the persistent history
        # icons used to build the templates. Downscale only the matching view;
        # retain the full-size crop as evidence.
        match_scale = max(
            0.2, min(1.0, float(popup_config.get("result_match_scale", 1.0)))
        )
        match_crop = crop
        if match_scale != 1.0 or scale_x != 1 or scale_y != 1:
            match_crop = cv2.resize(
                crop,
                None,
                fx=match_scale / scale_x,
                fy=match_scale / scale_y,
                interpolation=cv2.INTER_AREA,
            )
        scores = {
            code: self._best_template_score(match_crop, variants)
            for code, variants in self.templates.items()
        }
        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        best_code, confidence = ranked[0]
        second_score = ranked[1][1]
        margin = confidence - second_score
        minimum_confidence = float(
            popup_config.get("result_minimum_confidence", 0.65)
        )
        minimum_margin = float(popup_config.get("result_minimum_margin", 0.08))
        if confidence < minimum_confidence:
            raise DetectionError(
                f"Popup result confidence {confidence:.3f} below "
                f"{minimum_confidence:.3f}; best={best_code}"
            )
        if margin < minimum_margin:
            raise DetectionError(
                f"Popup result ambiguous {best_code}; confidence={confidence:.3f}, "
                f"margin={margin:.3f}"
            )

        item = ITEMS[best_code]
        return PopupResultDetection(
            code=best_code,
            name=item["name"],
            category=item["category"],
            confidence=confidence,
            margin=margin,
            crop=crop,
            scores=scores,
        )

    def crop_history(self, frame: np.ndarray) -> np.ndarray:
        return self.layout.crop(frame, self.history_crop)

    def annotate(self, frame: np.ndarray, detection: HistoryDetection | None) -> np.ndarray:
        output = frame.copy()
        scale_x, scale_y, offset_y = self.layout.transform(frame.shape)
        half_x = round(self.half_size * scale_x)
        half_y = round(self.half_size * scale_y)
        for index, reference_x in enumerate(self.centers_x):
            center_x = round(reference_x * scale_x)
            center_y = round(self.center_y * scale_y + offset_y)
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
