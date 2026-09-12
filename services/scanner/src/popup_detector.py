from __future__ import annotations

import cv2
import numpy as np

from screen_geometry import ReferenceLayout


def is_result_popup(frame: np.ndarray, config: dict) -> bool:
    """Identify the dimmed game overlay plus the bright winner dialog at the bottom."""
    if frame is None or frame.size == 0 or frame.ndim != 3:
        return False

    layout = ReferenceLayout(config)
    try:
        dim_region = layout.crop(frame, config["dim_region"])
        dialog_region = layout.crop(frame, config["dialog_region"])
    except RuntimeError:
        return False
    if dim_region.size == 0 or dialog_region.size == 0:
        return False

    dim_brightness = float(cv2.cvtColor(dim_region, cv2.COLOR_BGR2GRAY).mean())
    bright_pixels = np.all(dialog_region >= int(config.get("bright_pixel_min", 200)), axis=2)
    bright_fraction = float(bright_pixels.mean())
    return (
        dim_brightness <= float(config.get("max_dim_brightness", 135))
        and bright_fraction >= float(config.get("min_dialog_bright_fraction", 0.35))
    )
