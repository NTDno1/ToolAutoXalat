from __future__ import annotations

import argparse
import gc
from concurrent.futures import Future, ThreadPoolExecutor
import ctypes
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import shutil
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

from adb_capture import (AdbFrameSource, CaptureBlockedError,
                         DeviceConnectionError, ReconnectPendingError,
                         list_devices, resolve_adb_path)  # noqa: E402
from betting_signal_detector import BettingSignalDetector  # noqa: E402
from screen_geometry import ReferenceLayout  # noqa: E402
from countdown_detector import CountdownDetection, CountdownDetectionError, CountdownDetector  # noqa: E402
from database import ScannerDatabase, utc_now  # noqa: E402
from detector import (  # noqa: E402
    DetectionError,
    HistoryDetection,
    HistoryDetector,
    ITEMS,
    PopupResultDetection,
    find_sequence_shift,
)
from financial_detector import (  # noqa: E402
    FinancialDetection,
    FinancialDetectionError,
    FinancialDetector,
)
from popup_detector import is_result_popup  # noqa: E402
from round_detector import RoundDetection, RoundDetectionError, RoundDetector  # noqa: E402
from verification import StableSequenceVerifier  # noqa: E402


class BlueStacksFrameSource:
    """ADB connection plus Win32 background capture; never sends input."""

    def __init__(self, config: dict):
        from bluestacks_capture import TimedBlueStacksWindowCapture
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

    def map_input_point(self, x: int, y: int) -> tuple[int, int]:
        # Legacy BlueStacks config stores Android input coordinates directly.
        return x, y

    def close(self) -> None:
        self.window_capture.close()


def load_config(config_path: Path, device: str | None = None) -> dict:
    config = json.loads(config_path.read_text(encoding="utf-8-sig"))
    if device:
        if config.get("source", {}).get("type", "bluestacks") != "adb":
            raise ValueError("--device requires an ADB profile, e.g. --config services/scanner/config.phone.json")
        config["source"]["serial"] = device
        if config["source"].get("connect_address") != device:
            config["source"]["connect_address"] = ""
    return config


def create_frame_source(config: dict):
    source = config.get("source", {})
    source_type = source.get("type", "bluestacks")
    if source_type == "bluestacks":
        return BlueStacksFrameSource(config["emulator"])
    if source_type == "adb":
        backend = source.get("capture_backend", "screencap")
        if backend == "scrcpy":
            from scrcpy_capture import ScrcpyFrameSource
            return ScrcpyFrameSource(source, config.get("screen"))
        if backend == "screencap":
            return AdbFrameSource(source, config.get("screen"))
        raise ValueError(f"Unknown source.capture_backend: {backend}")
    raise ValueError(f"Unknown source.type: {source_type}")


