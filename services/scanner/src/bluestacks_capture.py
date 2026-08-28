"""Background-only BlueStacks window capture for Windows.

This module deliberately does not call SetForegroundWindow, ShowWindow, or any
input API. PrintWindow asks the target process to render into an off-screen
bitmap, so capture keeps working while another window covers BlueStacks.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
from typing import Optional, Tuple

import numpy as np


PW_RENDERFULLCONTENT = 0x00000002
BI_RGB = 0
DIB_RGB_COLORS = 0


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [
        ("bmiHeader", BITMAPINFOHEADER),
        ("bmiColors", wintypes.DWORD * 3),
    ]


class BlueStacksWindowCapture:
    """Capture the Android surface of one BlueStacks instance off-screen."""

    def __init__(self, adb_port: int, window_title: str = ""):
        if os.name != "nt":
            raise RuntimeError("BlueStacks window capture is only available on Windows")

        self.adb_port = int(adb_port)
        self.window_title = window_title.strip() or self._title_for_adb_port()
        self._hwnd: Optional[int] = None

        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        self._configure_winapi()

        # Keep Win32 window coordinates and bitmap pixels consistent on a
        # monitor with scaling enabled. It is fine if this was set already.
        try:
            self.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except (AttributeError, OSError):
            pass

    def _configure_winapi(self) -> None:
        self.WNDENUMPROC = ctypes.WINFUNCTYPE(
            wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
        )
        self.user32.EnumWindows.argtypes = [self.WNDENUMPROC, wintypes.LPARAM]
        self.user32.EnumChildWindows.argtypes = [
            wintypes.HWND,
            self.WNDENUMPROC,
            wintypes.LPARAM,
        ]
        self.user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        self.user32.GetWindowTextLengthW.restype = ctypes.c_int
        self.user32.GetWindowTextW.argtypes = [
            wintypes.HWND,
            wintypes.LPWSTR,
            ctypes.c_int,
        ]
        self.user32.GetClassNameW.argtypes = [
            wintypes.HWND,
            wintypes.LPWSTR,
            ctypes.c_int,
        ]
        self.user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
        self.user32.GetWindowRect.restype = wintypes.BOOL
        self.user32.IsWindow.argtypes = [wintypes.HWND]
        self.user32.IsWindow.restype = wintypes.BOOL
        self.user32.IsWindowVisible.argtypes = [wintypes.HWND]
        self.user32.IsWindowVisible.restype = wintypes.BOOL
        self.user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
        self.user32.PrintWindow.restype = wintypes.BOOL

        self.gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
        self.gdi32.CreateCompatibleDC.restype = wintypes.HDC
        self.gdi32.CreateDIBSection.argtypes = [
            wintypes.HDC,
            ctypes.POINTER(BITMAPINFO),
            wintypes.UINT,
            ctypes.POINTER(ctypes.c_void_p),
            wintypes.HANDLE,
            wintypes.DWORD,
        ]
        self.gdi32.CreateDIBSection.restype = wintypes.HBITMAP
        self.gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
        self.gdi32.SelectObject.restype = wintypes.HGDIOBJ
        self.gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
        self.gdi32.DeleteObject.restype = wintypes.BOOL
        self.gdi32.DeleteDC.argtypes = [wintypes.HDC]
        self.gdi32.DeleteDC.restype = wintypes.BOOL

    @staticmethod
    def _read_bluestacks_config() -> dict[str, str]:
        program_data = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
        path = Path(program_data) / "BlueStacks_nxt" / "bluestacks.conf"
        values: dict[str, str] = {}
        try:
            for raw_line in path.read_text(encoding="utf-8").splitlines():
                if "=" not in raw_line:
                    continue
                key, value = raw_line.split("=", 1)
                values[key.strip()] = value.strip().strip('"')
        except OSError:
            pass
        return values

    def _title_for_adb_port(self) -> str:
        values = self._read_bluestacks_config()
        suffix = ".adb_port"
        for key, value in values.items():
            if not key.startswith("bst.instance.") or not key.endswith(suffix):
                continue
            if key.endswith(".status.adb_port") or value != str(self.adb_port):
                continue
            prefix = key[: -len(suffix)]
            return values.get(f"{prefix}.display_name", "")
        return ""

    def _window_text(self, hwnd: int) -> str:
        length = self.user32.GetWindowTextLengthW(hwnd)
        buffer = ctypes.create_unicode_buffer(max(length + 1, 256))
        self.user32.GetWindowTextW(hwnd, buffer, len(buffer))
        return buffer.value

    def _class_name(self, hwnd: int) -> str:
        buffer = ctypes.create_unicode_buffer(256)
        self.user32.GetClassNameW(hwnd, buffer, len(buffer))
        return buffer.value

    def _rect(self, hwnd: int) -> RECT:
        rect = RECT()
        if not self.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            raise RuntimeError(f"GetWindowRect failed for HWND {hwnd}")
        return rect

    def _find_main_window(self) -> int:
        candidates: list[Tuple[int, str, int]] = []

        @self.WNDENUMPROC
        def callback(hwnd, _lparam):
            if not self.user32.IsWindowVisible(hwnd):
                return True
            title = self._window_text(hwnd)
            class_name = self._class_name(hwnd)
            if not title or not class_name.startswith("Qt"):
                return True
            if "BlueStacks" not in title and "App Player" not in title:
                return True
            rect = self._rect(hwnd)
            area = max(0, rect.right - rect.left) * max(0, rect.bottom - rect.top)
            candidates.append((int(hwnd), title, area))
            return True

        self.user32.EnumWindows(callback, 0)
        if self.window_title:
            exact = [item for item in candidates if item[1] == self.window_title]
            if exact:
                return max(exact, key=lambda item: item[2])[0]

        if len(candidates) == 1:
            return candidates[0][0]

        found = ", ".join(repr(item[1]) for item in candidates) or "none"
        target = repr(self.window_title) if self.window_title else "auto-detected title"
        raise RuntimeError(f"Cannot select BlueStacks window {target}; candidates: {found}")

    def _get_main_window(self) -> int:
        if self._hwnd and self.user32.IsWindow(self._hwnd):
            return self._hwnd
        self._hwnd = self._find_main_window()
        return self._hwnd

    def _android_surface_rect(self, main_hwnd: int) -> Optional[RECT]:
        surfaces: list[Tuple[int, RECT]] = []

        @self.WNDENUMPROC
        def callback(hwnd, _lparam):
            if self._class_name(hwnd) == "BlueStacksApp":
                rect = self._rect(hwnd)
                area = max(0, rect.right - rect.left) * max(0, rect.bottom - rect.top)
                surfaces.append((area, rect))
            return True

        self.user32.EnumChildWindows(main_hwnd, callback, 0)
        return max(surfaces, key=lambda item: item[0])[1] if surfaces else None

    def _print_window(self, hwnd: int, width: int, height: int) -> np.ndarray:
        bitmap_info = BITMAPINFO()
        bitmap_info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bitmap_info.bmiHeader.biWidth = width
        bitmap_info.bmiHeader.biHeight = -height  # top-down pixels
        bitmap_info.bmiHeader.biPlanes = 1
        bitmap_info.bmiHeader.biBitCount = 32
        bitmap_info.bmiHeader.biCompression = BI_RGB

        memory_dc = self.gdi32.CreateCompatibleDC(0)
        if not memory_dc:
            raise RuntimeError("CreateCompatibleDC failed")

        pixels = ctypes.c_void_p()
        bitmap = self.gdi32.CreateDIBSection(
            memory_dc,
            ctypes.byref(bitmap_info),
            DIB_RGB_COLORS,
            ctypes.byref(pixels),
            None,
            0,
        )
        if not bitmap or not pixels.value:
            self.gdi32.DeleteDC(memory_dc)
            raise RuntimeError("CreateDIBSection failed")

        previous = self.gdi32.SelectObject(memory_dc, bitmap)
        try:
            ok = self.user32.PrintWindow(hwnd, memory_dc, PW_RENDERFULLCONTENT)
            if not ok:
                ok = self.user32.PrintWindow(hwnd, memory_dc, 0)
            if not ok:
                raise RuntimeError("PrintWindow failed")

            raw = ctypes.string_at(pixels.value, width * height * 4)
            bgra = np.frombuffer(raw, dtype=np.uint8).reshape((height, width, 4))
            return bgra[:, :, :3].copy()
        finally:
            if previous:
                self.gdi32.SelectObject(memory_dc, previous)
            self.gdi32.DeleteObject(bitmap)
            self.gdi32.DeleteDC(memory_dc)

    def capture(self) -> np.ndarray:
        """Return only the Android content as a BGR numpy array."""
        hwnd = self._get_main_window()
        main_rect = self._rect(hwnd)
        width = main_rect.right - main_rect.left
        height = main_rect.bottom - main_rect.top
        if width <= 0 or height <= 0:
            raise RuntimeError("BlueStacks window has no drawable area")

        frame = self._print_window(hwnd, width, height)
        surface = self._android_surface_rect(hwnd)
        if surface is not None:
            left = max(0, surface.left - main_rect.left)
            top = max(0, surface.top - main_rect.top)
            right = min(width, surface.right - main_rect.left)
            bottom = min(height, surface.bottom - main_rect.top)
            if right > left and bottom > top:
                frame = frame[top:bottom, left:right].copy()

        if frame.size == 0 or float(frame.std()) < 1.0:
            raise RuntimeError("BlueStacks returned an empty/blank frame")
        return frame
