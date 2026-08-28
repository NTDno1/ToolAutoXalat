from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import signal
import subprocess
import sys
import time
from typing import Optional

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

from bluestacks_capture import BlueStacksWindowCapture  # noqa: E402
from database import ScannerDatabase, utc_now  # noqa: E402
from detector import (  # noqa: E402
    DetectionError,
    HistoryDetection,
    HistoryDetector,
    ITEMS,
    find_sequence_shift,
)


class BlueStacksFrameSource:
    """ADB connection plus Win32 background capture; never sends input."""

    def __init__(self, config: dict):
        self.adb_host = config["adb_host"]
        self.adb_port = int(config["adb_port"])
        self.adb_path = config["adb_path"]
        self.serial = f"{self.adb_host}:{self.adb_port}"
        self.output_size = (int(config["output_width"]), int(config["output_height"]))
        self.window_capture = BlueStacksWindowCapture(
            adb_port=self.adb_port,
            window_title=config.get("window_title", ""),
        )

    def connect(self) -> None:
        result = subprocess.run(
            [self.adb_path, "connect", self.serial],
            capture_output=True,
            text=True,
            timeout=10,
        )
        output = (result.stdout + result.stderr).strip()
        if result.returncode != 0 or "connected" not in output.lower():
            raise RuntimeError(f"ADB connect failed for {self.serial}: {output}")
        probe = subprocess.run(
            [self.adb_path, "-s", self.serial, "shell", "echo", "scanner-ready"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if probe.returncode != 0 or "scanner-ready" not in probe.stdout:
            raise RuntimeError(f"ADB probe failed for {self.serial}")

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

        scanner = self.config["scanner"]
        self.scan_interval = float(scanner["scan_interval_seconds"])
        self.round_interval = float(scanner["round_interval_seconds"])
        self.max_failed_attempts = int(scanner["max_failed_attempts"])
        self.failure_cooldown = float(scanner["failure_alert_cooldown_seconds"])
        self.save_result_crops = bool(scanner.get("save_result_crops", True))

        backend = self.config["backend"]
        self.backend_url = backend["base_url"].rstrip("/")
        self.backend_timeout = float(backend["request_timeout_seconds"])

        self.stop_requested = False
        self.previous_sequence = self._load_sequence()
        self.last_result_at = self._load_last_result_time()
        self.first_successful_scan = True
        self.capture_failures = 0
        self.no_result_attempts = 0
        self.last_alert_at: dict[str, datetime] = {}

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

    def _load_sequence(self) -> Optional[list[str]]:
        raw = self.database.get_state("last_sequence")
        if not raw:
            return None
        try:
            value = json.loads(raw)
            return value if isinstance(value, list) and len(value) == 8 else None
        except json.JSONDecodeError:
            return None

    def _load_last_result_time(self) -> datetime:
        raw = self.database.get_state("last_result_at_utc")
        if raw:
            try:
                return datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                pass
        return utc_now()

    def _save_runtime_state(self, status: str, sequence: Optional[list[str]] = None) -> None:
        now = utc_now().isoformat(timespec="milliseconds").replace("+00:00", "Z")
        self.database.set_state("scanner_status", status)
        self.database.set_state("scanner_heartbeat_utc", now)
        if sequence is not None:
            self.database.set_state("last_sequence", json.dumps(sequence))
        self.database.set_state(
            "last_result_at_utc",
            self.last_result_at.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        )

    def _save_debug_frame(self, frame: Optional[np.ndarray], prefix: str) -> Optional[str]:
        if frame is None:
            return None
        timestamp = utc_now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        path = self.capture_dir / f"{prefix}_{timestamp}.png"
        cv2.imwrite(str(path), frame)
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
    ) -> int:
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
            detected_at=detected_at,
        )
        self._save_result_crop(frame, result_id)
        self.last_result_at = detected_at
        self.database.set_state(
            "last_result_id", str(result_id)
        )
        self.logger.info(
            "NEW RESULT id=%s item=%s category=%s confidence=%.3f reason=%s",
            result_id,
            item["name"],
            item["category"],
            slot.confidence,
            reason,
        )
        return result_id

    def scan_once(self) -> dict:
        frame: Optional[np.ndarray] = None
        try:
            frame = self.frame_source.capture()
            detection = self.detector.detect(frame)
            self.capture_failures = 0
        except (RuntimeError, DetectionError, OSError) as exc:
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
                elif self.no_result_attempts >= self.max_failed_attempts:
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

    def run(self, once: bool = False) -> int:
        self.frame_source.connect()
        self.database.insert_event(
            "INFO",
            "SCANNER_STARTED",
            "Scanner đã kết nối BlueStacks và bắt đầu chạy nền",
            {"serial": self.frame_source.serial, "intervalSeconds": self.scan_interval},
        )
        self.logger.info(
            "Connected to %s; background scan interval %.1fs",
            self.frame_source.serial,
            self.scan_interval,
        )
        self._save_runtime_state("STARTING")
        while not self.stop_requested:
            started = time.monotonic()
            self.scan_once()
            if once:
                break
            remaining = self.scan_interval - (time.monotonic() - started)
            if remaining > 0:
                time.sleep(remaining)
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
