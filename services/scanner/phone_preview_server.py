from __future__ import annotations

import argparse
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
import threading

import cv2

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from scrcpy_capture import ScrcpyFrameSource  # noqa: E402


class PreviewState:
    def __init__(
        self,
        config_path: Path,
        *,
        max_size: int | None = None,
        max_fps: int | None = None,
        bit_rate: int | None = None,
        jpeg_quality: int = 68,
        jpeg_max_width: int = 540,
        jpeg_max_height: int = 1200,
    ):
        self.config_path = config_path
        self.lock = threading.Lock()
        config = json.loads(config_path.read_text(encoding="utf-8-sig"))
        self.source_config = dict(config["source"])
        if max_size is not None:
            self.source_config["video_max_size"] = max(320, int(max_size))
        if max_fps is not None:
            self.source_config["video_max_fps"] = max(1, min(30, int(max_fps)))
        if bit_rate is not None:
            self.source_config["video_bit_rate"] = max(100000, int(bit_rate))
        self.screen_config = config.get("screen")
        self.jpeg_quality = max(35, min(90, int(jpeg_quality)))
        self.jpeg_max_width = max(240, int(jpeg_max_width))
        self.jpeg_max_height = max(360, int(jpeg_max_height))
        self.source = ScrcpyFrameSource(self.source_config, self.screen_config)

    def _resize_for_preview(self, frame):
        height, width = frame.shape[:2]
        scale = min(
            1.0,
            self.jpeg_max_width / max(1, width),
            self.jpeg_max_height / max(1, height),
        )
        if scale >= 0.999:
            return frame
        return cv2.resize(
            frame,
            (max(1, round(width * scale)), max(1, round(height * scale))),
            interpolation=cv2.INTER_AREA,
        )

    def capture_jpeg(self) -> bytes:
        with self.lock:
            frame = self.source.capture()
            raw = self.source.last_raw_frame if self.source.last_raw_frame is not None else frame
            preview = self._resize_for_preview(raw)
            ok, encoded = cv2.imencode(".jpg", preview, [int(cv2.IMWRITE_JPEG_QUALITY), self.jpeg_quality])
            if not ok:
                raise RuntimeError("Cannot encode preview frame")
            return encoded.tobytes()

    def status(self) -> dict:
        with self.lock:
            native_size = self.source.native_size
            return {
                "ready": True,
                "connected": bool(self.source.connected),
                "serial": self.source.serial,
                "nativeSize": list(native_size) if native_size else None,
                "preview": {
                    "jpegQuality": self.jpeg_quality,
                    "maxWidth": self.jpeg_max_width,
                    "maxHeight": self.jpeg_max_height,
                    "videoMaxSize": self.source_config.get("video_max_size"),
                    "videoMaxFps": self.source_config.get("video_max_fps"),
                    "videoBitRate": self.source_config.get("video_bit_rate"),
                },
            }

    def close(self) -> None:
        with self.lock:
            self.source.close()


def build_handler(state: PreviewState):
    class Handler(BaseHTTPRequestHandler):
        server_version = "ToolAutoXalatPhonePreview/1.0"

        def log_message(self, fmt: str, *args) -> None:
            return

        def _send_json(self, status: int, payload: dict) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            if self.path.startswith("/health"):
                self._send_json(HTTPStatus.OK, {"ok": True})
                return
            if self.path.startswith("/status"):
                try:
                    self._send_json(HTTPStatus.OK, state.status())
                except Exception as exc:
                    self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
                return
            if self.path.startswith("/screenshot"):
                try:
                    data = state.capture_jpeg()
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", "image/jpeg")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except Exception as exc:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found"})

    return Handler


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--max-size", type=int)
    parser.add_argument("--max-fps", type=int)
    parser.add_argument("--bit-rate", type=int)
    parser.add_argument("--jpeg-quality", type=int, default=68)
    parser.add_argument("--jpeg-max-width", type=int, default=540)
    parser.add_argument("--jpeg-max-height", type=int, default=1200)
    args = parser.parse_args()

    state = PreviewState(
        args.config.resolve(),
        max_size=args.max_size,
        max_fps=args.max_fps,
        bit_rate=args.bit_rate,
        jpeg_quality=args.jpeg_quality,
        jpeg_max_width=args.jpeg_max_width,
        jpeg_max_height=args.jpeg_max_height,
    )
    server = ThreadingHTTPServer((args.host, args.port), build_handler(state))
    try:
        server.serve_forever()
    finally:
        state.close()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
