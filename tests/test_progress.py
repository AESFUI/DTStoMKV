"""Progress records must reach the log while encoding is still running."""
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from dtstomkv.core import Converter, EncodingProgress


class ProgressTests(unittest.TestCase):
    def test_partial_records_unknown_values_and_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "progress.txt"
            messages = []
            progress = EncodingProgress(path, 10, messages.append)
            progress.poll()  # FFmpeg has not created the file yet.
            path.write_bytes(b"out_time_us=N/A\nspeed=N/A\nprogress=continue\nout_time_us=500")
            progress.poll()
            self.assertIn("0.0%", messages[-1])
            with path.open("ab") as stream:
                stream.write(b"0000\nspeed=2.0x\nprogress=continue\n")
            progress.poll()
            self.assertIn("50.0%", messages[-1])
            self.assertIn("00:00:05 / 00:00:10", messages[-1])
            self.assertIn("скорость 2.0x", messages[-1])
            count = len(messages)
            progress.poll()
            self.assertEqual(len(messages), count)  # No repeated records on every poll.
            with path.open("ab") as stream:
                stream.write(b"out_time_us=11000000\nprogress=end\n")
            progress.poll()
            self.assertFalse(any("100.0%" in message for message in messages))
            progress.complete()  # Only after the process exits successfully.
            self.assertIn("100.0%", messages[-1])

    def test_unknown_duration_and_legacy_microseconds(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "progress.txt"
            messages = []
            progress = EncodingProgress(path, 0, messages.append)
            path.write_bytes(b"out_time_ms=3000000\nspeed=1.5x\nprogress=continue\n")
            progress.poll()
            self.assertIn("00:00:03", messages[-1])
            self.assertNotIn("%", messages[-1])
            progress.complete()
            self.assertIn("завершено", messages[-1])

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
    def test_real_ffmpeg_reports_before_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            records = []
            engine = Converter(log=lambda message: None)
            progress = EncodingProgress(work / "progress.txt", 2.4,
                                        lambda message: records.append((time.monotonic(), message)))
            engine.run([engine.ffmpeg, "-v", "error", "-nostdin", "-re", "-f", "lavfi", "-i",
                        "sine=duration=2.4", "-stats_period", "0.2", "-progress", "progress.txt",
                        "-f", "null", "-"], cwd=work, progress=progress)
            finished = time.monotonic()
            intermediate = [(timestamp, message) for timestamp, message in records
                            if "00:00:01" in message and "скорость" in message]
            self.assertTrue(intermediate, records)
            self.assertLess(intermediate[0][0], finished - 0.2)
