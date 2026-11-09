"""Automatic file names in CLI, Tk snapshots, and batch jobs."""
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

try:
    import tkinter as tk
except ImportError:
    tk = None

from dtstomkv.core import Converter, default_output
from dtstomkv.runtime import process_options


class OutputNameTests(unittest.TestCase):
    def test_changes_only_the_last_extension(self):
        self.assertEqual(default_output(Path("album.mix.DTS")), Path("album.mix.mkv"))
        self.assertEqual(default_output(Path("input/Музыка.mkv"), "output"), Path("output/Музыка.mkv"))

    @unittest.skipUnless(tk, "Tkinter required")
    def test_gui_follows_input_and_preserves_chosen_directory(self):
        from dtstomkv.gui import App
        app = App.__new__(App)
        master = tk.Tcl()
        for prefix in ("s", "v"):
            source, output = tk.StringVar(master=master), tk.StringVar(master=master)
            setattr(app, prefix + "_in", source)
            setattr(app, prefix + "_out", output)
            source.set(str(Path("first") / "Альбом.dts"))
            app.update_output_name(prefix)
            self.assertEqual(Path(output.get()), Path("first/Альбом.mkv"))
            source.set(str(Path("second") / "Other.flac"))
            app.update_output_name(prefix)
            self.assertEqual(Path(output.get()), Path("second/Other.mkv"))
            output.set(str(Path("chosen") / "Other.mkv"))
            source.set(str(Path("third") / "Next.wav"))
            app.update_output_name(prefix)
            self.assertEqual(Path(output.get()), Path("chosen/Next.mkv"))


@unittest.skipUnless(tk, "Tkinter required")
class BatchNameTests(unittest.TestCase):
    def make_app(self, source, output):
        from dtstomkv.gui import App
        app = App.__new__(App)
        app.events = queue.Queue()
        app.stop_event = threading.Event()
        master = tk.Tcl()
        for name, value in {"b_in_dir": str(source), "b_out_dir": str(output), "b_video_mode": "none",
                            "b_codec": "lpcm", "b_bit": "Auto", "b_rate": "Auto", "bitrate": "448k"}.items():
            setattr(app, name, tk.StringVar(master=master, value=value))
        app.overwrite = tk.BooleanVar(master=master, value=True)
        app.normalize_channels = tk.BooleanVar(master=master, value=True)
        app.max_channels = tk.StringVar(master=master, value="5.1")
        return app

    def test_mkv_keeps_its_name_in_another_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory) / "input", Path(directory) / "output"
            source.mkdir()
            for name in ("Альбом.mkv", "Other_lpcm.flac"):
                (source / name).touch()
            app = self.make_app(source, output)
            engine = Mock()
            with patch.object(app, "converter", return_value=engine):
                app.run_batch()()
            self.assertEqual({call.args[1].name for call in engine.convert.call_args_list},
                             {"Альбом.mkv", "Other_lpcm.mkv"})

    def test_same_directory_mkv_and_colliding_names_fail_before_start(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            (source / "album.mkv").touch()
            app = self.make_app(source, source)
            with self.assertRaisesRegex(ValueError, "другую папку"):
                app.run_batch()
            (source / "album.wav").touch()
            # Existing output next to its source is excluded on repeat runs.
            engine = Mock()
            with patch.object(app, "converter", return_value=engine):
                app.run_batch()()
            self.assertEqual(engine.convert.call_count, 1)
            self.assertEqual(engine.convert.call_args.args[0].name, "album.wav")
            (source / "album.flac").touch()
            with self.assertRaisesRegex(ValueError, "Совпадает выходное имя"):
                app.run_batch()


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
class CliNameTests(unittest.TestCase):
    def test_default_name_and_directory_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            engine = Converter(log=lambda message: None)
            source = work / "Альбом.mix.wav"
            engine.run([engine.ffmpeg, "-v", "error", "-f", "lavfi", "-i", "sine=duration=0.3", str(source)])
            output_directory = work / "other"
            output_directory.mkdir()
            for destination in ([], [str(output_directory)]):
                result = subprocess.run([sys.executable, "-m", "dtstomkv", "convert", str(source), *destination],
                                        capture_output=True, **process_options())
                self.assertEqual(result.returncode, 0, result.stderr)
                expected = default_output(source, output_directory if destination else work)
                self.assertTrue(expected.is_file())
                self.assertIn(b"100.0%", result.stdout)
