"""Acoustic color mapping and measured titles, including the encoded frames."""
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import ImageFont

from dtstomkv.core import Converter, Options, audio_format, fit_text, playback_timer, sound_color, sound_features
from dtstomkv.runtime import font_path


class ColorTests(unittest.TestCase):
    def test_content_changes_color_and_silence_is_finite(self):
        time = np.arange(22050) / 22050
        bass = 0.25 * np.sin(2 * np.pi * 80 * time)
        treble = 0.25 * np.sin(2 * np.pi * 6000 * time)
        pulses = bass * (np.sin(2 * np.pi * 8 * time) > 0)
        colors = [sound_color(sound_features(audio)) for audio in (bass, treble, pulses)]
        self.assertEqual(len(set(colors)), 3)
        for audio in (np.zeros(22050), np.array([]), np.array([0.1])):
            features = sound_features(audio)
            self.assertTrue(all(np.isfinite(value) for value in features.values()))
            self.assertRegex(sound_color(features), r"^0x[0-9a-f]{6}$")

    def test_lossy_format_does_not_claim_pcm_bit_depth(self):
        self.assertEqual(audio_format("lpcm", 96000, 6, 24, "5.1"), "LPCM · 24 bit · 96 kHz · 5.1")
        self.assertEqual(audio_format("ac3", 48000, 2, 24), "AC3 · 48 kHz · 2 ch")


class TitleTests(unittest.TestCase):
    def setUp(self):
        try:
            self.font = font_path()
        except RuntimeError as error:
            self.skipTest(str(error))

    def test_actual_glyph_width_and_two_lines(self):
        for width in (288, 1152):
            for title in ("Очень длинное название трека: Музыка '100%' " * 5, "W" * 150):
                with self.subTest(width=width, title=title[:20]):
                    text, size = fit_text(title, self.font, 36, width)
                    self.assertEqual(len(text.splitlines()), 2)
                    self.assertEqual(text.replace("\n", "").replace(" ", ""), title.replace(" ", ""))
                    face = ImageFont.truetype(str(self.font), size)
                    self.assertTrue(all(face.getlength(line) <= width for line in text.splitlines()))
        self.assertEqual(fit_text("Short", self.font, 36, 1152), ("Short", 36))


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
class VisualIntegrationTests(unittest.TestCase):
    def test_timer_renders_whole_seconds_and_optional_hours(self):
        try:
            font = font_path()
        except RuntimeError as error:
            self.skipTest(str(error))
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            shutil.copyfile(font, work / "font.ttf")
            engine = Converter(log=lambda message: None)
            cases = ((199.9, 61, "01:01 / 03:19"),
                     (3599.9, 3599, "59:59 / 59:59"),
                     (3600, 61, "00:01:01 / 01:00:00"),
                     (3600, 3599, "00:59:59 / 01:00:00"),
                     (3661.9, 3600, "01:00:00 / 01:01:01"))
            for duration, elapsed, expected in cases:
                with self.subTest(duration=duration, elapsed=elapsed):
                    (work / "timer.txt").write_text(playback_timer(duration), encoding="utf-8")
                    (work / "expected.txt").write_text(expected, encoding="utf-8")
                    def render(filename, expansion):
                        return engine.run([engine.ffmpeg, "-v", "error", "-f", "lavfi", "-i", "color=s=640x64:r=1",
                                           "-filter:v", f"setpts=PTS+{elapsed}/TB,drawtext=fontfile=font.ttf:"
                                           f"textfile={filename}:expansion={expansion}:fontcolor=white:fontsize=24",
                                           "-frames:v", "1", "-f", "framemd5", "-"], cwd=work, capture=True)
                    self.assertEqual(render("timer.txt", "normal"), render("expected.txt", "none"))

    def test_modes_log_audio_and_visual_frame_contains_format(self):
        try:
            font_path()
        except RuntimeError as error:
            self.skipTest(str(error))
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            messages = []
            engine = Converter(log=messages.append)
            source = work / "source.wav"
            engine.run([engine.ffmpeg, "-v", "error", "-f", "lavfi", "-i",
                        "sine=duration=0.5:sample_rate=48000", str(source)])
            original_visualization = engine.visualization
            captured = {}

            def inspect(*args):
                original_visualization(*args)
                staging = args[4]
                captured["title"] = (staging / "track0.txt").read_text(encoding="utf-8")
                captured["format"] = (staging / "format.txt").read_text(encoding="utf-8")
                captured["timer"] = (staging / "timer.txt").read_text(encoding="utf-8")

            for mode in ("none", "copy", "visual"):
                with self.subTest(mode=mode), patch.object(engine, "visualization", side_effect=inspect):
                    messages.clear()
                    output = work / f"{mode}.mkv"
                    engine.convert(source, output, Options(video=mode, title="Длинное имя трека " * 8,
                                                           resolution="320x240"))
                    self.assertTrue(any(line.startswith("Исходный звук: LPCM") for line in messages))
                    self.assertTrue(any(line.startswith("Выходной звук: LPCM") for line in messages))
                    if mode == "visual":
                        self.assertEqual(captured["timer"], playback_timer(0.5))
                        self.assertEqual(len(captured["title"].splitlines()), 2)
                        self.assertRegex(captured["format"], r"^LPCM · 16 bit · 48 kHz · (mono|1 ch)$")
                        raw = work / "frame.rgb"
                        engine.run([engine.ffmpeg, "-v", "error", "-i", str(output), "-frames:v", "1",
                                    "-f", "rawvideo", "-pix_fmt", "rgb24", str(raw)])
                        pixels = np.frombuffer(raw.read_bytes(), dtype=np.uint8).reshape(240, 320, 3)
                        # White text in the format row, safely inside the picture.
                        self.assertTrue(np.any(np.all(pixels[204:230, 16:304] > 200, axis=2)))

    def test_filtergraph_loading_on_new_and_older_ffmpeg(self):
        engine = Converter(log=lambda message: None)
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            (work / "visual.txt").write_text("null", encoding="utf-8")
            supports_file_filters = engine.video_filter(work)[0] == "-/filter:v"
            for version, option in (("9.0.2", "-/filter:v"), ("6.1", "-filter:v")):
                engine._file_filters = None
                with patch.object(engine, "run", return_value=f"ffmpeg version {version}"):
                    arguments = engine.video_filter(work)
                self.assertEqual(arguments[0], option)
                # Older FFmpeg installations can only execute the inline form.
                if option == "-filter:v" or supports_file_filters:
                    engine.run([engine.ffmpeg, "-v", "error", "-f", "lavfi", "-i", "color=s=32x32",
                                *arguments, "-frames:v", "1", "-f", "null", "-"], cwd=work)
