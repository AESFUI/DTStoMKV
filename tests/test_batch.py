"""Check GUI job snapshots and batch orchestration without a display server."""
import queue
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

try:
    import tkinter as tk
except ImportError:
    tk = None

from dtstomkv.core import Converter


@unittest.skipUnless(tk and shutil.which("ffmpeg") and shutil.which("ffprobe"), "Tkinter and FFmpeg required")
class BatchTests(unittest.TestCase):
    def test_snapshot_and_repeat_batch_with_uppercase_cue(self):
        from dtstomkv.gui import App
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            engine = Converter(log=lambda message: None)
            subprocess.run([engine.ffmpeg, "-v", "error", "-f", "lavfi", "-i",
                            "sine=duration=0.5:sample_rate=48000", str(folder / "album.wav")], check=True)
            (folder / "album.CUE").write_text('TRACK 01 AUDIO\nTITLE "Track"\nINDEX 01 00:00:00\n')
            app = App.__new__(App)
            app.events = queue.Queue()
            app.stop_event = threading.Event()
            master = tk.Tcl()
            for name, value in {"b_in_dir": directory, "b_out_dir": "", "b_video_mode": "none",
                                "b_codec": "flac", "b_bit": "Auto", "b_rate": "Auto", "bitrate": "448k"}.items():
                setattr(app, name, tk.StringVar(master=master, value=value))
            app.overwrite = tk.BooleanVar(master=master, value=False)
            task = app.run_batch()
            app.b_codec.set("invalid")  # Edits after launch must not change the queued job.
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
            app.b_codec.set("flac")
            app.run_batch()()
            messages = []
            while not app.events.empty():
                messages.append(app.events.get()[1])
            self.assertIn("Найдено файлов: 1", messages)
            self.assertFalse((folder / "album_lpcm.mkv").exists())
