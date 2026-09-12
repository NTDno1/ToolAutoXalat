"""Reference coordinates and reversible screenshot normalization."""
from __future__ import annotations

import cv2
import numpy as np


class ReferenceLayout:
    def __init__(self, config: dict):
        self.width = int(config.get("reference_width", 720))
        self.height = int(config.get("reference_height", 1500))
        self.mode = config.get("scale_mode", "stretch")
        self.anchor = config.get("anchor_y", "top")
        if self.width <= 0 or self.height <= 0:
            raise ValueError("Reference dimensions must be positive")
        if self.mode not in {"stretch", "width"}:
            raise ValueError("scale_mode must be stretch or width")
        if self.anchor not in {"top", "center", "bottom"}:
            raise ValueError("anchor_y must be top, center or bottom")

    def transform(self, shape) -> tuple[float, float, float]:
        height, width = shape[:2]
        sx = width / self.width
        sy = height / self.height if self.mode == "stretch" else sx
        offset = (height - self.height * sy) * {"top": 0, "center": .5, "bottom": 1}[self.anchor]
        return sx, sy, offset

    def point(self, shape, x: float, y: float) -> tuple[int, int]:
        sx, sy, offset = self.transform(shape)
        return round(x * sx), round(y * sy + offset)

    def rect(self, shape, rect: dict) -> tuple[int, int, int, int]:
        x1, y1 = self.point(shape, rect["x"], rect["y"])
        x2, y2 = self.point(shape, rect["x"] + rect["width"], rect["y"] + rect["height"])
        return x1, y1, x2 - x1, y2 - y1

    def crop(self, frame: np.ndarray, rect: dict) -> np.ndarray:
        x, y, w, h = self.rect(frame.shape, rect)
        height, width = frame.shape[:2]
        if w <= 0 or h <= 0 or x < 0 or y < 0 or x + w > width or y + h > height:
            raise RuntimeError(f"Region {rect} is outside frame {width}x{height}; calibrate the screen profile")
        return frame[y:y+h, x:x+w].copy()


class FrameNormalizer:
    """Crop a fractional viewport, then scale to a fixed width without distortion.

    Fractions refer to the native screenshot, including system bars. No Android
    display settings are changed. The inverse is used exclusively for popup taps.
    """
    def __init__(self, config: dict | None = None):
        config = config or {}
        self.width = int(config.get("output_width", 720))
        self.viewport = config.get("viewport", {"x": 0, "y": 0, "width": 1, "height": 1})
        self.orientation = config.get("orientation", "portrait")
        v = self.viewport
        if self.width <= 0 or self.orientation not in {"portrait", "landscape", "any"}:
            raise ValueError("Invalid screen output_width or orientation")
        if not (0 <= v["x"] < 1 and 0 <= v["y"] < 1 and
                0 < v["width"] <= 1 and 0 < v["height"] <= 1 and
                v["x"] + v["width"] <= 1.000001 and v["y"] + v["height"] <= 1.000001):
            raise ValueError("screen.viewport must be a rectangle of fractions within 0..1")
        self.native_shape = None
        self.bounds = None
        self.output_size = None

    def normalize(self, frame: np.ndarray) -> np.ndarray:
        self.bounds = None  # A failed capture must never leave a usable tap mapping.
        if frame is None or frame.ndim != 3 or frame.size == 0:
            raise RuntimeError("ADB screenshot is empty or invalid")
        h, w = frame.shape[:2]
        if (self.orientation == "portrait" and w >= h) or (self.orientation == "landscape" and h >= w):
            raise RuntimeError(f"Screen rotated to {w}x{h}; restore {self.orientation} orientation")
        v = self.viewport
        x, y = round(v["x"] * w), round(v["y"] * h)
        right = min(w, round((v["x"] + v["width"]) * w))
        bottom = min(h, round((v["y"] + v["height"]) * h))
        if right <= x or bottom <= y:
            raise RuntimeError("Screen viewport contains no pixels")
        crop = frame[y:bottom, x:right]
        out_h = max(1, round(crop.shape[0] * self.width / crop.shape[1]))
        output = cv2.resize(crop, (self.width, out_h), interpolation=(
            cv2.INTER_AREA if self.width < crop.shape[1] else cv2.INTER_CUBIC))
        self.native_shape = frame.shape
        self.bounds = (x, y, right - x, bottom - y)
        self.output_size = (self.width, out_h)
        return output

    def to_native(self, x: int, y: int) -> tuple[int, int]:
        if self.bounds is None or self.output_size is None:
            raise RuntimeError("Capture a valid frame before mapping input")
        ow, oh = self.output_size
        if not (0 <= x < ow and 0 <= y < oh):
            raise RuntimeError("Popup tap is outside the normalized screen")
        left, top, w, h = self.bounds
        return min(left + w - 1, left + round(x * w / ow)), min(top + h - 1, top + round(y * h / oh))
