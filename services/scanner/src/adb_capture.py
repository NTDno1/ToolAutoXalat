"""Direct USB / wireless ADB capture. No dependency on emulator windows."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

import cv2
import numpy as np

from screen_geometry import FrameNormalizer


class CaptureBlockedError(RuntimeError):
    """The foreground app explicitly prohibits screen capture."""


class DeviceConnectionError(RuntimeError):
    """A real connection attempt could not reach the selected Android device."""


class ReconnectPendingError(RuntimeError):
    """The source is waiting for its configured reconnect interval."""


def is_adb_connection_error(message: str) -> bool:
    lowered = message.lower()
    return any(fragment in lowered for fragment in (
        "device offline",
        "device not found",
        "is not connected",
        "no devices/emulators found",
        "cannot connect",
        "closed",
        "transport error",
    ))


def secure_focused_window(dump: str) -> str | None:
    focus = re.search(r"mCurrentFocus=Window\{(\S+)\s+[^}]+\}", dump)
    if not focus:
        return None
    for block in re.split(r"(?=^[ \t]*Window #\d+ Window\{)", dump, flags=re.MULTILINE):
        if not re.match(r"\s*Window #\d+ Window\{" + re.escape(focus[1]) + r"\s", block):
            continue
        flags = re.search(r"\bfl=([^\n]+)", block)
        if flags and re.search(r"\bSECURE\b", flags[1]):
            return focus[0].split("=", 1)[1]
    return None


def resolve_adb_path(configured: str = "") -> str:
    if configured:
        path = shutil.which(configured)
        if path:
            return path
        raise RuntimeError(f"ADB not found: {configured}. Install Android Platform Tools or set source.adb_path")
    candidates = [shutil.which("adb")]
    for name in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        if os.environ.get(name):
            candidates.append(str(Path(os.environ[name]) / "platform-tools" / "adb.exe"))
    if os.environ.get("LOCALAPPDATA"):
        candidates.append(str(Path(os.environ["LOCALAPPDATA"]) / "Android/Sdk/platform-tools/adb.exe"))
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    raise RuntimeError("ADB not found. Install Android Platform Tools and add adb to PATH")


def run_adb(adb_path: str, args: list[str], timeout: float = 10) -> subprocess.CompletedProcess:
    try:
        return subprocess.run([adb_path, *args], capture_output=True, timeout=timeout,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"ADB {args[0]} failed: {exc}") from exc


def adb_error(result: subprocess.CompletedProcess) -> str:
    return (result.stderr + result.stdout).decode("utf-8", errors="replace").strip()


@dataclass(frozen=True)
class AdbDevice:
    serial: str
    status: str
    model: str = ""
    kind: str = "phone"

    def to_dict(self) -> dict:
        return asdict(self)


def list_devices(adb_path: str) -> list[AdbDevice]:
    result = run_adb(adb_path, ["devices", "-l"])
    if result.returncode:
        raise RuntimeError(adb_error(result))
    devices = []
    for line in result.stdout.decode("utf-8", errors="replace").splitlines():
        parts = line.split()
        if len(parts) < 2 or parts[1] not in {"device", "offline", "unauthorized", "recovery", "sideload", "no"}:
            continue
        metadata = dict(p.split(":", 1) for p in parts[2:] if ":" in p)
        serial = parts[0]
        emulator = serial.startswith(("emulator-", "127.", "localhost:", "[::1]:"))
        # Also distinguish remote emulators from wireless physical devices.
        if parts[1] == "device" and not emulator:
            probe = run_adb(adb_path, ["-s", serial, "shell", "getprop", "ro.kernel.qemu"], timeout=3)
            if probe.returncode:
                raise RuntimeError(f"Cannot identify device {serial}: {adb_error(probe)}")
            emulator = probe.stdout.strip() == b"1"
        devices.append(AdbDevice(serial, parts[1], metadata.get("model", ""), "emulator" if emulator else "phone"))
    return devices


def select_device(devices: list[AdbDevice], serial: str = "", kind: str = "phone") -> AdbDevice:
    if kind not in {"phone", "emulator", "any"}:
        raise ValueError("source.device_kind must be phone, emulator or any")
    if serial:
        matches = [d for d in devices if d.serial == serial]
        if not matches:
            raise RuntimeError(f"Device {serial} is not connected; run --list-devices")
        device = matches[0]
        if device.status != "device":
            raise RuntimeError(f"Device {serial} is {device.status}; unlock it and accept USB debugging, or reconnect the cable")
        if kind != "any" and device.kind != kind:
            raise RuntimeError(f"Device {serial} is {device.kind}, but source.device_kind={kind}")
        return device
    candidates = [d for d in devices if d.status == "device" and (kind == "any" or d.kind == kind)]
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        raise RuntimeError("Multiple matching devices; choose --device SERIAL: " + ", ".join(d.serial for d in candidates))
    found = ", ".join(f"{d.serial} ({d.status}, {d.kind})" for d in devices) or "none"
    raise RuntimeError(f"No ready {kind} device. Enable USB debugging and accept the phone prompt. Found: {found}")


class AdbFrameSource:
    def __init__(self, config: dict, screen: dict | None = None):
        self.adb_path = resolve_adb_path(config.get("adb_path", ""))
        self.serial = str(config.get("serial", "")).strip()
        self.kind = config.get("device_kind", "phone")
        self.endpoint = str(config.get("connect_address", "")).strip()
        if self.endpoint and self.serial and self.endpoint != self.serial:
            raise ValueError("source.serial and source.connect_address must refer to the same endpoint")
        self.timeout = max(.1, float(config.get("capture_timeout_seconds", 8)))
        self.reconnect_interval = max(1, float(config.get("reconnect_interval_seconds", 3)))
        self.normalizer = FrameNormalizer(screen)
        self.output_size = (self.normalizer.width, 1500)
        self.last_raw_frame = None
        self.connected = False
        self.last_connect_attempt = -float("inf")
        self.blocked_until = 0.0
        self.blocked_message = ""

    def connect(self) -> None:
        self.last_connect_attempt = time.monotonic()
        self.connected = False
        try:
            if self.endpoint:
                result = run_adb(self.adb_path, ["connect", self.endpoint])
                if result.returncode:
                    raise RuntimeError(adb_error(result))
            device = select_device(list_devices(self.adb_path), self.serial or self.endpoint, self.kind)
        except RuntimeError as exc:
            raise DeviceConnectionError(str(exc)) from exc
        # Pin this serial; reconnect must never switch to a different phone.
        self.serial = device.serial
        self.connected = True

    def capture(self) -> np.ndarray:
        self.normalizer.bounds = None
        if time.monotonic() < self.blocked_until:
            raise CaptureBlockedError(self.blocked_message)
        if not self.connected:
            if time.monotonic() - self.last_connect_attempt < self.reconnect_interval:
                raise ReconnectPendingError(f"Waiting to reconnect device {self.serial}")
            self.connect()
        try:
            result = run_adb(self.adb_path, ["-s", self.serial, "exec-out", "screencap", "-p"], self.timeout)
            if result.returncode or not result.stdout.startswith(b"\x89PNG\r\n\x1a\n"):
                raise RuntimeError(f"ADB screenshot failed for {self.serial}: {result.stderr.decode(errors='replace') or 'empty/invalid PNG'}")
            frame = cv2.imdecode(np.frombuffer(result.stdout, dtype=np.uint8), cv2.IMREAD_COLOR)
            if frame is None or frame.size == 0 or float(frame.std()) < 1:
                raise RuntimeError("ADB screenshot is blank or cannot be decoded; keep the game visible")
        except RuntimeError as exc:
            # Diagnose the foreground window only on capture failure, so normal
            # scans do not pay for a large dumpsys call on every frame.
            try:
                probe = run_adb(self.adb_path, ["-s", self.serial, "shell", "dumpsys", "window"], timeout=5)
                secure_window = secure_focused_window(probe.stdout.decode(errors="replace"))
            except RuntimeError:
                secure_window = None
            if secure_window:
                self.blocked_message = (
                    f"CAPTURE_BLOCKED: {secure_window} uses FLAG_SECURE. "
                    "Android blocks screenshots of this app; resolution calibration cannot fix this. "
                    "Use an app screen that permits capture."
                )
                self.blocked_until = time.monotonic() + 5
                raise CaptureBlockedError(self.blocked_message) from exc
            self.connected = False
            if is_adb_connection_error(str(exc)):
                raise DeviceConnectionError(str(exc)) from exc
            raise
        self.last_raw_frame = frame
        output = self.normalizer.normalize(frame)
        self.output_size = (output.shape[1], output.shape[0])
        return output

    def map_input_point(self, x: int, y: int) -> tuple[int, int]:
        return self.normalizer.to_native(x, y)

    def close(self) -> None:
        self.connected = False
        self.normalizer.bounds = None
