from __future__ import annotations

import cv2
import numpy as np


def _scaled_crop(frame: np.ndarray, crop: dict, reference_width: int, reference_height: int) -> np.ndarray:
    height, width = frame.shape[:2]
    scale_x = width / max(1, reference_width)
    scale_y = height / max(1, reference_height)
    x1 = max(0, min(width, round(float(crop["x"]) * scale_x)))
    y1 = max(0, min(height, round(float(crop["y"]) * scale_y)))
    x2 = max(x1, min(width, round((float(crop["x"]) + float(crop["width"])) * scale_x)))
    y2 = max(y1, min(height, round((float(crop["y"]) + float(crop["height"])) * scale_y)))
    return frame[y1:y2, x1:x2]


def is_result_popup(frame: np.ndarray, config: dict) -> bool:
    """Identify the dimmed game overlay plus the bright winner dialog at the bottom."""
    if frame is None or frame.size == 0 or frame.ndim != 3:
        return False

    reference_width = int(config.get("reference_width", 720))
    reference_height = int(config.get("reference_height", 1500))
    dim_region = _scaled_crop(frame, config["dim_region"], reference_width, reference_height)
    dialog_region = _scaled_crop(frame, config["dialog_region"], reference_width, reference_height)
    if dim_region.size == 0 or dialog_region.size == 0:
        return False

    dim_brightness = float(cv2.cvtColor(dim_region, cv2.COLOR_BGR2GRAY).mean())
    bright_pixels = np.all(dialog_region >= int(config.get("bright_pixel_min", 200)), axis=2)
    bright_fraction = float(bright_pixels.mean())
    return (
        dim_brightness <= float(config.get("max_dim_brightness", 135))
        and bright_fraction >= float(config.get("min_dialog_bright_fraction", 0.35))
    )
