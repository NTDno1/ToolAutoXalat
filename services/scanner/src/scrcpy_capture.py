"""Continuous scrcpy video capture over ADB, decoded to fresh frames in memory.

Uses the upstream standalone-server raw_stream interface. On the tested Android
10 Redmi this captures Greedy even though Android's screencap command refuses it.
No root, APK replacement, audio capture, desktop window or video recording.
"""
from __future__ import annotations

from collections import deque
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import threading
import time

import cv2
import numpy as np

from adb_capture import AdbFrameSource, adb_error, run_adb


def resolve_program(configured: str, name: str) -> str:
    path = shutil.which(configured or name)
    if not path:
        raise RuntimeError(f"{name} not found. Install {name} and add it to PATH, or set source.{name}_path")
    return path


def parse_display_size(output: bytes) -> tuple[int, int]:
    sizes = re.findall(rb"(?:Physical|Override) size:\s*(\d+)x(\d+)", output)
    if not sizes:
        raise RuntimeError("Cannot read Android display size for scrcpy input mapping")
    width, height = map(int, sizes[-1])
    if min(width, height) <= 0:
        raise RuntimeError("Invalid Android display size")
    return width, height


def find_available_tcp_port() -> int:
    """Ask Windows for a free loopback port for older ADB clients."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


class JpegStreamParser:
    """Split FFmpeg image2pipe output; bound incomplete/corrupt-frame memory."""
    def __init__(self, limit: int = 16 * 1024 * 1024):
        self.buffer = bytearray()
        self.limit = limit

    def feed(self, chunk: bytes) -> list[bytes]:
        self.buffer.extend(chunk)
        images = []
        while True:
            start = self.buffer.find(b"\xff\xd8")
            if start < 0:
                self.buffer[:] = self.buffer[-1:]
                break
            if start:
                del self.buffer[:start]
            end = self.buffer.find(b"\xff\xd9", 2)
            if end < 0:
                if len(self.buffer) > self.limit:
                    raise RuntimeError("FFmpeg JPEG frame exceeded the buffer limit")
                break
            images.append(bytes(self.buffer[:end+2]))
            del self.buffer[:end+2]
        return images


class ScrcpyFrameSource(AdbFrameSource):
    def __init__(self, config: dict, screen: dict | None = None):
        super().__init__(config, screen)
        self.scrcpy_path = resolve_program(config.get("scrcpy_path", ""), "scrcpy")
        self.ffmpeg_path = resolve_program(config.get("ffmpeg_path", ""), "ffmpeg")
        default_server = os.environ.get("SCRCPY_SERVER_PATH") or str(Path(self.scrcpy_path).parent / "scrcpy-server")
        self.server_path = Path(config.get("scrcpy_server_path") or default_server)
        if not self.server_path.is_file():
            raise RuntimeError(f"scrcpy-server not found: {self.server_path}; set source.scrcpy_server_path")
        self.server_version = config.get("scrcpy_server_version", "")
        self.max_size = max(320, int(config.get("video_max_size", 1600)))
        self.max_fps = max(1, min(60, int(config.get("video_max_fps", 10))))
        self.bit_rate = max(100000, int(config.get("video_bit_rate", 8000000)))
        self.startup_timeout = max(3, float(config.get("video_startup_timeout_seconds", 15)))
        self.max_frame_age = max(.1, float(config.get("video_max_frame_age_seconds", 2)))
        self.native_size = None
        self._server_process = None
        self._decoder = None
        self._socket = None
        self._forward_port = None
        self._remote_path = None
        self._threads = []
        self._stopping = threading.Event()
        self._condition = threading.Condition()
        self._latest = None
        self._latest_at = 0.0
        self._sequence = 0
        self._delivered = 0
        self._stream_error = ""
        self._logs = deque(maxlen=20)

    def _checked_adb(self, args, timeout=10):
        result = run_adb(self.adb_path, ["-s", self.serial, *args], timeout)
        if result.returncode:
            raise RuntimeError(adb_error(result) or f"ADB {args[0]} failed")
        return result

    def _spawn_thread(self, target, *args):
        thread = threading.Thread(target=target, args=args, daemon=True)
        self._threads.append(thread)
        thread.start()

    def _server_command(self, version: str, scid: str) -> list[str]:
        return [self.adb_path, "-s", self.serial, "shell",
                f"CLASSPATH={self._remote_path}", "app_process", "/",
                "com.genymobile.scrcpy.Server", version, f"scid={scid}",
                "tunnel_forward=true", "audio=false", "control=false", "cleanup=true",
                "raw_stream=true", "video_codec=h264", f"max_size={self.max_size}",
                f"max_fps={self.max_fps}", f"video_bit_rate={self.bit_rate}"]

    def _create_forward(self, scid: str) -> int:
        remote = f"localabstract:scrcpy_{scid}"
        result = self._checked_adb(["forward", "tcp:0", remote])
        assigned = result.stdout.strip()
        if assigned:
            port = int(assigned)
            if port > 0:
                return port

        # ADB 1.0.36 accepts tcp:0 but creates a literal, unusable port 0 and
        # prints no assigned port. Remove it and use an explicit free port.
        self._checked_adb(["forward", "--remove", "tcp:0"])
        port = find_available_tcp_port()
        self._checked_adb(["forward", f"tcp:{port}", remote])
        return port

    def _collect_logs(self, pipe):
        try:
            for line in iter(pipe.readline, b""):
                self._logs.append(line.decode("utf-8", errors="replace").strip())
        except (OSError, ValueError):
            pass

    def _error(self, message):
        with self._condition:
            if not self._stopping.is_set():
                self._stream_error = message
            self._condition.notify_all()

    def _feed_decoder(self, first_chunk):
        try:
            chunk = first_chunk
            while chunk and not self._stopping.is_set():
                self._decoder.stdin.write(chunk)
                self._decoder.stdin.flush()
                chunk = self._socket.recv(65536)
            if not self._stopping.is_set():
                self._error("scrcpy video connection closed")
        except (OSError, ValueError) as exc:
            self._error(f"scrcpy video transport failed: {exc}")
        finally:
            try:
                self._decoder.stdin.close()
            except (OSError, ValueError):
                pass

    def _read_frames(self):
        parser = JpegStreamParser()
        try:
            while not self._stopping.is_set():
                chunk = self._decoder.stdout.read1(65536)
                if not chunk:
                    break
                for encoded in parser.feed(chunk):
                    frame = cv2.imdecode(np.frombuffer(encoded, np.uint8), cv2.IMREAD_COLOR)
                    if frame is None:
                        continue
                    with self._condition:
                        self._latest = frame
                        self._latest_at = time.monotonic()
                        self._sequence += 1
                        self._condition.notify_all()
            if not self._stopping.is_set():
                self._error("FFmpeg video decoder stopped")
        except (OSError, ValueError, RuntimeError) as exc:
            self._error(f"FFmpeg video decode failed: {exc}")

    def connect(self) -> None:
        self.close()
        super().connect()  # Select/pin the same device as the screenshot source.
        self.connected = False
        self._stopping.clear()
        self._logs.clear()
        self._stream_error = ""
        self._sequence = self._delivered = 0
        self._latest = None
        self._latest_at = 0
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            version = self.server_version
            if not version:
                probe = subprocess.run([self.scrcpy_path, "--version"], capture_output=True,
                                       timeout=5, creationflags=flags)
                match = re.search(rb"scrcpy (\d+\.\d+(?:\.\d+)?)", probe.stdout)
                if probe.returncode or not match:
                    raise RuntimeError("Cannot determine scrcpy version; use its matching scrcpy-server")
                version = match[1].decode("ascii")
            if not re.fullmatch(r"\d+\.\d+(?:\.\d+)?", version):
                raise ValueError("Invalid source.scrcpy_server_version")
            self.native_size = parse_display_size(self._checked_adb(["shell", "wm", "size"]).stdout)
            scid = f"{secrets.randbelow(0x7fffffff):08x}"
            self._remote_path = f"/data/local/tmp/toolautoxalat-scrcpy-{scid}.jar"
            self._checked_adb(["push", str(self.server_path), self._remote_path])
            self._forward_port = self._create_forward(scid)
            command = self._server_command(version, scid)
            self._server_process = subprocess.Popen(command, stdout=subprocess.PIPE,
                                                     stderr=subprocess.STDOUT, creationflags=flags)
            self._spawn_thread(self._collect_logs, self._server_process.stdout)
            deadline = time.monotonic() + self.startup_timeout
            first_chunk = b""
            while time.monotonic() < deadline:
                if self._server_process.poll() is not None:
                    raise RuntimeError("scrcpy server stopped: " + " | ".join(self._logs))
                connection = None
                try:
                    connection = socket.create_connection(("127.0.0.1", self._forward_port), timeout=1)
                    connection.settimeout(2)
                    first_chunk = connection.recv(65536)
                    if first_chunk:
                        connection.settimeout(max(5, self.timeout))
                        self._socket = connection
                        break
                except OSError:
                    pass
                if connection:
                    connection.close()
                time.sleep(.1)
            if self._socket is None:
                raise RuntimeError("Timed out opening scrcpy stream: " + " | ".join(self._logs))
            self._decoder = subprocess.Popen(
                [self.ffmpeg_path, "-hide_banner", "-loglevel", "error",
                 "-probesize", "32", "-analyzeduration", "0", "-flags", "low_delay",
                 "-f", "h264", "-i", "pipe:0", "-an", "-threads", "1",
                 "-c:v", "mjpeg", "-q:v", "2", "-fps_mode", "passthrough",
                 "-f", "image2pipe", "-flush_packets", "1", "pipe:1"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                creationflags=flags)
            self._spawn_thread(self._collect_logs, self._decoder.stderr)
            self._spawn_thread(self._feed_decoder, first_chunk)
            self._spawn_thread(self._read_frames)
            self.connected = True
        except (OSError, ValueError, subprocess.TimeoutExpired, RuntimeError) as exc:
            self.close()
            raise RuntimeError(f"scrcpy capture startup failed: {exc}") from exc

    def capture(self) -> np.ndarray:
        self.normalizer.bounds = None
        if not self.connected:
            if time.monotonic() - self.last_connect_attempt < self.reconnect_interval:
                raise RuntimeError(f"Waiting to reconnect scrcpy device {self.serial}")
            self.connect()
        deadline = time.monotonic() + self.timeout
        error = ""
        with self._condition:
            while True:
                if self._stream_error:
                    error = self._stream_error
                    break
                if self._sequence > self._delivered and time.monotonic() - self._latest_at <= self.max_frame_age:
                    frame = self._latest.copy()
                    self._delivered = self._sequence
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    error = "Timed out waiting for a fresh scrcpy frame"
                    break
                self._condition.wait(min(.2, remaining))
        if error:
            details = " | ".join(self._logs)
            self.close()
            self.last_connect_attempt = time.monotonic()
            raise RuntimeError(f"{error}: {details}")
        if float(frame.std()) < 1:
            raise RuntimeError("scrcpy frame is blank; this Android/app combination may still block mirroring")
        self.last_raw_frame = frame
        output = self.normalizer.normalize(frame)
        self.output_size = (output.shape[1], output.shape[0])
        return output

    def map_input_point(self, x: int, y: int) -> tuple[int, int]:
        # The video is downscaled on Android; map through video pixels to the
        # actual Android input resolution, including configured viewport insets.
        vx, vy = self.normalizer.to_native(x, y)
        if self.native_size is None or self.last_raw_frame is None:
            raise RuntimeError("No native display geometry available")
        nw, nh = self.native_size
        vh, vw = self.last_raw_frame.shape[:2]
        if (vw > vh) != (nw > nh):
            nw, nh = nh, nw
        return min(nw-1, round(vx*nw/vw)), min(nh-1, round(vy*nh/vh))

    def close(self) -> None:
        self._stopping.set()
        if self._socket:
            try:
                self._socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self._socket.close()
        # Closing the video socket makes the remote server exit and lets the
        # feeder close FFmpeg stdin. Wait for that normal shutdown first so the
        # server can restore any Android state covered by cleanup=true.
        for process in (self._server_process, self._decoder):
            if process and process.poll() is None:
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=3)
        for thread in self._threads:
            thread.join(timeout=2)
        for process in (self._decoder, self._server_process):
            if process:
                for pipe in (process.stdin, process.stdout, process.stderr):
                    if pipe:
                        pipe.close()
        if self._forward_port is not None:
            try:
                self._checked_adb(["forward", "--remove", f"tcp:{self._forward_port}"], timeout=3)
            except RuntimeError:
                pass
        if self._remote_path:
            try:
                self._checked_adb(["shell", "rm", "-f", self._remote_path], timeout=3)
            except RuntimeError:
                pass
        self._socket = self._decoder = self._server_process = None
        self._forward_port = self._remote_path = None
        self._threads = []
        self._latest = None
        super().close()