class GreedyScanner:
    def __init__(self, config_path: Path, device: str | None = None):
        self.config_path = config_path.resolve()
        self.config_dir = self.config_path.parent
        self.config = load_config(self.config_path, device)
        self.frame_source = create_frame_source(self.config)
        configured_input = self.config.get("popup_dismiss", {}).get("input_serial", "")
        if isinstance(self.frame_source, AdbFrameSource) and configured_input:
            raise ValueError("ADB popup input follows the captured device; remove popup_dismiss.input_serial")

        paths = self.config["paths"]
        self.database_path = self._resolve(paths["database"])
        self.template_dir = self._resolve(paths["templates"])
        self.capture_dir = self._resolve(paths["captures"])
        self.log_dir = self._resolve(paths["logs"])
        self.live_frame_path = self._resolve(paths["live_frame"]) if paths.get("live_frame") else None
        self.last_live_frame_write_monotonic = 0.0
        self.capture_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)

        self.logger = self._configure_logging()
        self.database = ScannerDatabase(self.database_path)
        self.detector = HistoryDetector(
            self.template_dir,
            self.config["history"],
            self.config["detection"],
        )
        self.round_detector = RoundDetector(self.config["round"])
        self.round_scan_interval = max(
            1.0, float(self.config["round"].get("scan_interval_seconds", 2.0))
        )
        self.round_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="round-ocr"
        )
        self.round_future: Optional[Future[RoundDetection]] = None
        self.round_future_observed_at: Optional[datetime] = None
        self.last_round_submit_monotonic = 0.0
        self.active_round: Optional[int] = None
        self.active_round_observed_at: Optional[datetime] = None
        self.active_round_date: Optional[str] = None
        self.round_reanchor_candidate: Optional[int] = None
        self.round_reanchor_candidate_date: Optional[str] = None
        self.round_reanchor_confirmations = 0
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
        financial_config = self.config.get("financials", {})
        self.financial_detector = FinancialDetector(financial_config)
        self.financial_scan_interval = max(
            0.5, float(financial_config.get("scan_interval_seconds", 1.0))
        )
        self.financial_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="financial-ocr"
        )
        self.financial_future: Optional[Future[FinancialDetection]] = None
        self.financial_future_frame: Optional[np.ndarray] = None
        self.last_financial_balance: Optional[int] = None
        self.financial_future_observed_at: Optional[datetime] = None
        self.last_financial_submit_monotonic = 0.0
        betting_config = self.config.get("betting_signals", {})
        self.betting_signal_detector = BettingSignalDetector(betting_config)
        self.betting_signal_scan_interval = max(
            0.25, float(betting_config.get("scan_interval_seconds", 0.75))
        )
        self.last_betting_signal_scan_monotonic = 0.0
        self.betting_signal_round: Optional[int] = None
        self.betting_signal_hot: Optional[str] = None
        self.betting_signal_coin_max: dict[str, int] = {}
        self.last_betting_signal_countdown: Optional[int] = None
        self.local_timezone = ZoneInfo(
            self.config["round"].get("timezone", "Asia/Bangkok")
        )
        self.round_day_boundary_hour = max(
            0, min(23, int(self.config["round"].get("day_boundary_hour", 23)))
        )
        self.round_reanchor_required = max(
            2, int(self.config["round"].get("reanchor_confirmations", 3))
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
        self.no_result_alert_after_attempts = max(
            1,
            int(
                scanner.get(
                    "no_result_alert_after_attempts", self.max_failed_attempts
                )
            ),
        )
        self.failure_cooldown = float(scanner["failure_alert_cooldown_seconds"])
        self.reconnect_alert_after = max(
            1, int(scanner.get("reconnect_alert_after_attempts", 5))
        )
        self.save_result_crops = bool(scanner.get("save_result_crops", True))
        self.max_error_captures = max(
            0, int(scanner.get("max_error_captures", 100))
        )
        self.capture_retention_days = max(
            1, int(scanner.get("capture_retention_days", 2))
        )
        self.last_capture_prune_monotonic = 0.0
        self.memory_watchdog_enabled = bool(
            scanner.get("memory_watchdog_enabled", True)
        )
        self.memory_check_interval = max(
            5.0, float(scanner.get("memory_check_interval_seconds", 30))
        )
        self.memory_warning_mb = max(
            256.0, float(scanner.get("memory_warning_mb", 1500))
        )
        self.memory_restart_mb = max(
            self.memory_warning_mb + 256.0,
            float(scanner.get("memory_restart_mb", 3000)),
        )
        self.memory_trim_enabled = bool(scanner.get("memory_trim_enabled", True))
        self.last_memory_check_monotonic = 0.0
        self.memory_warning_sent = False

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
        self.popup_result_confirmations = max(
            1, int(popup.get("result_confirmations", 2))
        )
        self.popup_result_candidate_timeout = max(
            0.5, float(popup.get("result_candidate_timeout_seconds", 2))
        )
        self.history_verifier = StableSequenceVerifier(
            required_confirmations=int(popup.get("history_confirmations", 3)),
            timeout_seconds=float(
                popup.get("history_confirmation_timeout_seconds", 2)
            ),
        )
        self.last_popup_dismiss_monotonic = 0.0
        self.last_popup_seen_monotonic = 0.0
        self.popup_candidate_code: Optional[str] = None
        self.popup_candidate_count = 0
        self.popup_candidate_seen_monotonic = 0.0
        self.pending_popup_result_code: Optional[str] = None
        self.pending_popup_result_id: Optional[int] = None
        self.pending_popup_round_number: Optional[int] = None
        self.pending_popup_round_local_date: Optional[str] = None
        self.pending_popup_result_monotonic = 0.0

        backend = self.config["backend"]
        self.backend_url = backend["base_url"].rstrip("/")
        self.backend_timeout = float(backend["request_timeout_seconds"])

        self.stop_requested = False
        # Restore source-specific state only after auto-selection pins the serial.
        self.previous_sequence = None
        self.last_result_at = utc_now()
        self.current_round = None
        self.current_round_date = None
        self.first_successful_scan = True
        self.capture_failures = 0
        self.connection_failures = 0
        self.connection_alert_sent = False
        self.round_failures = 0
        self.no_result_attempts = 0
        self.last_alert_at: dict[str, datetime] = {}
        self._prune_result_capture_directories(force=True)

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

    def _collect_financial_detection(self) -> None:
        future = getattr(self, "financial_future", None)
        if future is None or not future.done():
            return
        observed_at = self.financial_future_observed_at or utc_now()
        observed_frame = self.financial_future_frame
        self.financial_future = None
        self.financial_future_frame = None
        self.financial_future_observed_at = None
        try:
            detection = future.result()
        except (FinancialDetectionError, OSError, RuntimeError):
            return
        if (self.live_frame_path is not None and observed_frame is not None and
                self.last_financial_balance is not None and
                detection.balance_units != self.last_financial_balance):
            evidence = self.log_dir / (
                f"balance_{self.last_financial_balance}_to_{detection.balance_units}_"
                f"{observed_at.strftime('%Y%m%d_%H%M%S_%f')[:-3]}.png"
            )
            cv2.imwrite(str(evidence), observed_frame)
        self.last_financial_balance = detection.balance_units
        payload = {
            "observedAtUtc": observed_at.isoformat(timespec="milliseconds").replace(
                "+00:00", "Z"
            ),
            "balanceUnits": detection.balance_units,
            "ownBets": detection.own_bets,
        }
        self.database.set_state(
            "scanner_financials",
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        )

    def _schedule_financial_detection(self, frame: np.ndarray) -> None:
        self._collect_financial_detection()
        detector = getattr(self, "financial_detector", None)
        if detector is None or not detector.enabled or getattr(self, "financial_future", None) is not None:
            return
        now = time.monotonic()
        if now - self.last_financial_submit_monotonic < self.financial_scan_interval:
            return
        self.last_financial_submit_monotonic = now
        self.financial_future_observed_at = utc_now()
        self.financial_future_frame = frame.copy() if self.live_frame_path is not None else None
        self.financial_future = self.financial_executor.submit(
            detector.detect, frame.copy()
        )

    def _update_betting_signals(self, frame: np.ndarray) -> None:
        if not self.betting_signal_detector.enabled:
            return
        seconds = self.last_countdown_seconds
        observed_at = self.last_countdown_observed_at
        if seconds is None or observed_at is None or not 1 <= seconds <= 30:
            return
        if (utc_now() - observed_at).total_seconds() > 4:
            return
        now_monotonic = time.monotonic()
        if now_monotonic - self.last_betting_signal_scan_monotonic < self.betting_signal_scan_interval:
            return
        self.last_betting_signal_scan_monotonic = now_monotonic

        detected_round = self.active_round
        if detected_round is None and self.current_round is not None:
            detected_round = self.current_round + 1
        new_betting_window = (
            self.last_betting_signal_countdown is not None
            and self.last_betting_signal_countdown <= 3
            and seconds >= 20
        )
        if detected_round != self.betting_signal_round or new_betting_window:
            self.betting_signal_round = detected_round
            self.betting_signal_hot = None
            self.betting_signal_coin_max = {}

        try:
            detection = self.betting_signal_detector.detect(frame)
        except (RuntimeError, ValueError, cv2.error) as exc:
            self.logger.warning("Betting signal detection skipped: %s", exc)
            return

        if detection.hot_item_code is not None:
            self.betting_signal_hot = detection.hot_item_code
        for item in detection.items:
            self.betting_signal_coin_max[item.item_code] = max(
                self.betting_signal_coin_max.get(item.item_code, 0),
                item.coin_count,
            )
        self.last_betting_signal_countdown = seconds
        observed_utc = utc_now().isoformat(timespec="milliseconds").replace(
            "+00:00", "Z"
        )
        signal_items = [
            {
                "itemCode": item.item_code,
                "coinCount": self.betting_signal_coin_max.get(item.item_code, 0),
                "activityPercent": round(
                    self.betting_signal_coin_max.get(item.item_code, 0)
                    * 100
                    / self.betting_signal_detector.max_coins
                ),
            }
            for item in detection.items
        ]
        payload = {
            "round": self.betting_signal_round,
            "observedAtUtc": observed_utc,
            "countdownSeconds": seconds,
            "hotItemCode": self.betting_signal_hot,
            "items": signal_items,
        }
        self.database.set_state(
            "scanner_betting_signals",
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        )
        if self.betting_signal_round is not None:
            self.database.upsert_betting_signal_snapshot(
                self.frame_source.serial,
                self._local_date(),
                self.betting_signal_round,
                observed_utc,
                self.betting_signal_hot,
                signal_items,
            )

    def _publish_active_round(self, number: int, observed_at: datetime) -> bool:
        local_date = self._local_date()
        reference_round = self.active_round
        reference_observed_at = self.active_round_observed_at
        reference_date = self.active_round_date
        # Prefer the latest completed round for this business date. A single
        # bad asynchronous OCR read must not become the permanent reference.
        if self.current_round is not None and self.current_round_date == local_date and (
            reference_round is None
            or reference_date != local_date
            or self.current_round > reference_round
        ):
            reference_round = self.current_round
            reference_observed_at = self.last_result_at
            reference_date = self.current_round_date
        maximum_plausible = self._maximum_plausible_round(observed_at)
        if number > maximum_plausible:
            self.logger.warning(
                "ROUND OCR rejected as impossible for current day: detected=%s maximum=%s",
                number,
                maximum_plausible,
            )
            return False

        corrupt_reference = (
            reference_round is not None
            and reference_date == local_date
            and reference_round > maximum_plausible
        )
        return self._accept_published_round(
            number,
            observed_at,
            local_date,
            reference_round,
            reference_observed_at,
            reference_date,
            maximum_plausible,
            corrupt_reference,
        )

    def _maximum_plausible_round(self, observed_at: datetime) -> int:
        maximum_configured = getattr(
            getattr(self, "round_detector", None), "maximum", 999999
        )
        local_observed = observed_at.astimezone(self.local_timezone)
        boundary = local_observed.replace(
            hour=self.round_day_boundary_hour, minute=0, second=0, microsecond=0
        )
        if local_observed < boundary:
            boundary -= timedelta(days=1)
        # Round 1 starts at the configured 23:00 boundary. The small allowance
        # covers clock drift and temporary changes in the game's round duration.
        maximum_for_time = max(
            30,
            int((local_observed - boundary).total_seconds() / max(1.0, self.round_interval))
            + 30,
        )
        return min(maximum_configured, maximum_for_time)

    def _accept_published_round(
        self,
        number: int,
        observed_at: datetime,
        local_date: str,
        reference_round: Optional[int],
        reference_observed_at: Optional[datetime],
        reference_date: Optional[str],
        maximum_plausible: int,
        corrupt_reference: bool,
    ) -> bool:
        if (
            reference_round is not None
            and reference_date == local_date
            and number < reference_round
            and not corrupt_reference
        ):
            self.round_reanchor_candidate = None
            self.round_reanchor_candidate_date = None
            self.round_reanchor_confirmations = 0
            self.logger.warning(
                "ROUND OCR rejected as regression: detected=%s reference=%s",
                number,
                reference_round,
            )
            return False
        if corrupt_reference:
            previous_candidate = getattr(self, "round_reanchor_candidate", None)
            candidate_date = getattr(self, "round_reanchor_candidate_date", None)
            if (
                previous_candidate is not None
                and candidate_date == local_date
                and 0 <= number - previous_candidate <= 1
            ):
                self.round_reanchor_confirmations += 1
                self.round_reanchor_candidate = number
            else:
                self.round_reanchor_candidate = number
                self.round_reanchor_candidate_date = local_date
                self.round_reanchor_confirmations = 1
            required = getattr(self, "round_reanchor_required", 3)
            if self.round_reanchor_confirmations < required:
                self.logger.warning(
                    "ROUND OCR repairing impossible reference: detected=%s reference=%s "
                    "confirmations=%s/%s",
                    number,
                    reference_round,
                    self.round_reanchor_confirmations,
                    required,
                )
                return False
            self.logger.warning(
                "ROUND OCR repaired impossible reference: detected=%s reference=%s",
                number,
                reference_round,
            )
        if (
            reference_round is not None
            and reference_observed_at is not None
            and reference_date == local_date
        ):
            elapsed = max(
                0.0, (observed_at - reference_observed_at).total_seconds()
            )
            maximum_step = max(2, int(elapsed / max(1.0, self.round_interval)) + 2)
            if number - reference_round > maximum_step:
                previous_candidate = getattr(self, "round_reanchor_candidate", None)
                candidate_date = getattr(self, "round_reanchor_candidate_date", None)
                if (
                    previous_candidate is not None
                    and candidate_date == local_date
                    and 0 <= number - previous_candidate <= 1
                ):
                    self.round_reanchor_confirmations = (
                        getattr(self, "round_reanchor_confirmations", 0) + 1
                    )
                    self.round_reanchor_candidate = number
                else:
                    self.round_reanchor_candidate = number
                    self.round_reanchor_candidate_date = local_date
                    self.round_reanchor_confirmations = 1

                required = getattr(self, "round_reanchor_required", 3)
                if self.round_reanchor_confirmations < required:
                    self.logger.warning(
                        "ROUND OCR jump awaiting confirmation: detected=%s "
                        "reference=%s max_step=%s confirmations=%s/%s",
                        number,
                        reference_round,
                        maximum_step,
                        self.round_reanchor_confirmations,
                        required,
                    )
                    return False
                self.logger.warning(
                    "ROUND OCR re-anchored after stable jump: detected=%s "
                    "reference=%s confirmations=%s",
                    number,
                    reference_round,
                    self.round_reanchor_confirmations,
                )

        self.round_reanchor_candidate = None
        self.round_reanchor_candidate_date = None
        self.round_reanchor_confirmations = 0

        changed = number != self.active_round or local_date != self.active_round_date
        self.active_round = number
        self.active_round_observed_at = observed_at
        self.active_round_date = local_date
        observed_utc = observed_at.isoformat(timespec="milliseconds").replace(
            "+00:00", "Z"
        )
        states = {
            "scanner_active_round": str(number),
            "scanner_active_round_observed_at_utc": observed_utc,
        }
        if (
            self.popup_candidate_code is None
            and self.pending_popup_result_code is None
            and (
                self.current_round_date != local_date
                or self.current_round is None
                or number > self.current_round
                or corrupt_reference
            )
        ):
            # Outside a result popup the app label is the latest completed
            # round. Re-anchor inference here so missed detections cannot
            # leave every later result permanently offset.
            self.current_round = number
            self.current_round_date = local_date
            states["scanner_current_round"] = str(number)
            states[self._state_key("current_round")] = str(number)
            states[self._state_key("round_local_date")] = local_date
        self.database.set_states(states)
        if changed:
            self.logger.info("ACTIVE ROUND OCR=%s", number)
        return True

    def _collect_round_detection(self) -> None:
        future = self.round_future
        if future is None or not future.done():
            return
        observed_at = self.round_future_observed_at or utc_now()
        self.round_future = None
        self.round_future_observed_at = None
        try:
            detection = future.result()
        except (RoundDetectionError, OSError, RuntimeError):
            return
        self._publish_active_round(detection.number, observed_at)

    def _schedule_round_detection(self, frame: np.ndarray) -> None:
        self._collect_round_detection()
        if self.round_future is not None:
            return
        now = time.monotonic()
        if now - self.last_round_submit_monotonic < self.round_scan_interval:
            return
        self.last_round_submit_monotonic = now
        self.round_future_observed_at = utc_now()
        self.round_future = self.round_executor.submit(
            self.round_detector.detect, frame.copy()
        )

    def _round_for_popup_result(
        self, frame: np.ndarray, local_date: str
    ) -> Optional[int]:
        self._collect_round_detection()
        if (
            self.active_round is not None
            and self.active_round_date == local_date
            and self.active_round_observed_at is not None
            and (utc_now() - self.active_round_observed_at).total_seconds()
            <= self.round_interval + 5
        ):
            return self.active_round

        observed_round = self._read_round(frame)
        if observed_round is not None:
            if self._publish_active_round(observed_round, utc_now()):
                return observed_round
        if self.current_round is not None and self.current_round_date == local_date:
            return self.current_round + 1
        return None

    def _dismiss_result_popup(
        self, frame: np.ndarray, assume_visible: bool = False
    ) -> bool:
        if not self.popup_dismiss_enabled or (
            not assume_visible
            and not is_result_popup(frame, self.popup_dismiss_config)
        ):
            return False

        now = time.monotonic()
        if now - self.last_popup_dismiss_monotonic < self.popup_cooldown:
            return False

        try:
            tap_x, tap_y = ReferenceLayout(self.popup_dismiss_config).point(
                frame.shape, self.popup_tap_x, self.popup_tap_y
            )
            tap_x, tap_y = self.frame_source.map_input_point(tap_x, tap_y)
            result = subprocess.run(
                [
                    self.frame_source.adb_path,
                    "-s",
                    self.popup_input_serial,
                    "shell",
                    "input",
                    "tap",
                    str(tap_x),
                    str(tap_y),
                ],
                capture_output=True,
                text=True,
                timeout=3,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if result.returncode != 0:
                self.logger.warning(
                    "Popup detected but ADB dismiss failed: %s",
                    (result.stdout + result.stderr).strip() or result.returncode,
                )
                return False
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            self.logger.warning("Popup detected but ADB dismiss failed: %s", exc)
            return False

        self.last_popup_dismiss_monotonic = now
        self.logger.info(
            "RESULT_POPUP_DISMISSED serial=%s tap=(%s,%s)",
            self.popup_input_serial,
            tap_x,
            tap_y,
        )
        return True

    def _clear_popup_candidate(self) -> None:
        self.popup_candidate_code = None
        self.popup_candidate_count = 0
        self.popup_candidate_seen_monotonic = 0.0

    def _clear_pending_popup_result(self) -> None:
        self.pending_popup_result_code = None
        self.pending_popup_result_id = None
        self.pending_popup_round_number = None
        self.pending_popup_round_local_date = None
        self.pending_popup_result_monotonic = 0.0
        self.history_verifier.reset()

    def _touch_result_revision(self) -> None:
        revision = utc_now().isoformat(timespec="milliseconds").replace("+00:00", "Z")
        self.database.set_state("last_result_revision", revision)

    def _handle_result_popup(self, frame: np.ndarray) -> dict:
        now = time.monotonic()
        self.last_popup_seen_monotonic = now

        try:
            detection = self.detector.detect_popup_result(
                frame, self.popup_dismiss_config
            )
        except DetectionError as exc:
            if (
                self.popup_candidate_seen_monotonic > 0
                and now - self.popup_candidate_seen_monotonic
                > self.popup_result_candidate_timeout
            ):
                self._clear_popup_candidate()
            self._save_runtime_state("RESULT_POPUP")
            return {"status": "popup_waiting", "error": str(exc)}

        pending_age = now - self.pending_popup_result_monotonic
        if self.pending_popup_result_code is not None and pending_age < max(
            self.popup_result_grace, self.post_popup_fast_window + 2.0
        ):
            dismissed = self._dismiss_result_popup(frame, assume_visible=True)
            self._save_runtime_state("VERIFYING_RESULT")
            return {
                "status": (
                    "popup_already_recorded"
                    if self.pending_popup_result_code == detection.code
                    else "popup_conflicting_candidate"
                ),
                "code": detection.code,
                "resultId": self.pending_popup_result_id,
                "dismissed": dismissed,
            }
        if self.pending_popup_result_code is not None:
            self._clear_pending_popup_result()

        # During some game transitions the persistent history strip becomes
        # readable a few seconds before the popup animation settles. If that
        # shift already committed this same item, the popup is confirmation of
        # the existing round rather than a second result.
        recent_result_age = (utc_now() - self.last_result_at).total_seconds()
        if (
            self.previous_sequence
            and self.previous_sequence[0] == detection.code
            and 0 <= recent_result_age <= self.popup_result_grace
        ):
            self._clear_popup_candidate()
            dismissed = self._dismiss_result_popup(frame, assume_visible=True)
            self._save_runtime_state("RUNNING", self.previous_sequence)
            self.logger.info(
                "RESULT POPUP MATCHED RECENT HISTORY item=%s age=%.1fs",
                detection.code,
                recent_result_age,
            )
            return {
                "status": "popup_history_confirmed",
                "code": detection.code,
                "dismissed": dismissed,
            }

        same_candidate = (
            self.popup_candidate_code == detection.code
            and now - self.popup_candidate_seen_monotonic
            <= self.popup_result_candidate_timeout
        )
        if same_candidate:
            self.popup_candidate_count += 1
        else:
            self.popup_candidate_code = detection.code
            self.popup_candidate_count = 1
        self.popup_candidate_seen_monotonic = now

        if self.popup_candidate_count < self.popup_result_confirmations:
            self._save_runtime_state("RESULT_CANDIDATE")
            return {
                "status": "popup_candidate",
                "code": detection.code,
                "confidence": detection.confidence,
                "confirmations": self.popup_candidate_count,
            }

        if self.previous_sequence is None:
            # A first-ever startup has no eight-item baseline from which to
            # build the immediate sequence. Let the persistent row establish
            # that baseline after the popup closes.
            self._save_runtime_state("RESULT_POPUP")
            return {
                "status": "popup_waiting_for_baseline",
                "code": detection.code,
            }

        local_date = self._local_date()
        round_number = self._round_for_popup_result(frame, local_date)

        result_id, predicted_sequence = self._record_popup_result(
            detection,
            frame,
            round_number,
            local_date,
        )
        self.pending_popup_result_code = detection.code
        self.pending_popup_result_id = result_id
        self.pending_popup_round_number = round_number
        self.pending_popup_round_local_date = local_date
        self.pending_popup_result_monotonic = now
        self.history_verifier.reset()
        self.first_successful_scan = False
        self._clear_popup_candidate()

        # Publish the predicted one-position shift immediately. The persistent
        # history row is reconciled a few seconds later when the popup closes.
        self._save_runtime_state("RUNNING", predicted_sequence)
        dismissed = self._dismiss_result_popup(frame, assume_visible=True)
        return {
            "status": "recorded",
            "recordedIds": [result_id],
            "code": detection.code,
            "confidence": detection.confidence,
            "sequence": predicted_sequence,
            "dismissed": dismissed,
            "source": "result_popup",
        }

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
        # Scheduled Task launches can expose a stdout handle with no active
        # reader. Writing enough log output to that handle eventually blocks
        # the scanner loop, so only mirror logs to an interactive terminal.
        if sys.stdout is not None and sys.stdout.isatty():
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
        values = {
            "scanner_status": status,
            "scanner_heartbeat_utc": now,
            "scanner_source_serial": self.frame_source.serial,
            "last_result_at_utc": self.last_result_at.isoformat(
                timespec="milliseconds"
            ).replace("+00:00", "Z"),
            self._state_key("last_result_at_utc"): self.last_result_at.isoformat(
                timespec="milliseconds"
            ).replace("+00:00", "Z"),
        }
        if sequence is not None:
            serialized_sequence = json.dumps(sequence)
            values["last_sequence"] = serialized_sequence
            values[self._state_key("last_sequence")] = serialized_sequence
        if self.current_round is not None:
            values["scanner_current_round"] = str(self.current_round)
            values[self._state_key("current_round")] = str(self.current_round)
        if self.active_round is not None:
            values["scanner_active_round"] = str(self.active_round)
        if self.active_round_observed_at is not None:
            values["scanner_active_round_observed_at_utc"] = (
                self.active_round_observed_at.isoformat(timespec="milliseconds").replace(
                    "+00:00", "Z"
                )
            )
        if self.current_round_date:
            values[self._state_key("round_local_date")] = self.current_round_date
        self.database.set_states(values)

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
        observed_at = utc_now()
        maximum_plausible = self._maximum_plausible_round(observed_at)
        end_round = (
            observed_round
            if observed_round is not None
            and shift <= observed_round <= maximum_plausible
            else None
        )
        if observed_round is not None and end_round is None:
            self.logger.warning(
                "ROUND recovery anchor rejected as impossible: detected=%s maximum=%s",
                observed_round,
                maximum_plausible,
            )
        if (
            end_round is None
            and self.active_round is not None
            and self.active_round_date == local_date
            and self.active_round_observed_at is not None
            and (observed_at - self.active_round_observed_at).total_seconds()
            <= self.round_interval + 5
            and shift <= self.active_round <= maximum_plausible
        ):
            # The app label names the round whose result is currently being
            # revealed. Prefer that OCR value over inferred +1 arithmetic so
            # a previously missed detection cannot keep all later rounds off.
            end_round = self.active_round
        if end_round is None and self.current_round_date == local_date:
            inferred_end_round = (
                self.current_round + shift if self.current_round is not None else None
            )
            if (
                inferred_end_round is not None
                and shift <= inferred_end_round <= maximum_plausible
            ):
                end_round = inferred_end_round
            elif inferred_end_round is not None:
                self.logger.warning(
                    "ROUND recovery reference rejected as impossible: inferred=%s maximum=%s",
                    inferred_end_round,
                    maximum_plausible,
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

    def _prune_result_capture_directories(self, force: bool = False) -> None:
        now_monotonic = time.monotonic()
        if (
            not force
            and now_monotonic - self.last_capture_prune_monotonic < 3600
        ):
            return
        self.last_capture_prune_monotonic = now_monotonic
        cutoff = utc_now().date() - timedelta(days=self.capture_retention_days - 1)
        removed = 0
        try:
            entries = list(self.capture_dir.iterdir())
        except OSError as exc:
            self.logger.warning("Could not inspect result captures: %s", exc)
            return
        for path in entries:
            if not path.is_dir():
                continue
            try:
                capture_date = datetime.strptime(path.name, "%Y-%m-%d").date()
            except ValueError:
                continue
            if capture_date >= cutoff:
                continue
            try:
                shutil.rmtree(path)
                removed += 1
            except OSError as exc:
                self.logger.warning("Could not prune result capture directory %s: %s", path, exc)
        if removed:
            self.logger.info(
                "Pruned %d result capture directories older than %s",
                removed,
                cutoff.isoformat(),
            )

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

    def _publish_live_frame(self, frame: np.ndarray) -> None:
        if self.live_frame_path is None:
            return
        now = time.monotonic()
        if now - self.last_live_frame_write_monotonic < 0.4:
            return
        self.last_live_frame_write_monotonic = now
        try:
            ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
            if not ok:
                return
            temp_path = self.live_frame_path.with_suffix(".jpg.tmp")
            temp_path.write_bytes(encoded.tobytes())
            os.replace(temp_path, self.live_frame_path)
        except OSError as exc:
            self.logger.warning("Could not publish live frame: %s", exc)

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

    def _record_connection_failure(self, exc: Exception) -> dict:
        self.connection_failures += 1
        self._save_runtime_state("PHONE_RECONNECTING")
        self.logger.warning(
            "Phone reconnect attempt %s/%s failed: %s",
            self.connection_failures,
            self.reconnect_alert_after,
            exc,
        )
        if (
            self.connection_failures >= self.reconnect_alert_after
            and not self.connection_alert_sent
        ):
            self._notify_failure(
                "PHONE_CONNECTION_FAILED",
                f"Không thể kết nối lại điện thoại sau {self.connection_failures} lần thử",
                {
                    "serial": self.frame_source.serial,
                    "attempts": self.connection_failures,
                    "reconnectIntervalSeconds": self.frame_source.reconnect_interval,
                    "error": str(exc),
                },
            )
            self.connection_alert_sent = True
        return {"status": "phone_reconnecting", "error": str(exc)}

    def _record_connection_restored(self) -> None:
        if self.connection_failures <= 0:
            return
        attempts = self.connection_failures
        now = utc_now()
        self.database.insert_event(
            "INFO",
            "PHONE_RECONNECTED",
            "Đã kết nối lại điện thoại và tiếp tục quét",
            {"serial": self.frame_source.serial, "failedAttempts": attempts},
            source_key=(
                f"scanner:PHONE_RECONNECTED:"
                f"{now.strftime('%Y%m%d%H%M%S%f')}"
            ),
        )
        self.logger.info(
            "Phone reconnected after %s failed attempt(s)", attempts
        )
        self.connection_failures = 0
        self.connection_alert_sent = False

    def _private_memory_mb(self) -> Optional[float]:
        if os.name != "nt":
            return None

        class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_ulong),
                ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
                ("PrivateUsage", ctypes.c_size_t),
            ]

        counters = PROCESS_MEMORY_COUNTERS_EX()
        counters.cb = ctypes.sizeof(counters)
        handle = ctypes.windll.kernel32.GetCurrentProcess()
        ok = ctypes.windll.psapi.GetProcessMemoryInfo(
            handle,
            ctypes.byref(counters),
            counters.cb,
        )
        if not ok:
            return None
        return float(counters.PrivateUsage) / 1024 / 1024

    def _trim_process_memory(self) -> None:
        gc.collect()
        if os.name != "nt" or not self.memory_trim_enabled:
            return
        try:
            handle = ctypes.windll.kernel32.GetCurrentProcess()
            ctypes.windll.psapi.EmptyWorkingSet(handle)
        except Exception as exc:  # pragma: no cover - best-effort Windows trim
            self.logger.debug("Memory trim skipped: %s", exc)

    def _update_runtime_scanner_pid(self, pid: int) -> None:
        state_path = PROJECT_DIR.parent.parent / "runtime" / "processes.json"
        try:
            if not state_path.is_file():
                return
            state = json.loads(state_path.read_text(encoding="utf-8-sig"))
            state["scannerPid"] = pid
            state["scannerRestartedAt"] = utc_now().isoformat(timespec="milliseconds").replace("+00:00", "Z")
            state_path.write_text(
                json.dumps(state, ensure_ascii=False, indent=4),
                encoding="utf-8",
            )
        except (OSError, ValueError, TypeError) as exc:
            self.logger.warning("Could not update runtime scanner PID: %s", exc)

    def _restart_for_memory(self, private_mb: float) -> None:
        source_key = f"scanner:SCANNER_MEMORY_RESTART:{utc_now().strftime('%Y%m%d%H%M%S')}"
        event_id = self.database.insert_event(
            "WARNING",
            "SCANNER_MEMORY_RESTART",
            "Scanner tự khởi động lại vì RAM tăng quá ngưỡng",
            {
                "privateMemoryMb": round(private_mb, 1),
                "restartThresholdMb": self.memory_restart_mb,
                "argv": sys.argv,
            },
            source_key=source_key,
        )
        self._send_backend_event(
            event_id,
            {
                "severity": "WARNING",
                "eventCode": "SCANNER_MEMORY_RESTART",
                "message": "Scanner tự khởi động lại vì RAM tăng quá ngưỡng",
                "details": {
                    "privateMemoryMb": round(private_mb, 1),
                    "restartThresholdMb": self.memory_restart_mb,
                },
                "sourceKey": source_key,
                "occurredAtUtc": utc_now().isoformat().replace("+00:00", "Z"),
            },
        )
        command = [sys.executable, *sys.argv]
        process = subprocess.Popen(
            command,
            cwd=str(PROJECT_DIR),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            close_fds=True,
        )
        self._update_runtime_scanner_pid(process.pid)
        self.logger.warning(
            "Scanner memory %.1f MB exceeded %.1f MB; spawned replacement PID %s",
            private_mb,
            self.memory_restart_mb,
            process.pid,
        )
        self.stop_requested = True

    def _check_memory_watchdog(self) -> None:
        if not self.memory_watchdog_enabled:
            return
        now = time.monotonic()
        if now - self.last_memory_check_monotonic < self.memory_check_interval:
            return
        self.last_memory_check_monotonic = now
        self._trim_process_memory()
        private_mb = self._private_memory_mb()
        if private_mb is None:
            return
        if private_mb >= self.memory_restart_mb:
            self._restart_for_memory(private_mb)
            return
        if private_mb >= self.memory_warning_mb and not self.memory_warning_sent:
            self.memory_warning_sent = True
            self._notify_failure(
                "SCANNER_MEMORY_HIGH",
                "Scanner đang dùng RAM cao",
                {
                    "privateMemoryMb": round(private_mb, 1),
                    "warningThresholdMb": self.memory_warning_mb,
                    "restartThresholdMb": self.memory_restart_mb,
                },
            )
        elif private_mb < self.memory_warning_mb * 0.75:
            self.memory_warning_sent = False

    def _save_result_crop(
        self,
        frame: np.ndarray,
        result_id: int,
        crop: Optional[np.ndarray] = None,
    ) -> Optional[str]:
        if not self.save_result_crops:
            return None
        self._prune_result_capture_directories()
        day_dir = self.capture_dir / utc_now().strftime("%Y-%m-%d")
        day_dir.mkdir(parents=True, exist_ok=True)
        path = day_dir / f"result_{result_id:08d}.png"
        evidence = crop if crop is not None else self.detector.crop_history(frame)
        cv2.imwrite(str(path), evidence)
        self.database.update_result_capture(result_id, str(path))
        return str(path)

    def _persist_result(
        self,
        code: str,
        confidence: float,
        sequence: list[str],
        frame: np.ndarray,
        reason: str,
        round_number: Optional[int],
        round_local_date: str,
        evidence_crop: Optional[np.ndarray] = None,
    ) -> int:
        item = ITEMS[code]
        detected_at = utc_now()
        result_id = self.database.insert_result(
            item_code=code,
            item_name=item["name"],
            category=item["category"],
            confidence=confidence,
            sequence=sequence,
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
        revision = utc_now().isoformat(timespec="milliseconds").replace("+00:00", "Z")
        serialized_sequence = json.dumps(sequence)
        self.database.set_states(
            {
                "last_result_id": str(result_id),
                "last_result_revision": revision,
                "last_sequence": serialized_sequence,
                self._state_key("last_sequence"): serialized_sequence,
            }
        )
        self._save_result_crop(frame, result_id, evidence_crop)
        self.logger.info(
            "NEW RESULT id=%s round=%s item=%s category=%s confidence=%.3f reason=%s",
            result_id,
            round_number,
            item["name"],
            item["category"],
            confidence,
            reason,
        )
        return result_id

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
        return self._persist_result(
            code=code,
            confidence=slot.confidence,
            sequence=detection.sequence,
            frame=frame,
            reason=reason,
            round_number=round_number,
            round_local_date=round_local_date,
        )

    def _record_popup_result(
        self,
        detection: PopupResultDetection,
        frame: np.ndarray,
        round_number: Optional[int],
        round_local_date: str,
    ) -> tuple[int, list[str]]:
        previous = self.previous_sequence or []
        predicted_sequence = [detection.code, *previous[:7]]
        result_id = self._persist_result(
            code=detection.code,
            confidence=detection.confidence,
            sequence=predicted_sequence,
            frame=frame,
            reason="result_popup_pending_history",
            round_number=round_number,
            round_local_date=round_local_date,
            evidence_crop=detection.crop,
        )
        return result_id, predicted_sequence

    def _reconcile_popup_result(
        self,
        detection: HistoryDetection,
        frame: np.ndarray,
    ) -> tuple[bool, str, str]:
        if self.pending_popup_result_id is None or self.pending_popup_result_code is None:
            raise RuntimeError("No provisional popup result is waiting for verification")

        popup_code = self.pending_popup_result_code
        actual_slot = detection.slots[0]
        actual_code = actual_slot.code
        corrected = actual_code != popup_code
        reason = (
            "result_popup_corrected_by_history"
            if corrected
            else "result_popup_verified_history"
        )
        item = ITEMS[actual_code]
        self.database.reconcile_result(
            result_id=self.pending_popup_result_id,
            item_code=actual_code,
            item_name=item["name"],
            category=item["category"],
            confidence=actual_slot.confidence,
            sequence=detection.sequence,
            detection_reason=reason,
        )
        synchronized = []
        if (
            self.pending_popup_round_number is not None
            and self.pending_popup_round_local_date is not None
        ):
            outcomes = []
            for slot in detection.slots[:8]:
                slot_item = ITEMS[slot.code]
                outcomes.append(
                    (
                        slot.code,
                        slot_item["name"],
                        slot_item["category"],
                        slot.confidence,
                    )
                )
            synchronized = self.database.synchronize_verified_sequence(
                source_serial=self.frame_source.serial,
                round_local_date=self.pending_popup_round_local_date,
                latest_round=self.pending_popup_round_number,
                outcomes=outcomes,
                sequence=detection.sequence,
                anchor_result_id=self.pending_popup_result_id,
                round_interval_seconds=self.round_interval,
            )
        self.database.set_state("last_result_id", str(self.pending_popup_result_id))
        self._touch_result_revision()

        if synchronized:
            inserted = sum(entry["action"] == "inserted" for entry in synchronized)
            repaired = sum(entry["action"] == "corrected" for entry in synchronized)
            retimed = sum(entry["action"] == "retimed" for entry in synchronized)
            self.logger.info(
                "VERIFIED HISTORY SYNC latest_round=%s inserted=%s corrected=%s "
                "retimed=%s rounds=%s",
                self.pending_popup_round_number,
                inserted,
                repaired,
                retimed,
                [entry["round"] for entry in synchronized],
            )

        if self.save_result_crops:
            self._prune_result_capture_directories()
            day_dir = self.capture_dir / utc_now().strftime("%Y-%m-%d")
            day_dir.mkdir(parents=True, exist_ok=True)
            verification_path = (
                day_dir / f"verification_{self.pending_popup_result_id:08d}.png"
            )
            cv2.imwrite(str(verification_path), self.detector.crop_history(frame))

        return corrected, popup_code, actual_code

    def _scan_once_legacy(self) -> dict:
        frame: Optional[np.ndarray] = None
        try:
            frame = self.frame_source.capture()
            self._record_connection_restored()
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
                    self.no_result_attempts >= self.no_result_alert_after_attempts
                    and elapsed
                    >= self.round_interval
                    + self.no_result_alert_after_attempts * self.scan_interval
                    and time.monotonic()
                    - max(
                        self.last_popup_seen_monotonic,
                        self.last_popup_dismiss_monotonic,
                    )
                    >= self.popup_result_grace
                ):
                    self._notify_failure(
                        "RESULT_TIMEOUT",
                        f"Quá {self.no_result_alert_after_attempts} lần quét sau thời điểm dự kiến nhưng chưa thấy kèo mới",
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

        published_sequence = self.previous_sequence
        if self.pending_popup_result_code is not None:
            published_sequence = [
                self.pending_popup_result_code,
                *(self.previous_sequence or [])[:7],
            ]
        self._save_runtime_state("RUNNING", published_sequence)
        return {
            "status": "recorded" if recorded else "waiting",
            "recordedIds": recorded,
            "sequence": sequence,
            "minimumConfidence": detection.minimum_confidence,
            "newMarkerScore": detection.new_marker_score,
        }

    def scan_once(self) -> dict:
        self._collect_countdown_detection()
        self._collect_round_detection()
        self._collect_financial_detection()
        frame: Optional[np.ndarray] = None
        try:
            frame = self.frame_source.capture()
            self._record_connection_restored()
            self._publish_live_frame(frame)
            # OCR runs on its own worker so reading the on-screen 30-second
            # timer never delays history matching or result persistence.
            self._schedule_countdown_detection(frame)
            self._schedule_round_detection(frame)
            self._schedule_financial_detection(frame)
            # The popup exposes the winning icon about five seconds before the
            # persistent history strip shifts. Detect and publish it directly.
            if is_result_popup(frame, self.popup_dismiss_config):
                self.capture_failures = 0
                self.no_result_attempts = 0
                return self._handle_result_popup(frame)
            self._update_betting_signals(frame)
            detection = self.detector.detect(frame)
            self.capture_failures = 0
        except ReconnectPendingError as exc:
            self._save_runtime_state("PHONE_RECONNECT_WAIT")
            return {"status": "phone_reconnect_wait", "error": str(exc)}
        except DeviceConnectionError as exc:
            return self._record_connection_failure(exc)
        except CaptureBlockedError as exc:
            self._save_runtime_state("CAPTURE_BLOCKED")
            self.logger.warning("%s", exc)
            return {"status": "capture_blocked", "error": str(exc)}
        except (RuntimeError, DetectionError, OSError) as exc:
            if frame is not None and is_result_popup(
                frame, self.popup_dismiss_config
            ):
                self.capture_failures = 0
                self.no_result_attempts = 0
                return self._handle_result_popup(frame)
            if self.pending_popup_result_code is not None:
                self.history_verifier.reset()
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
                self._publish_active_round(observed_round, utc_now())
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
                if any(round_number is None for round_number in round_numbers):
                    self.logger.warning(
                        "Startup sequence shift ignored without a plausible round anchor: "
                        "shift=%s observed_round=%s",
                        startup_shift,
                        observed_round,
                    )
                    startup_shift = 0
                else:
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

        history_is_stable = False
        if self.pending_popup_result_code is not None:
            history_is_stable = self.history_verifier.observe(sequence)

        if (
            self.pending_popup_result_code is not None
            and not shift
            and sequence != self.previous_sequence
        ):
            self._save_runtime_state(
                "VERIFICATION_CONFLICT" if history_is_stable else "VERIFYING_RESULT",
                [self.pending_popup_result_code, *(self.previous_sequence or [])[:7]],
            )
            if history_is_stable:
                self._notify_failure(
                    "RESULT_VERIFICATION_CONFLICT",
                    "Stable history could not be aligned with the pre-result eight-slot baseline",
                    {
                        "resultId": self.pending_popup_result_id,
                        "popupCode": self.pending_popup_result_code,
                        "previous": self.previous_sequence,
                        "observed": sequence,
                        "historyConfirmations": self.history_verifier.confirmations,
                    },
                    self.detector.annotate(frame, detection),
                )
                self.previous_sequence = sequence
                self.last_result_at = utc_now()
                self.no_result_attempts = 0
                self._clear_pending_popup_result()
                self._save_runtime_state("RUNNING", sequence)
            return {
                "status": "verification_conflict",
                "confirmations": self.history_verifier.confirmations,
                "sequence": sequence,
            }

        if shift:
            new_codes = list(reversed(sequence[:shift]))
            if self.pending_popup_result_code is not None:
                if not history_is_stable:
                    published_sequence = [
                        self.pending_popup_result_code,
                        *(self.previous_sequence or [])[:7],
                    ]
                    self._save_runtime_state("VERIFYING_RESULT", published_sequence)
                    return {
                        "status": "verifying_popup_result",
                        "confirmations": self.history_verifier.confirmations,
                        "requiredConfirmations": (
                            self.history_verifier.required_confirmations
                        ),
                        "sequence": sequence,
                    }

                pending_id = self.pending_popup_result_id
                corrected, popup_code, actual_code = self._reconcile_popup_result(
                    detection, frame
                )
                self.previous_sequence = sequence
                self.no_result_attempts = 0
                self._clear_pending_popup_result()
                self._save_runtime_state("RUNNING", sequence)

                if corrected:
                    self._notify_failure(
                        "POPUP_RESULT_MISMATCH",
                        "Popup result was corrected after three stable history scans",
                        {
                            "resultId": pending_id,
                            "popupCode": popup_code,
                            "correctedCode": actual_code,
                            "shift": shift,
                            "historyConfirmations": (
                                self.history_verifier.required_confirmations
                            ),
                        },
                        self.detector.annotate(frame, detection),
                    )
                self.logger.info(
                    "POPUP RESULT %s id=%s popup=%s history=%s "
                    "history_shift=%s confirmations=%s",
                    "CORRECTED" if corrected else "VERIFIED",
                    pending_id,
                    popup_code,
                    actual_code,
                    shift,
                    self.history_verifier.required_confirmations,
                )
                return {
                    "status": (
                        "popup_corrected" if corrected else "popup_verified"
                    ),
                    "resultId": pending_id,
                    "popupCode": popup_code,
                    "actualCode": actual_code,
                    "sequence": sequence,
                }

            if new_codes and self.current_round is None and observed_round is None:
                observed_round = self._read_round(frame)
            round_numbers = self._rounds_for_shift(
                len(new_codes), local_date, observed_round
            )
            for code, round_number in zip(new_codes, round_numbers):
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
            if (
                self.pending_popup_result_code is not None
                and time.monotonic() - self.pending_popup_result_monotonic
                >= self.popup_result_grace
            ):
                if (
                    history_is_stable
                    and len(set(sequence)) == 1
                    and sequence[0] == self.pending_popup_result_code
                ):
                    pending_id = self.pending_popup_result_id
                    _, popup_code, actual_code = self._reconcile_popup_result(
                        detection, frame
                    )
                    self._clear_pending_popup_result()
                    self.logger.info(
                        "POPUP RESULT VERIFIED BY REPEATED HISTORY "
                        "id=%s item=%s confirmations=%s",
                        pending_id,
                        actual_code,
                        self.history_verifier.required_confirmations,
                    )
                else:
                    self._notify_failure(
                        "RESULT_VERIFICATION_TIMEOUT",
                        "Popup result is still waiting for a stable eight-slot history shift",
                        {
                            "resultId": self.pending_popup_result_id,
                            "popupCode": self.pending_popup_result_code,
                            "historyConfirmations": self.history_verifier.confirmations,
                            "sequence": sequence,
                        },
                        self.detector.annotate(frame, detection),
                    )
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
                    self.no_result_attempts >= self.no_result_alert_after_attempts
                    and elapsed
                    >= self.round_interval
                    + self.no_result_alert_after_attempts * self.scan_interval
                    and time.monotonic()
                    - max(
                        self.last_popup_seen_monotonic,
                        self.last_popup_dismiss_monotonic,
                    )
                    >= self.popup_result_grace
                ):
                    self._notify_failure(
                        "RESULT_TIMEOUT",
                        f"Quá {self.no_result_alert_after_attempts} lần quét sau thời điểm dự kiến nhưng chưa thấy kèo mới",
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

        published_sequence = self.previous_sequence
        if self.pending_popup_result_code is not None:
            published_sequence = [
                self.pending_popup_result_code,
                *(self.previous_sequence or [])[:7],
            ]
        self._save_runtime_state("RUNNING", published_sequence)
        return {
            "status": "recorded" if recorded else "waiting",
            "recordedIds": recorded,
            "round": self.current_round,
            "sequence": sequence,
            "minimumConfidence": detection.minimum_confidence,
            "newMarkerScore": detection.new_marker_score,
        }

    def run(self, once: bool = False) -> int:
        while not self.stop_requested:
            try:
                self.frame_source.connect()
                self._record_connection_restored()
                break
            except DeviceConnectionError as exc:
                self._record_connection_failure(exc)
                if once:
                    raise
                time.sleep(self.frame_source.reconnect_interval)
        self.popup_input_serial = self.config.get("popup_dismiss", {}).get("input_serial") or self.frame_source.serial
        self.previous_sequence = self._load_sequence()
        self.last_result_at = self._load_last_result_time()
        self.current_round = self._load_round()
        self.current_round_date = self.database.get_state(self._state_key("round_local_date"))
        self.database.insert_event(
            "INFO",
            "SCANNER_STARTED",
            "Scanner đã kết nối thiết bị và bắt đầu quét",
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
            self._check_memory_watchdog()
            if self.stop_requested:
                break
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
            latest_popup_activity = max(
                self.last_popup_seen_monotonic,
                self.last_popup_dismiss_monotonic,
            )
            if (
                latest_popup_activity > 0
                and time.monotonic() - latest_popup_activity
                < self.post_popup_fast_window
            ):
                interval = self.post_popup_scan_interval
            remaining = interval - (time.monotonic() - started)
            if remaining > 0:
                time.sleep(remaining)
        self._collect_countdown_detection()
        self._collect_round_detection()
        self._collect_financial_detection()
        self._save_runtime_state("STOPPED")
        return 0

    def close(self) -> None:
        self.countdown_executor.shutdown(wait=True, cancel_futures=True)
        self.round_executor.shutdown(wait=True, cancel_futures=True)
        self.financial_executor.shutdown(wait=True, cancel_futures=True)
        self.frame_source.close()
        for handler in self.logger.handlers[:]:
            self.logger.removeHandler(handler)
            handler.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Greedy BIGO background result scanner")
    parser.add_argument(
        "--config",
        default=str(PROJECT_DIR / "config.json"),
        help="Path to scanner config.json",
    )
    parser.add_argument("--once", action="store_true", help="Capture/detect once then exit")
    parser.add_argument("--device", help="Pin an ADB serial from --list-devices")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--list-devices", action="store_true", help="List ADB devices without starting the scanner")
    actions.add_argument("--check", action="store_true", help="Capture and diagnose without database writes or taps")
    actions.add_argument("--calibrate", action="store_true", help="Select history/OCR regions on a screenshot and save a profile")
    actions.add_argument("--calibrate-popup", action="store_true", help="Select popup regions on a result screenshot")
    parser.add_argument("--image", type=Path, help="Use a saved native screenshot for --check or --calibrate")
    parser.add_argument("--output", type=Path, default=PROJECT_DIR / "../../runtime/scanner-check", help="Diagnostic directory, or new JSON profile with --calibrate")
    args = parser.parse_args()

    try:
        config_path = Path(args.config).resolve()
        config = load_config(config_path, args.device)
        if args.list_devices:
            adb_path = resolve_adb_path(config.get("source", config.get("emulator", {})).get("adb_path", ""))
            print(json.dumps([d.to_dict() for d in list_devices(adb_path)], ensure_ascii=False, indent=2))
            return 0
        if args.check or args.calibrate or args.calibrate_popup:
            from diagnostics import check_capture, calibrate
            if args.calibrate or args.calibrate_popup:
                return calibrate(config, config_path, args.output.resolve(), args.image, popup_only=args.calibrate_popup)
            return check_capture(config, config_path, args.output.resolve(), args.image)
        if args.image:
            parser.error("--image requires --check or --calibrate")
        scanner = GreedyScanner(config_path, args.device)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Scanner setup failed: {exc}", file=sys.stderr)
        return 1

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
    finally:
        scanner.close()


if __name__ == "__main__":
    raise SystemExit(main())
