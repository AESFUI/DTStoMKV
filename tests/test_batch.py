"""Check GUI job snapshots and batch orchestration without a display server."""
import queue
import shutil
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from pathlib import Path

try:
    import tkinter as tk
except ImportError:
    tk = None

from dtstomkv.core import Converter, VIDEO_PROFILES


@unittest.skipUnless(tk and shutil.which("ffmpeg") and shutil.which("ffprobe"), "Tkinter and FFmpeg required")
class BatchTests(unittest.TestCase):
    def test_visual_profile_is_snapshotted_for_the_whole_batch(self):
        from dtstomkv.gui import App
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "first.wav").touch()
            (folder / "second.wav").touch()
            app = App.__new__(App)
            app.events = queue.Queue()
            app.stop_event = threading.Event()
            master = tk.Tcl()
            for name, value in {"b_in_dir": directory, "b_out_dir": "", "b_video_mode": "visual",
                                "b_video_profile": VIDEO_PROFILES["hevc"].label,
                                "b_codec": "lpcm", "b_bit": "Auto", "b_rate": "Auto", "bitrate": "448k"}.items():
                setattr(app, name, tk.StringVar(master=master, value=value))
            app.overwrite = tk.BooleanVar(master=master, value=False)
            app.downmix_5_1 = tk.BooleanVar(master=master, value=True)
            task = app.run_batch()
            app.downmix_5_1.set(False)
            app.b_video_profile.set(VIDEO_PROFILES["av1"].label)
            engine = Mock()
            with patch.object(app, "converter", return_value=engine):
                task()
            self.assertEqual(engine.convert.call_count, 2)
            for call in engine.convert.call_args_list:
                self.assertEqual(call.args[2].video, "visual")
                self.assertEqual(call.args[2].video_profile, "hevc")
                self.assertTrue(call.args[2].downmix_5_1)

    def test_snapshot_and_repeat_batch_with_uppercase_cue(self):
        from dtstomkv.gui import App
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            engine = Converter(log=lambda message: None)
            subprocess.run([engine.ffmpeg, "-v", "error", "-f", "lavfi", "-i",
                            "sine=duration=0.5:sample_rate=48000", str(folder / "album.wav")], check=True)
            shutil.copyfile(folder / "album.wav", folder / "without-cue.wav")
            (folder / "album.CUE").write_text('TRACK 01 AUDIO\nTITLE "Track"\nINDEX 01 00:00:00\n')
            app = App.__new__(App)
            app.events = queue.Queue()
            app.stop_event = threading.Event()
            master = tk.Tcl()
            for name, value in {"b_in_dir": directory, "b_out_dir": "", "b_video_mode": "none",
                                "b_video_profile": VIDEO_PROFILES["hevc"].label,
                                "b_codec": "flac", "b_bit": "Auto", "b_rate": "Auto", "bitrate": "448k"}.items():
                setattr(app, name, tk.StringVar(master=master, value=value))
            app.overwrite = tk.BooleanVar(master=master, value=False)
            app.downmix_5_1 = tk.BooleanVar(master=master, value=True)
            task = app.run_batch()
            self.assertEqual(app.options("b").video_profile, "hevc")
            app.b_codec.set("invalid")  # Edits after launch must not change the queued job.
            app.b_video_profile.set(VIDEO_PROFILES["vp9"].label)
            failures = []
            def run():
                try:
                    task()
                except Exception as error:
                    failures.append(error)
            worker = threading.Thread(target=run)
            worker.start()
            worker.join(timeout=20)
            self.assertFalse(worker.is_alive())
            self.assertEqual(failures, [])
            self.assertEqual(engine.probe(folder / "album.mkv")["streams"][0]["codec_name"], "flac")
            self.assertEqual(engine.probe(folder / "without-cue.mkv")["streams"][0]["codec_name"], "flac")
            app.b_codec.set("flac")
            app.run_batch()()
            messages = []
            while not app.events.empty():
                messages.append(app.events.get()[1])
            self.assertIn("Найдено файлов: 2", messages)
            self.assertEqual(sum(message.startswith("Исходный звук:") for message in messages), 2)
            self.assertEqual(sum(message.startswith("Выходной звук: FLAC") for message in messages), 2)
            self.assertFalse((folder / "album_lpcm.mkv").exists())
