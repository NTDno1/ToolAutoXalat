from __future__ import annotations

import argparse
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path
import signal
import subprocess
import sys
import time
from typing import Optional
from zoneinfo import ZoneInfo

import cv2
import numpy as np
import requests


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


PROJECT_DIR = Path(__file__).resolve().parent
SRC_DIR = PROJECT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from bluestacks_capture import TimedBlueStacksWindowCapture  # noqa: E402
from countdown_detector import CountdownDetection, CountdownDetectionError, CountdownDetector  # noqa: E402
from database import ScannerDatabase, utc_now  # noqa: E402
from detector import (  # noqa: E402
    DetectionError,
    HistoryDetection,
    HistoryDetector,
    ITEMS,
    find_sequence_shift,
)
from popup_detector import is_result_popup  # noqa: E402
from round_detector import RoundDetectionError, RoundDetector  # noqa: E402


class BlueStacksFrameSource:
    """ADB connection plus Win32 background capture; never sends input."""

    def __init__(self, config: dict):
        self.adb_host = config["adb_host"]
        self.adb_port = int(config["adb_port"])
        self.adb_path = config["adb_path"]
        self.serial = f"{self.adb_host}:{self.adb_port}"
        self.output_size = (int(config["output_width"]), int(config["output_height"]))
        self.window_capture = TimedBlueStacksWindowCapture(
            adb_port=self.adb_port,
            window_title=config.get("window_title", ""),
            timeout_seconds=float(config.get("capture_timeout_seconds", 5)),
        )

    def connect(self) -> None:
        last_error = "unknown ADB error"
        for attempt in range(1, 4):
            try:
                result = subprocess.run(
                    [self.adb_path, "connect", self.serial],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                output = (result.stdout + result.stderr).strip()
                if result.returncode != 0 or "connected" not in output.lower():
                    raise RuntimeError(output or f"exit code {result.returncode}")

                # BlueStacks can list the device while `adb shell` remains
                # blocked. Capture uses Win32 PrintWindow, so get-state is the
                # non-invasive readiness check we actually need here.
                probe = subprocess.run(
                    [self.adb_path, "-s", self.serial, "get-state"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if probe.returncode == 0 and probe.stdout.strip() == "device":
                    return
                raise RuntimeError(
                    (probe.stdout + probe.stderr).strip()
                    or f"get-state exit code {probe.returncode}"
                )
            except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
                last_error = str(exc)
                if attempt < 3:
                    time.sleep(2)
        raise RuntimeError(
            f"ADB connection failed for {self.serial} after 3 attempts: {last_error}"
        )

    def capture(self) -> np.ndarray:
        frame = self.window_capture.capture()
        target_width, target_height = self.output_size
        if frame.shape[1] != target_width or frame.shape[0] != target_height:
            frame = cv2.resize(
                frame,
                (target_width, target_height),
                interpolation=cv2.INTER_LANCZOS4,
            )
        if frame.size == 0 or float(frame.std()) < 1.0:
            raise RuntimeError("Captured frame is blank")
        return frame


class GreedyScanner:
    def __init__(self, config_path: Path):
        self.config_path = config_path.resolve()
        self.config_dir = self.config_path.parent
        self.config = json.loads(self.config_path.read_text(encoding="utf-8"))

        paths = self.config["paths"]
        self.database_path = self._resolve(paths["database"])
        self.template_dir = self._resolve(paths["templates"])
        self.capture_dir = self._resolve(paths["captures"])
        self.log_dir = self._resolve(paths["logs"])
        self.capture_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)

        self.logger = self._configure_logging()
        self.database = ScannerDatabase(self.database_path)
        self.frame_source = BlueStacksFrameSource(self.config["emulator"])
        self.detector = HistoryDetector(
            self.template_dir,
            self.config["history"],
            self.config["detection"],
        )
        self.round_detector = RoundDetector(self.config["round"])
        self.countdown_detector = CountdownDetector(self.config["countdown"])
        self.countdown_scan_interval = max(
            0.5, float(self.config["countdown"].get("scan_interval_seconds", 0.75))
        )
        self.countdown_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="countdown-ocr"
        )
        self.countdown_future: Optional[Future[CountdownDetection]] = None
        self.countdown_future_observed_at: Optional[datetime] = None
        self.last_countdown_submit_monotonic = 0.0
        self.last_countdown_seconds: Optional[int] = None
        self.last_countdown_observed_at: Optional[datetime] = None
        self.local_timezone = ZoneInfo(
            self.config["round"].get("timezone", "Asia/Bangkok")
        )
        self.round_day_boundary_hour = max(
            0, min(23, int(self.config["round"].get("day_boundary_hour", 23)))
        )

        scanner = self.config["scanner"]
        self.scan_interval = max(0.25, float(scanner["scan_interval_seconds"]))
        self.post_popup_scan_interval = max(
            0.15,
            min(
                self.scan_interval,
                float(
                    scanner.get(
                        "post_popup_scan_interval_seconds", self.scan_interval
                    )
                ),
            ),
        )
        self.post_popup_fast_window = max(
            0.0, float(scanner.get("post_popup_fast_window_seconds", 0))
        )
        self.round_interval = float(scanner["round_interval_seconds"])
        self.max_failed_attempts = int(scanner["max_failed_attempts"])
        self.failure_cooldown = float(scanner["failure_alert_cooldown_seconds"])
        self.save_result_crops = bool(scanner.get("save_result_crops", True))
        self.max_error_captures = max(
            0, int(scanner.get("max_error_captures", 100))
        )

        popup = self.config.get("popup_dismiss", {})
        self.popup_dismiss_config = popup
        self.popup_dismiss_enabled = bool(popup.get("enabled", False))
        self.popup_input_serial = str(
            popup.get("input_serial", self.frame_source.serial)
        )
        self.popup_tap_x = int(popup.get("tap_x", self.frame_source.output_size[0] // 2))
        self.popup_tap_y = int(popup.get("tap_y", self.frame_source.output_size[1] // 2))
        self.popup_cooldown = max(0.0, float(popup.get("cooldown_seconds", 8)))
        self.popup_result_grace = max(
            0.0, float(popup.get("result_grace_seconds", 12))
        )
        self.last_popup_dismiss_monotonic = 0.0

        backend = self.config["backend"]
        self.backend_url = backend["base_url"].rstrip("/")
        self.backend_timeout = float(backend["request_timeout_seconds"])

        self.stop_requested = False
        self.previous_sequence = self._load_sequence()
        self.last_result_at = self._load_last_result_time()
        self.current_round = self._load_round()
        self.current_round_date = self.database.get_state(
            self._state_key("round_local_date")
        )
        self.first_successful_scan = True
        self.capture_failures = 0
        self.round_failures = 0
        self.no_result_attempts = 0
        self.last_alert_at: dict[str, datetime] = {}

    def _accept_countdown(self, seconds: int, observed_at: datetime) -> bool:
        previous = self.last_countdown_seconds
        previous_at = self.last_countdown_observed_at
        if previous is None or previous_at is None:
            return True
        elapsed = max(0.0, (observed_at - previous_at).total_seconds())
        if elapsed >= 5.0:
            return True
        expected = max(0, previous - round(elapsed))
        if previous <= 3 and seconds >= 20:
            return True
        return abs(seconds - expected) <= 4

    def _collect_countdown_detection(self) -> None:
        future = self.countdown_future
        if future is None or not future.done():
            return
        observed_at = self.countdown_future_observed_at or utc_now()
        self.countdown_future = None
        self.countdown_future_observed_at = None
        try:
            detection = future.result()
        except (CountdownDetectionError, OSError, RuntimeError):
            return
        if not self._accept_countdown(detection.seconds, observed_at):
            return
        self.last_countdown_seconds = detection.seconds
        self.last_countdown_observed_at = observed_at
        payload = {
            "seconds": detection.seconds,
            "observedAtUtc": observed_at.isoformat(timespec="milliseconds").replace(
                "+00:00", "Z"
            ),
        }
        self.database.set_state(
            "scanner_countdown", json.dumps(payload, separators=(",", ":"))
        )

    def _schedule_countdown_detection(self, frame: np.ndarray) -> None:
        self._collect_countdown_detection()
        if self.countdown_future is not None:
            return
        now = time.monotonic()
        if now - self.last_countdown_submit_monotonic < self.countdown_scan_interval:
            return
        self.last_countdown_submit_monotonic = now
        self.countdown_future_observed_at = utc_now()
        self.countdown_future = self.countdown_executor.submit(
            self.countdown_detector.detect, frame.copy()
        )

    def _dismiss_result_popup(self, frame: np.ndarray) -> bool:
        if not self.popup_dismiss_enabled or not is_result_popup(
            frame, self.popup_dismiss_config
        ):
            return False

        now = time.monotonic()
        if now - self.last_popup_dismiss_monotonic < self.popup_cooldown:
            return False

        try:
            result = subprocess.run(
                [
                    self.frame_source.adb_path,
                    "-s",
                    self.popup_input_serial,
                    "shell",
                    "input",
                    "tap",
                    str(self.popup_tap_x),
                    str(self.popup_tap_y),
                ],
                capture_output=True,
                text=True,
                timeout=3,
            )
            if result.returncode != 0:
                self.logger.warning(
                    "Popup detected but ADB dismiss failed: %s",
                    (result.stdout + result.stderr).strip() or result.returncode,
                )
                return False
        except (OSError, subprocess.TimeoutExpired) as exc:
            self.logger.warning("Popup detected but ADB dismiss failed: %s", exc)
            return False

        self.last_popup_dismiss_monotonic = now
        self.logger.info(
            "RESULT_POPUP_DISMISSED serial=%s tap=(%s,%s)",
            self.popup_input_serial,
            self.popup_tap_x,
            self.popup_tap_y,
        )
        return True

    def _resolve(self, value: str) -> Path:
        path = Path(value)
        return path.resolve() if path.is_absolute() else (self.config_dir / path).resolve()

    def _configure_logging(self) -> logging.Logger:
        logger = logging.getLogger("greedy-scanner")
        logger.setLevel(logging.INFO)
        logger.handlers.clear()
        formatter = logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(formatter)
        logger.addHandler(console)
        file_handler = logging.FileHandler(
            self.log_dir / "scanner.log", encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        return logger

    def _state_key(self, name: str) -> str:
        return f"{name}:{self.frame_source.serial}"

    def _load_sequence(self) -> Optional[list[str]]:
        raw = self.database.get_state(self._state_key("last_sequence"))
        if not raw:
            return None
        try:
            value = json.loads(raw)
            return value if isinstance(value, list) and len(value) == 8 else None
        except json.JSONDecodeError:
            return None

    def _load_last_result_time(self) -> datetime:
        raw = self.database.get_state(self._state_key("last_result_at_utc"))
        if raw:
            try:
                return datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                pass
        return utc_now()

    def _load_round(self) -> Optional[int]:
        raw = self.database.get_state(self._state_key("current_round"))
        try:
            return int(raw) if raw else None
        except ValueError:
            return None

    def _local_date(self) -> str:
        local_now = datetime.now(self.local_timezone)
        if self.round_day_boundary_hour != 0 and local_now.hour >= self.round_day_boundary_hour:
            local_now += timedelta(days=1)
        return local_now.date().isoformat()

    def _save_runtime_state(self, status: str, sequence: Optional[list[str]] = None) -> None:
        now = utc_now().isoformat(timespec="milliseconds").replace("+00:00", "Z")
        self.database.set_state("scanner_status", status)
        self.database.set_state("scanner_heartbeat_utc", now)
        self.database.set_state("scanner_source_serial", self.frame_source.serial)
        if sequence is not None:
            self.database.set_state("last_sequence", json.dumps(sequence))
            self.database.set_state(
                self._state_key("last_sequence"), json.dumps(sequence)
            )
        self.database.set_state(
            "last_result_at_utc",
            self.last_result_at.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        )
        self.database.set_state(
            self._state_key("last_result_at_utc"),
            self.last_result_at.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        )
        if self.current_round is not None:
            self.database.set_state("scanner_current_round", str(self.current_round))
            self.database.set_state(
                self._state_key("current_round"), str(self.current_round)
            )
        if self.current_round_date:
            self.database.set_state(
                self._state_key("round_local_date"), self.current_round_date
            )

    def _read_round(self, frame: np.ndarray) -> Optional[int]:
        try:
            detection = self.round_detector.detect(frame)
            self.round_failures = 0
            self.logger.info("ROUND OCR=%s raw=%r", detection.number, detection.raw_text)
            return detection.number
        except (RoundDetectionError, OSError) as exc:
            self.round_failures += 1
            self.logger.warning(
                "Round OCR attempt %s/%s failed: %s",
                self.round_failures,
                self.max_failed_attempts,
                exc,
            )
            if self.round_failures >= self.max_failed_attempts:
                self._notify_failure(
                    "ROUND_OCR_FAILED",
                    "Không đọc được số Today's Round sau 4 lần thử",
                    {"attempts": self.round_failures, "error": str(exc)},
                    frame,
                )
                self.round_failures = 0
            return None

    def _rounds_for_shift(
        self,
        shift: int,
        local_date: str,
        observed_round: Optional[int] = None,
    ) -> list[Optional[int]]:
        end_round = observed_round
        if end_round is None and self.current_round_date == local_date:
            end_round = (
                self.current_round + shift if self.current_round is not None else None
            )
        if end_round is None or end_round < shift:
            return [None] * shift
        return list(range(end_round - shift + 1, end_round + 1))

    def _prune_error_captures(self) -> None:
        if self.max_error_captures <= 0:
            return
        files = sorted(
            self.capture_dir.glob("error_*.png"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for path in files[self.max_error_captures :]:
            try:
                path.unlink()
            except OSError as exc:
                self.logger.warning("Could not prune debug capture %s: %s", path, exc)

    def _save_debug_frame(self, frame: Optional[np.ndarray], prefix: str) -> Optional[str]:
        if frame is None or self.max_error_captures <= 0:
            return None
        self._prune_error_captures()
        timestamp = utc_now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        path = self.capture_dir / f"{prefix}_{timestamp}.png"
        if not cv2.imwrite(str(path), frame):
            self.logger.warning("Could not write debug capture %s", path)
            return None
        self._prune_error_captures()
        return str(path)

    def _send_backend_event(self, event_id: int, payload: dict) -> None:
        try:
            response = requests.post(
                f"{self.backend_url}/api/scanner/events",
                json={
                    **payload,
                    "sourceKey": payload.get("sourceKey", f"scanner-db:{event_id}"),
                },
                timeout=self.backend_timeout,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            self.logger.warning("Backend event delivery failed: %s", exc)

    def _notify_failure(
        self,
        event_code: str,
        message: str,
        details: dict,
        frame: Optional[np.ndarray] = None,
    ) -> None:
        now = utc_now()
        last = self.last_alert_at.get(event_code)
        if last and (now - last).total_seconds() < self.failure_cooldown:
            return
        self.last_alert_at[event_code] = now
        debug_path = self._save_debug_frame(frame, f"error_{event_code.lower()}")
        full_details = {**details, "debugCapturePath": debug_path}
        source_key = f"scanner:{event_code}:{now.strftime('%Y%m%d%H%M%S')}"
        event_id = self.database.insert_event(
            "ERROR", event_code, message, full_details, source_key=source_key
        )
        self._send_backend_event(
            event_id,
            {
                "severity": "ERROR",
                "eventCode": event_code,
                "message": message,
                "details": full_details,
                "sourceKey": source_key,
                "occurredAtUtc": now.isoformat().replace("+00:00", "Z"),
            },
        )
        self.logger.error("%s | %s", event_code, message)

    def _save_result_crop(self, frame: np.ndarray, result_id: int) -> Optional[str]:
        if not self.save_result_crops:
            return None
        day_dir = self.capture_dir / utc_now().strftime("%Y-%m-%d")
        day_dir.mkdir(parents=True, exist_ok=True)
        path = day_dir / f"result_{result_id:08d}.png"
        cv2.imwrite(str(path), self.detector.crop_history(frame))
        self.database.update_result_capture(result_id, str(path))
        return str(path)

    def _record_result(
        self,
        code: str,
        detection: HistoryDetection,
        frame: np.ndarray,
        reason: str,
        round_number: Optional[int] = None,
        round_local_date: Optional[str] = None,
    ) -> int:
        round_local_date = round_local_date or self._local_date()
        slot = next((value for value in detection.slots if value.code == code), detection.slots[0])
        item = ITEMS[code]
        detected_at = utc_now()
        result_id = self.database.insert_result(
            item_code=code,
            item_name=item["name"],
            category=item["category"],
            confidence=slot.confidence,
            sequence=detection.sequence,
            detection_reason=reason,
            source_serial=self.frame_source.serial,
            round_number=round_number,
            round_local_date=round_local_date,
            detected_at=detected_at,
        )
        self.last_result_at = detected_at
        if round_number is not None:
            self.current_round = round_number
            self.current_round_date = round_local_date
        # Publish the committed row immediately. Saving the optional evidence
        # crop is not required by the dashboard and must not hold back its
        # realtime change signal.
        self.database.set_state(
            "last_result_id", str(result_id)
        )
        self._save_result_crop(frame, result_id)
        self.logger.info(
            "NEW RESULT id=%s round=%s item=%s category=%s confidence=%.3f reason=%s",
            result_id,
            round_number,
            item["name"],
            item["category"],
            slot.confidence,
            reason,
        )
        return result_id

    def _scan_once_legacy(self) -> dict:
        frame: Optional[np.ndarray] = None
        try:
            frame = self.frame_source.capture()
            # Detect and dismiss the result dialog before running the much
            # heavier eight-slot matcher. This exposes the updated history row
            # roughly one full detection pass earlier.
            if self._dismiss_result_popup(frame):
                self.capture_failures = 0
                self.no_result_attempts = 0
                self._save_runtime_state("POPUP_DISMISSED")
                return {"status": "popup_dismissed"}
            detection = self.detector.detect(frame)
            self.capture_failures = 0
        except (RuntimeError, DetectionError, OSError) as exc:
            if frame is not None and self._dismiss_result_popup(frame):
                self.capture_failures = 0
                self.no_result_attempts = 0
                self._save_runtime_state("POPUP_DISMISSED")
                return {"status": "popup_dismissed"}
            self.capture_failures += 1
            self._save_runtime_state("DETECTION_RETRY")
            self.logger.warning(
                "Detection attempt %s/%s failed: %s",
                self.capture_failures,
                self.max_failed_attempts,
                exc,
            )
            if self.capture_failures >= self.max_failed_attempts:
                self._notify_failure(
                    "DETECTION_FAILED",
                    f"Không nhận diện đủ 8 ô sau {self.capture_failures} lần quét",
                    {"attempts": self.capture_failures, "error": str(exc)},
                    frame,
                )
                self.capture_failures = 0
            return {"status": "detection_failed", "error": str(exc)}

        sequence = detection.sequence
        if self.previous_sequence is None:
            self.previous_sequence = sequence
            self.last_result_at = utc_now()
            self.first_successful_scan = False
            self._save_runtime_state("BASELINE_READY", sequence)
            self.logger.info(
                "Baseline ready | sequence=%s | min_confidence=%.3f",
                sequence,
                detection.minimum_confidence,
            )
            return {"status": "baseline", "sequence": sequence}

        if self.first_successful_scan:
            # A persisted sequence may be minutes or hours old. Compare it once
            # to recover a provable shift, but never infer an identical result
            # only from elapsed wall time immediately after process startup.
            startup_shift = find_sequence_shift(self.previous_sequence, sequence)
            if startup_shift:
                for code in reversed(sequence[:startup_shift]):
                    self._record_result(
                        code,
                        detection,
                        frame,
                        f"startup_recovered_shift_{startup_shift}",
                    )
            elif sequence != self.previous_sequence:
                self._notify_failure(
                    "SEQUENCE_DESYNC",
                    "Chuỗi 8 ô khi khởi động không còn căn chỉnh được; đã tạo baseline mới",
                    {"previous": self.previous_sequence, "current": sequence},
                    self.detector.annotate(frame, detection),
                )
            self.previous_sequence = sequence
            self.last_result_at = utc_now()
            self.first_successful_scan = False
            self.no_result_attempts = 0
            self._save_runtime_state("RUNNING", sequence)
            return {
                "status": "startup_baseline",
                "sequence": sequence,
                "recovered": startup_shift,
            }

        shift = find_sequence_shift(self.previous_sequence, sequence)
        elapsed = (utc_now() - self.last_result_at).total_seconds()
        recorded: list[int] = []
        if shift:
            # current is newest-first. Persist missed results oldest-first so
            # database chronology and streak calculations stay correct.
            for code in reversed(sequence[:shift]):
                recorded.append(
                    self._record_result(
                        code,
                        detection,
                        frame,
                        "sequence_shift" if shift == 1 else f"recovered_shift_{shift}",
                    )
                )
            self.previous_sequence = sequence
            self.no_result_attempts = 0
        elif sequence == self.previous_sequence:
            if elapsed >= self.round_interval:
                self.no_result_attempts += 1
                # If all eight outcomes are identical, the sequence cannot
                # visibly shift. The persistent NEW marker plus round timing is
                # the only observable signal, so record the first slot.
                if (
                    len(set(sequence)) == 1
                    and detection.new_marker_score >= 0.03
                    and self.no_result_attempts == 1
                ):
                    recorded.append(
                        self._record_result(
                            sequence[0], detection, frame, "same_sequence_timed_new_marker"
                        )
                    )
                    self.no_result_attempts = 0
                elif (
                    self.no_result_attempts >= self.max_failed_attempts
                    and elapsed >= self.round_interval + self.max_failed_attempts * self.scan_interval
                    and time.monotonic() - self.last_popup_dismiss_monotonic >= self.popup_result_grace
                ):
                    self._notify_failure(
                        "RESULT_TIMEOUT",
                        "Quá 4 lần quét sau thời điểm dự kiến nhưng chưa thấy kèo mới",
                        {
                            "attempts": self.no_result_attempts,
                            "elapsedSeconds": round(elapsed, 1),
                            "sequence": sequence,
                            "newMarkerScore": detection.new_marker_score,
                        },
                        frame,
                    )
                    self.no_result_attempts = 0
        else:
            self._notify_failure(
                "SEQUENCE_DESYNC",
                "Chuỗi 8 ô thay đổi nhưng không khớp quy tắc dịch lịch sử",
                {
                    "previous": self.previous_sequence,
                    "current": sequence,
                    "minimumConfidence": detection.minimum_confidence,
                },
                self.detector.annotate(frame, detection),
            )
            # Re-baseline avoids a permanent error loop; no uncertain result is
            # inserted into the statistics database.
            self.previous_sequence = sequence
            self.last_result_at = utc_now()
            self.no_result_attempts = 0

        self._save_runtime_state("RUNNING", self.previous_sequence)
        return {
            "status": "recorded" if recorded else "waiting",
            "recordedIds": recorded,
            "sequence": sequence,
            "minimumConfidence": detection.minimum_confidence,
            "newMarkerScore": detection.new_marker_score,
        }

    def scan_once(self) -> dict:
        self._collect_countdown_detection()
        frame: Optional[np.ndarray] = None
        try:
            frame = self.frame_source.capture()
            # OCR runs on its own worker so reading the on-screen 30-second
            # timer never delays history matching or result persistence.
            self._schedule_countdown_detection(frame)
            # The popup hides the complete history strip. Dismiss it before
            # template matching and temporarily enter the fast scan cadence so
            # the first fully visible eight-slot frame is persisted at once.
            if self._dismiss_result_popup(frame):
                self.capture_failures = 0
                self.no_result_attempts = 0
                self._save_runtime_state("POPUP_DISMISSED")
                return {"status": "popup_dismissed"}
            detection = self.detector.detect(frame)
            self.capture_failures = 0
        except (RuntimeError, DetectionError, OSError) as exc:
            if frame is not None and self._dismiss_result_popup(frame):
                self.capture_failures = 0
                self.no_result_attempts = 0
                self._save_runtime_state("POPUP_DISMISSED")
                return {"status": "popup_dismissed"}
            self.capture_failures += 1
            self._save_runtime_state("DETECTION_RETRY")
            self.logger.warning(
                "Detection attempt %s/%s failed: %s",
                self.capture_failures,
                self.max_failed_attempts,
                exc,
            )
            if self.capture_failures >= self.max_failed_attempts:
                self._notify_failure(
                    "DETECTION_FAILED",
                    f"Không nhận diện đủ 8 ô sau {self.capture_failures} lần quét",
                    {"attempts": self.capture_failures, "error": str(exc)},
                    frame,
                )
                self.capture_failures = 0
            return {"status": "detection_failed", "error": str(exc)}

        sequence = detection.sequence
        local_date = self._local_date()

        if self.previous_sequence is None:
            observed_round = self._read_round(frame)
            if observed_round is not None:
                self.current_round = observed_round
                self.current_round_date = local_date
            self.previous_sequence = sequence
            self.last_result_at = utc_now()
            self.first_successful_scan = False
            self._save_runtime_state("BASELINE_READY", sequence)
            self.logger.info(
                "Baseline ready | round=%s | sequence=%s | min_confidence=%.3f",
                self.current_round,
                sequence,
                detection.minimum_confidence,
            )
            return {
                "status": "baseline",
                "round": self.current_round,
                "sequence": sequence,
            }

        if self.first_successful_scan:
            observed_round = self._read_round(frame)
            startup_shift = find_sequence_shift(self.previous_sequence, sequence)
            recovered = 0
            if startup_shift:
                round_numbers = self._rounds_for_shift(
                    startup_shift, local_date, observed_round
                )
                for code, round_number in zip(
                    reversed(sequence[:startup_shift]), round_numbers
                ):
                    self._record_result(
                        code,
                        detection,
                        frame,
                        f"startup_recovered_shift_{startup_shift}",
                        round_number,
                        local_date,
                    )
                    recovered += 1
            elif (
                sequence == self.previous_sequence
                and observed_round is not None
                and self.current_round is not None
                and self.current_round_date == local_date
                and 1 <= observed_round - self.current_round <= 4
                and len(set(sequence)) == 1
            ):
                for round_number in range(self.current_round + 1, observed_round + 1):
                    self._record_result(
                        sequence[0],
                        detection,
                        frame,
                        "startup_round_recovered_identical",
                        round_number,
                        local_date,
                    )
                    recovered += 1
            elif sequence != self.previous_sequence:
                self._notify_failure(
                    "SEQUENCE_DESYNC",
                    "Chuỗi 8 ô khi khởi động không còn căn chỉnh được; đã tạo baseline mới",
                    {
                        "previous": self.previous_sequence,
                        "current": sequence,
                        "observedRound": observed_round,
                    },
                    self.detector.annotate(frame, detection),
                )

            self.previous_sequence = sequence
            if observed_round is not None:
                self.current_round = observed_round
                self.current_round_date = local_date
            self.last_result_at = utc_now()
            self.first_successful_scan = False
            self.no_result_attempts = 0
            self._save_runtime_state("RUNNING", sequence)
            return {
                "status": "startup_baseline",
                "round": self.current_round,
                "sequence": sequence,
                "recovered": recovered,
            }

        shift = find_sequence_shift(self.previous_sequence, sequence)
        elapsed = (utc_now() - self.last_result_at).total_seconds()
        recorded: list[int] = []
        observed_round: Optional[int] = None
        if self.current_round_date != local_date:
            observed_round = self._read_round(frame)

        if shift:
            if self.current_round is None and observed_round is None:
                observed_round = self._read_round(frame)
            round_numbers = self._rounds_for_shift(shift, local_date, observed_round)
            for code, round_number in zip(reversed(sequence[:shift]), round_numbers):
                recorded.append(
                    self._record_result(
                        code,
                        detection,
                        frame,
                        "sequence_shift" if shift == 1 else f"recovered_shift_{shift}",
                        round_number,
                        local_date,
                    )
                )
            self.previous_sequence = sequence
            self.no_result_attempts = 0
        elif sequence == self.previous_sequence:
            if observed_round is not None:
                self.current_round = observed_round
                self.current_round_date = local_date
            if elapsed >= self.round_interval:
                self.no_result_attempts += 1
                if (
                    len(set(sequence)) == 1
                    and detection.new_marker_score >= 0.03
                    and self.no_result_attempts == 1
                ):
                    confirmed_round = self._read_round(frame)
                    start_round = (
                        self.current_round + 1
                        if self.current_round is not None
                        and self.current_round_date == local_date
                        else None
                    )
                    if (
                        confirmed_round is not None
                        and start_round is not None
                        and 1 <= confirmed_round - self.current_round <= 4
                    ):
                        for round_number in range(start_round, confirmed_round + 1):
                            recorded.append(
                                self._record_result(
                                    sequence[0],
                                    detection,
                                    frame,
                                    "round_ocr_identical_sequence",
                                    round_number,
                                    local_date,
                                )
                            )
                        self.no_result_attempts = 0
                if (
                    self.no_result_attempts >= self.max_failed_attempts
                    and elapsed >= self.round_interval + self.max_failed_attempts * self.scan_interval
                    and time.monotonic() - self.last_popup_dismiss_monotonic >= self.popup_result_grace
                ):
                    self._notify_failure(
                        "RESULT_TIMEOUT",
                        "Quá 4 lần quét sau thời điểm dự kiến nhưng chưa thấy kèo mới",
                        {
                            "attempts": self.no_result_attempts,
                            "elapsedSeconds": round(elapsed, 1),
                            "round": self.current_round,
                            "sequence": sequence,
                            "newMarkerScore": detection.new_marker_score,
                        },
                        frame,
                    )
                    self.no_result_attempts = 0
        else:
            if observed_round is None:
                observed_round = self._read_round(frame)
            self._notify_failure(
                "SEQUENCE_DESYNC",
                "Chuỗi 8 ô thay đổi nhưng không khớp quy tắc dịch lịch sử",
                {
                    "previous": self.previous_sequence,
                    "current": sequence,
                    "observedRound": observed_round,
                    "minimumConfidence": detection.minimum_confidence,
                },
                self.detector.annotate(frame, detection),
            )
            self.previous_sequence = sequence
            self.last_result_at = utc_now()
            self.no_result_attempts = 0
            if observed_round is not None:
                self.current_round = observed_round
                self.current_round_date = local_date

        self._save_runtime_state("RUNNING", self.previous_sequence)
        return {
            "status": "recorded" if recorded else "waiting",
            "recordedIds": recorded,
            "round": self.current_round,
            "sequence": sequence,
            "minimumConfidence": detection.minimum_confidence,
            "newMarkerScore": detection.new_marker_score,
        }

    def run(self, once: bool = False) -> int:
        self.frame_source.connect()
        self.database.insert_event(
            "INFO",
            "SCANNER_STARTED",
            "Scanner đã kết nối BlueStacks và bắt đầu chạy nền",
            {"serial": self.frame_source.serial, "intervalSeconds": self.scan_interval},
        )
        self.logger.info(
            "Connected to %s; scan interval %.2fs, post-popup %.2fs for %.1fs",
            self.frame_source.serial,
            self.scan_interval,
            self.post_popup_scan_interval,
            self.post_popup_fast_window,
        )
        self._save_runtime_state("STARTING")
        while not self.stop_requested:
            started = time.monotonic()
            outcome = self.scan_once()
            elapsed = time.monotonic() - started
            if outcome.get("status") in {"popup_dismissed", "recorded"}:
                self.logger.info(
                    "SCAN_TIMING status=%s duration_ms=%d",
                    outcome["status"],
                    round(elapsed * 1000),
                )
            if once:
                break
            interval = self.scan_interval
            if (
                self.last_popup_dismiss_monotonic > 0
                and time.monotonic() - self.last_popup_dismiss_monotonic
                < self.post_popup_fast_window
            ):
                interval = self.post_popup_scan_interval
            remaining = interval - (time.monotonic() - started)
            if remaining > 0:
                time.sleep(remaining)
        self._collect_countdown_detection()
        self.countdown_executor.shutdown(wait=False, cancel_futures=True)
        self.frame_source.window_capture.close()
        self._save_runtime_state("STOPPED")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Greedy BIGO background result scanner")
    parser.add_argument(
        "--config",
        default=str(PROJECT_DIR / "config.json"),
        help="Path to scanner config.json",
    )
    parser.add_argument("--once", action="store_true", help="Capture/detect once then exit")
    args = parser.parse_args()

    scanner = GreedyScanner(Path(args.config))

    def request_stop(_signal, _frame):
        scanner.stop_requested = True

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    try:
        return scanner.run(once=args.once)
    except Exception as exc:
        scanner.database.insert_event(
            "CRITICAL",
            "SCANNER_CRASHED",
            "Scanner dừng do lỗi nghiêm trọng",
            {"error": repr(exc)},
        )
        scanner.logger.exception("Scanner crashed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
