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

from dtstomkv.core import Converter


@unittest.skipUnless(tk and shutil.which("ffmpeg") and shutil.which("ffprobe"), "Tkinter and FFmpeg required")
class BatchTests(unittest.TestCase):
    def test_mixed_dts_and_ac3_batch_copies_only_ac3(self):
        from dtstomkv.gui import App
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            source = folder / "input"
            source.mkdir()
            output = folder / "output"
            engine = Converter(log=lambda message: None)
            for name, codec, extra in (("dolby.ac3", "ac3", []),
                                       ("surround.dts", "dca", ["-strict", "-2"])):
                engine.run([engine.ffmpeg, "-v", "error", "-f", "lavfi", "-i",
                            "anullsrc=r=44100:cl=5.0(side)", "-t", "0.5",
                            "-c:a", codec, *extra, str(source / name)])
            app = App.__new__(App)
            app.events = queue.Queue()
            app.stop_event = threading.Event()
            settings = dict(b_in_dir=str(source), b_out_dir=str(output), b_video_mode="none",
                            b_codec="lpcm", b_bit="Auto", b_rate="Auto", bitrate="192k",
                            overwrite=False, normalize_channels=True, max_channels="5.1", copy_ac3=True)
            for name, value in settings.items():
                setattr(app, name, Mock(get=Mock(return_value=value)))
            task = app.run_batch()
            app.copy_ac3.get.return_value = False  # The running batch keeps its snapshot.
            task()
            dolby = engine.probe(output / "dolby.mkv")["streams"][0]
            dts = engine.probe(output / "surround.mkv")["streams"][0]
            self.assertEqual((dolby["codec_name"], dolby["channels"]), ("ac3", 5))
            self.assertEqual((dts["codec_name"], dts["channels"]), ("pcm_s24le", 6))
            def digest(path):
                return engine.run([engine.ffmpeg, "-v", "error", "-i", str(path),
                                   "-map", "0:a:0", "-c:a", "copy", "-f", "hash", "-"], capture=True)
            self.assertEqual(digest(source / "dolby.ac3"), digest(output / "dolby.mkv"))

    def test_visual_settings_are_snapshotted_for_the_whole_batch(self):
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
                                "b_codec": "lpcm", "b_bit": "Auto", "b_rate": "Auto", "bitrate": "448k"}.items():
                setattr(app, name, tk.StringVar(master=master, value=value))
            app.overwrite = tk.BooleanVar(master=master, value=False)
            app.normalize_channels = tk.BooleanVar(master=master, value=True)
            app.copy_ac3 = tk.BooleanVar(master=master, value=True)
            app.max_channels = tk.StringVar(master=master, value="5.1")
            task = app.run_batch()
            app.normalize_channels.set(False)
            app.copy_ac3.set(False)
            app.max_channels.set("7.1")
            app.b_video_mode.set("none")
            engine = Mock()
            with patch.object(app, "converter", return_value=engine):
                task()
            self.assertEqual(engine.convert.call_count, 2)
            for call in engine.convert.call_args_list:
                self.assertEqual(call.args[2].video, "visual")
                self.assertTrue(call.args[2].normalize_channels)
                self.assertTrue(call.args[2].copy_ac3)
                self.assertEqual(call.args[2].max_channels, 6)

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
                                "b_codec": "flac", "b_bit": "Auto", "b_rate": "Auto", "bitrate": "448k"}.items():
                setattr(app, name, tk.StringVar(master=master, value=value))
            app.overwrite = tk.BooleanVar(master=master, value=False)
            app.normalize_channels = tk.BooleanVar(master=master, value=True)
            app.copy_ac3 = tk.BooleanVar(master=master, value=True)
            app.max_channels = tk.StringVar(master=master, value="5.1")
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
