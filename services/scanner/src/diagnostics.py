"""Read-only capture checks and local screenshot-based profile calibration."""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import time

import cv2
import numpy as np

from adb_capture import CaptureBlockedError
from countdown_detector import CountdownDetector
from detector import HistoryDetector
from popup_detector import is_result_popup
from round_detector import RoundDetector
from screen_geometry import FrameNormalizer, ReferenceLayout


def capture_frame(config: dict, image_path: Path | None):
    if image_path:
        raw = cv2.imdecode(np.frombuffer(image_path.read_bytes(), np.uint8), cv2.IMREAD_COLOR)
        if raw is None:
            raise RuntimeError(f"Cannot decode screenshot: {image_path}")
        if config.get("source", {}).get("type") == "adb":
            frame = FrameNormalizer(config.get("screen")).normalize(raw)
        else:
            emulator = config["emulator"]
            frame = cv2.resize(raw, (emulator["output_width"], emulator["output_height"]))
        return raw, frame, "offline-image"
    from scanner import create_frame_source
    source = create_frame_source(config)
    try:
        source.connect()
        for attempt in range(3):
            try:
                frame = source.capture()
                raw = getattr(source, "last_raw_frame", frame)
                return raw, frame, source.serial
            except CaptureBlockedError:
                raise
            except RuntimeError:
                if attempt == 2:
                    raise
                time.sleep(getattr(source, "reconnect_interval", 1))
    finally:
        source.close()


def save_image(path: Path, frame: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(".png", frame)
    if not ok:
        raise RuntimeError(f"Cannot encode image: {path}")
    path.write_bytes(encoded.tobytes())


def check_capture(config: dict, config_path: Path, output: Path, image_path: Path | None) -> int:
    output.mkdir(parents=True, exist_ok=True)
    try:
        raw, frame, serial = capture_frame(config, image_path)
    except RuntimeError as exc:
        text = json.dumps({"ready": False, "errors": {"capture": str(exc)}, "output": str(output)}, ensure_ascii=False, indent=2)
        (output / "report.json").write_text(text + "\n", encoding="utf-8")
        print(text)
        return 1
    save_image(output / "raw.png", raw)
    save_image(output / "normalized.png", frame)
    report = {"serial": serial, "native_size": [raw.shape[1], raw.shape[0]],
              "normalized_size": [frame.shape[1], frame.shape[0]], "errors": {}}
    detector = HistoryDetector((config_path.parent / config["paths"]["templates"]).resolve(),
                               config["history"], config["detection"])
    detection = None
    report["popup_visible"] = is_result_popup(frame, config["popup_dismiss"])
    try:
        if report["popup_visible"]:
            popup = detector.detect_popup_result(frame, config["popup_dismiss"])
            report["popup_result"] = {"code": popup.code, "confidence": popup.confidence}
            save_image(output / "popup-result.png", popup.crop)
        else:
            detection = detector.detect(frame)
            report["history"] = [{"code": s.code, "confidence": s.confidence, "margin": s.margin} for s in detection.slots]
    except (RuntimeError, OSError) as exc:
        report["errors"]["history_or_popup"] = str(exc)
    annotated = detector.annotate(frame, detection)
    for name, reader_type in (("round", RoundDetector), ("countdown", CountdownDetector)):
        layout = ReferenceLayout(config[name])
        x, y, w, h = layout.rect(frame.shape, config[name]["crop"])
        cv2.rectangle(annotated, (x, y), (x+w, y+h), (255, 160, 0), 2)
        cv2.putText(annotated, name, (max(0, x), max(15, y-5)), cv2.FONT_HERSHEY_SIMPLEX, .55, (255, 160, 0), 1)
        try:
            reader = reader_type(config[name])
            save_image(output / f"{name}.png", reader.crop(frame))
            result = reader.detect(frame)
            report[name] = {"value": result.number if name == "round" else result.seconds, "raw_text": result.raw_text}
        except (RuntimeError, OSError) as exc:
            report["errors"][name] = str(exc)
    save_image(output / "regions.png", annotated)
    # Countdown can be absent during spinning or a result popup.
    report["ready"] = (bool(detection or report.get("popup_result")) and "round" in report)
    report["output"] = str(output)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    (output / "report.json").write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if report["ready"] else 2


def save_profile(config: dict, source_path: Path, output: Path) -> None:
    # Paths in scanner profiles are relative to the profile, not the shell cwd.
    for key, value in config["paths"].items():
        absolute = (source_path.parent / value).resolve()
        try:
            config["paths"][key] = os.path.relpath(absolute, output.parent).replace("\\", "/")
        except ValueError:  # Different Windows drives.
            config["paths"][key] = str(absolute)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def calibrate(config: dict, config_path: Path, output: Path, image_path: Path | None, popup_only: bool = False) -> int:
    if output.suffix.lower() != ".json":
        raise ValueError("--calibrate requires --output PATH.json (a new device profile)")
    if output.exists():
        raise ValueError(f"Profile already exists: {output}; choose a new --output path")
    _, frame, serial = capture_frame(config, image_path)
    updated = deepcopy(config)
    height, width = frame.shape[:2]
    display_scale = min(1.0, 900 / height, 1100 / width)
    preview = cv2.resize(frame, None, fx=display_scale, fy=display_scale)

    def select(label):
        title = f"{label} - drag rectangle, Enter to confirm, C to cancel"
        box = cv2.selectROI(title, preview, showCrosshair=True, fromCenter=False)
        cv2.destroyWindow(title)
        x, y, w, h = [round(v / display_scale) for v in box]
        w, h = min(w, width-x), min(h, height-y)
        if w <= 0 or h <= 0:
            raise RuntimeError("Calibration cancelled; no profile saved")
        return {"x": x, "y": y, "width": w, "height": h}

    try:
        if popup_only:
            popup = updated["popup_dismiss"]
            # Rebase the tap before changing the popup reference dimensions.
            popup["tap_x"], popup["tap_y"] = ReferenceLayout(popup).point(frame.shape, popup["tap_x"], popup["tap_y"])
            for field, label in (("result_icon", "Winning popup icon only"),
                                 ("dim_region", "Dimmed game above popup"),
                                 ("dialog_region", "Bright popup background")):
                popup[field] = select(label)
            popup.update(reference_width=width, reference_height=height, scale_mode="width", anchor_y="bottom")
            # Calibration never enables automatic input.
            popup["enabled"] = False
        else:
            history = updated["history"]
            box = select("All 8 history icons, edge to edge, excluding Result label")
            spacing = box["width"] / 8
            history["slot_centers_x"] = [round(box["x"] + (i+.5)*spacing) for i in range(8)]
            history["slot_center_y"] = round(box["y"] + box["height"]/2)
            history["slot_half_size"] = max(8, round(min(spacing, box["height"])/2))
            history["crop"] = box
            history["new_marker"] = select("NEW marker below the newest history icon")
            for name in ("history", "round", "countdown"):
                updated[name].update(reference_width=width, reference_height=height,
                                     scale_mode="width", anchor_y="bottom" if name == "history" else "top")
            updated["round"]["crop"] = select("Round number digits only")
            updated["countdown"]["crop"] = select("Countdown digits and s suffix only")
        if serial != "offline-image" and updated.get("source", {}).get("type") == "adb":
            updated["source"]["serial"] = serial
    finally:
        cv2.destroyAllWindows()
    save_profile(updated, config_path, output)
    print(f"Saved profile: {output}\nRun --config \"{output}\" --check to verify the regions.")
    return 0
