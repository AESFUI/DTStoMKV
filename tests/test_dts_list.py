"""Check DTS channel listing with actual encoded audio."""
import contextlib
import io
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from find_non_51_dts import is_5_1, main
from dtstomkv.runtime import process_options


class LayoutTests(unittest.TestCase):
    def test_groups_layouts_and_unknown_channel_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            layouts = (("b", 8, "7.1"), ("a", 2, "stereo"), ("c", 8, "7.1"),
                       ("d", 6, None), ("e", 7, None), ("f", 6, "5.1(side)"))
            streams = {}
            for name, channels, layout in layouts:
                path = folder / f"{name}.dts"
                path.touch()
                streams[str(path)] = {"channels": channels, "channel_layout": layout}
            def probe(command, **kwargs):
                import json
                return Mock(stdout=json.dumps({"streams": [streams[command[-1]]]}))
            output = folder / "list.txt"
            with patch("find_non_51_dts.executable", return_value="ffprobe"), \
                 patch("find_non_51_dts.subprocess.run", side_effect=probe), \
                 contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main([str(folder), str(output)]), 0)
            self.assertEqual(output.read_text(encoding="utf-8"),
                             f"[7.1 | 8 ch]\n{folder / 'b.dts'}\n{folder / 'c.dts'}\n\n"
                             f"[stereo | 2 ch]\n{folder / 'a.dts'}\n\n"
                             f"[Неизвестная раскладка | 6 ch]\n{folder / 'd.dts'}\n\n"
                             f"[Неизвестная раскладка | 7 ch]\n{folder / 'e.dts'}\n")

    def test_layout_requires_six_channels_and_lfe(self):
        for layout in ("5.1", "5.1(side)"):
            self.assertTrue(is_5_1({"channels": 6, "channel_layout": layout}))
        for stream in ({"channels": 6, "channel_layout": "6.0"}, {"channels": 6},
                       {"channels": 8, "channel_layout": "7.1"},
                       {"channels": 5, "channel_layout": "5.0"}):
            self.assertFalse(is_5_1(stream))


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
class DtsListTests(unittest.TestCase):
    def test_recursive_scan_and_errors(self):
        with tempfile.TemporaryDirectory(prefix="Музыка ") as directory:
            folder = Path(directory)
            nested = folder / "Альбом"
            nested.mkdir()
            stereo, surround = nested / "stereo.DTS", folder / "surround.dts"
            for path, layout in ((stereo, "stereo"), (surround, "5.1(side)")):
                subprocess.run([shutil.which("ffmpeg"), "-v", "error", "-f", "lavfi", "-i",
                                f"anullsrc=r=48000:cl={layout}", "-t", "0.2", "-c:a", "dca",
                                "-strict", "-2", str(path)], check=True, capture_output=True,
                               **process_options())
            (folder / "ignore.wav").write_bytes(b"ignored")
            output = folder / "output" / "list.txt"
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as errors:
                self.assertEqual(main([str(folder), str(output)]), 0)
                self.assertEqual(output.read_text(encoding="utf-8").splitlines(), ["[stereo | 2 ch]", str(stereo)])
                self.assertEqual(errors.getvalue(), "")
                self.assertEqual(main([str(folder), str(stereo)]), 1)
                (nested / "broken.dts").write_bytes(b"invalid DTS")
                self.assertEqual(main([str(folder), str(output)]), 1)
                self.assertIn("broken.dts", errors.getvalue())
                self.assertEqual(output.read_text(encoding="utf-8").splitlines(), ["[stereo | 2 ch]", str(stereo)])
