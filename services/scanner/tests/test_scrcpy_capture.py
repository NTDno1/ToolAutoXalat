from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import call, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from scrcpy_capture import JpegStreamParser, ScrcpyFrameSource, parse_display_size


class JpegStreamParserTests(unittest.TestCase):
    def test_splits_frames_across_arbitrary_chunks_and_drops_noise(self):
        parser = JpegStreamParser()
        self.assertEqual([], parser.feed(b"noise\xff"))
        self.assertEqual([], parser.feed(b"\xd8first"))
        self.assertEqual([b"\xff\xd8first\xff\xd9", b"\xff\xd8two\xff\xd9"],
                         parser.feed(b"\xff\xd9junk\xff\xd8two\xff\xd9"))

    def test_rejects_unbounded_corrupt_frame(self):
        parser = JpegStreamParser(limit=10)
        with self.assertRaisesRegex(RuntimeError, "buffer limit"):
            parser.feed(b"\xff\xd8" + b"x" * 20)


class ScrcpyConfigurationTests(unittest.TestCase):
    def test_override_size_wins_when_android_reports_both(self):
        self.assertEqual((1080, 2400), parse_display_size(
            b"Physical size: 720x1600\nOverride size: 1080x2400\n"))

    def test_scrcpy_source_is_read_only_and_uses_raw_stream(self):
        with patch("scrcpy_capture.resolve_program", side_effect=["scrcpy", "ffmpeg"]), \
             patch("scrcpy_capture.Path.is_file", return_value=True), \
             patch("adb_capture.resolve_adb_path", return_value="adb"):
            source = ScrcpyFrameSource({"serial": "phone", "scrcpy_server_path": "server"})
        self.assertEqual(10, source.max_fps)
        self.assertEqual(1600, source.max_size)
        self.assertEqual(8_000_000, source.bit_rate)
        source._remote_path = "/data/local/tmp/server.jar"
        command = source._server_command("4.1", "00112233")
        self.assertIn("audio=false", command)
        self.assertIn("control=false", command)
        self.assertIn("raw_stream=true", command)
        self.assertIn("cleanup=true", command)
        self.assertNotIn("cleanup=false", command)

    def test_old_adb_falls_back_from_literal_tcp_zero_to_explicit_port(self):
        with patch("scrcpy_capture.resolve_program", side_effect=["scrcpy", "ffmpeg"]), \
             patch("scrcpy_capture.Path.is_file", return_value=True), \
             patch("adb_capture.resolve_adb_path", return_value="adb"):
            source = ScrcpyFrameSource({"serial": "phone", "scrcpy_server_path": "server"})
        source._checked_adb = Mock(side_effect=[
            SimpleNamespace(stdout=b""),
            SimpleNamespace(stdout=b""),
            SimpleNamespace(stdout=b""),
        ])
        with patch("scrcpy_capture.find_available_tcp_port", return_value=23456):
            self.assertEqual(23456, source._create_forward("00112233"))
        self.assertEqual([
            call(["forward", "tcp:0", "localabstract:scrcpy_00112233"]),
            call(["forward", "--remove", "tcp:0"]),
            call(["forward", "tcp:23456", "localabstract:scrcpy_00112233"]),
        ], source._checked_adb.call_args_list)


if __name__ == "__main__":
    unittest.main()
