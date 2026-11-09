"""End-to-end checks against FFmpeg, using tiny generated sources."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from pathlib import Path

from dtstomkv.core import Cancelled, Converter, Options, detect_bit_depth
from dtstomkv.cue import parse_cue
from dtstomkv.runtime import font_path, process_options


class CueTests(unittest.TestCase):
    def test_legacy_cue_and_invalid_timeline(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.cue"
            path.write_bytes('TRACK 01 AUDIO\nTITLE "Привет"\nINDEX 01 00:00:00\n'.encode("cp1251"))
            self.assertEqual(parse_cue(path)[0]["title"], "Привет")
            path.write_text('TRACK 01 AUDIO\nINDEX 01 00:00:75\n', encoding="utf-8")
            with self.assertRaises(ValueError):
                parse_cue(path)

    def test_zero_bit_depth_falls_back(self):
        self.assertEqual(detect_bit_depth({"bits_per_sample": 0, "codec_name": "dts"}), 24)
        self.assertEqual(detect_bit_depth({"bits_per_raw_sample": "0", "bits_per_sample": 16}), 16)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg is required")
class ConversionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="Тест audio ' % ")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.source = self.directory / "Исходный звук.wav"
        self.engine = Converter(log=lambda message: None)
        self.ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=1.6:sample_rate=48000",
                    "-ac", "2", "-c:a", "pcm_s24le", str(self.source))
        self.cue = self.directory / "Album.CUE"
        self.cue.write_text('FILE "Исходный звук.wav" WAVE\nTRACK 01 AUDIO\n'
                            'TITLE "Первый: 50% ; # = \'"\nINDEX 01 00:00:00\n'
                            'TRACK 02 AUDIO\nTITLE "Второй"\nINDEX 01 00:00:60\n', encoding="utf-8-sig")

    def ffmpeg(self, *arguments):
        subprocess.run([self.engine.ffmpeg, "-v", "error", "-y", *arguments],
                       check=True, capture_output=True, **process_options())

    def test_audio_codecs_and_chapters(self):
        for codec, expected in (("lpcm", "pcm_s24le"), ("flac", "flac"),
                                ("ac3", "ac3"), ("truehd", "truehd")):
            with self.subTest(codec=codec):
                output = self.directory / f"{codec}.mkv"
                self.engine.convert(self.source, output, Options(codec=codec, cue=str(self.cue)))
                info = self.engine.probe(output)
                self.assertEqual(info["streams"][0]["codec_name"], expected)
                self.assertEqual(info["streams"][0]["sample_rate"], "48000")
                self.assertAlmostEqual(float(info["format"]["duration"]), 1.6, delta=0.08)
                data = json.loads(self.engine.run([self.engine.ffprobe, "-v", "error", "-show_chapters",
                                                  "-of", "json", str(output)], capture=True))
                chapters = data["chapters"]
                self.assertEqual(len(chapters), 2)
                self.assertEqual(chapters[0]["tags"]["title"], "Первый: 50% ; # = '")
                self.assertEqual(float(chapters[1]["start_time"]), 0.8)

    def test_channel_normalization_and_preservation(self):
        for layout, channels in (("7.1", 8), ("6.1", 7), ("5.1", 6),
                                 ("5.0", 5), ("4.0", 4), ("stereo", 2), ("mono", 1)):
            source = self.directory / f"{layout}.wav"
            self.ffmpeg("-f", "lavfi", "-i", f"anullsrc=r=48000:cl={layout}",
                        "-t", "0.2", "-c:a", "pcm_s24le", str(source))
            for codec in ("lpcm", "flac", "ac3", "truehd"):
                with self.subTest(layout=layout, codec=codec):
                    output = self.directory / f"{layout}-{codec}.mkv"
                    messages = []
                    engine = Converter(log=messages.append)
                    engine.convert(source, output, Options(codec=codec))
                    audio = engine.probe(output)["streams"][0]
                    expected = 6 if channels > 2 else channels
                    self.assertEqual(audio["channels"], expected)
                    if channels in (4, 5, 7, 8):
                        # PCM in Matroska may expose only the channel count to ffprobe.
                        if codec != "lpcm":
                            self.assertIn(audio["channel_layout"], ("5.1", "5.1(side)"))
                        self.assertTrue(any(line.startswith("Выходной звук:") and "5.1" in line
                                            for line in messages))
            if channels > 6:
                expanded = self.directory / f"{layout}-expanded.mkv"
                result = subprocess.run([sys.executable, "-m", "dtstomkv", "convert", str(source),
                                         str(expanded), "--max-channels", "8"], capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.engine.probe(expanded)["streams"][0]["channels"], 8)
                with self.assertRaisesRegex(ValueError, "максимум 6 каналов"):
                    self.engine.convert(source, self.directory / "unsupported.mkv",
                                        Options(codec="truehd", max_channels=8))
                output = self.directory / f"{layout}-original.mkv"
                result = subprocess.run([sys.executable, "-m", "dtstomkv", "convert", str(source),
                                         str(output), "--no-downmix-5-1"], capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.engine.probe(output)["streams"][0]["channels"], channels)
                with self.assertRaisesRegex(ValueError, "максимум 6 каналов"):
                    self.engine.convert(source, self.directory / "invalid.mkv",
                                        Options(codec="ac3", normalize_channels=False))

    def test_copy_video_from_dts_container(self):
        source = self.directory / "video dts.mkv"
        self.ffmpeg("-f", "lavfi", "-i", "color=s=320x240:r=10:d=1.6", "-i", str(self.source),
                    "-c:v", "libx264", "-c:a", "dca", "-strict", "-2", str(source))
        output = self.directory / "copied.mkv"
        self.engine.convert(source, output, Options(video="copy"))
        streams = self.engine.probe(output)["streams"]
        self.assertEqual({s["codec_name"] for s in streams}, {"h264", "pcm_s24le"})
        # Compare decoded video hashes: copying must preserve every frame.
        def video_hash(path):
            return self.engine.run([self.engine.ffmpeg, "-v", "error", "-i", str(path),
                                    "-map", "0:v:0", "-f", "hash", "-"], capture=True)
        self.assertEqual(video_hash(source), video_hash(output))

    def test_visualization_special_characters_and_chapters(self):
        try:
            font_path()
        except RuntimeError as error:
            self.skipTest(str(error))
        for cue in ("", str(self.cue)):
            with self.subTest(cue=bool(cue)):
                output = self.directory / ("tracks.mkv" if cue else "single.mkv")
                self.engine.convert(self.source, output, Options(video="visual", cue=cue,
                                    album="Album: '50%' \\ ; [a], Музыка", title="Title: 100%",
                                    resolution="320x240"))
                data = self.engine.probe(output)
                self.assertEqual({s["codec_type"] for s in data["streams"]}, {"video", "audio"})
                self.assertAlmostEqual(float(data["format"]["duration"]), 1.6, delta=0.15)

    def test_existing_output_and_failure_are_safe(self):
        output = self.directory / "existing.mkv"
        output.write_bytes(b"preserve me")
        with self.assertRaises(FileExistsError):
            self.engine.convert(self.source, output)
        with self.assertRaises(ValueError):
            self.engine.convert(self.source, output, Options(codec="unknown", overwrite=True))
        self.assertEqual(output.read_bytes(), b"preserve me")
        self.engine.convert(self.source, output, Options(overwrite=True))
        self.assertEqual(self.engine.probe(output)["streams"][0]["codec_name"], "pcm_s24le")
        with self.assertRaises(ValueError):
            self.engine.convert(output, output, Options(overwrite=True))
        self.assertFalse(list(self.directory.glob("dtstomkv-*")))

    def test_cancel_running_process(self):
        timer = threading.Timer(0.3, self.engine.stop_event.set)
        timer.start()
        try:
            with self.assertRaises(Cancelled):
                self.engine.run([sys.executable, "-c", "import time; time.sleep(30)"])
        finally:
            timer.cancel()
            self.engine.stop_event.clear()

    def test_failed_conversion_cleans_staging_and_preserves_output(self):
        output = self.directory / "keep.mkv"
        output.write_bytes(b"original")
        real_run = self.engine.run
        def fail_encoding(command, cwd=None, capture=False, progress=None):
            if cwd:
                (Path(cwd) / "result.mkv").write_bytes(b"partial output")
                raise Cancelled("Остановлено пользователем")
            return real_run(command, cwd=cwd, capture=capture, progress=progress)
        with patch.object(self.engine, "run", side_effect=fail_encoding):
            with self.assertRaises(Cancelled):
                self.engine.convert(self.source, output, Options(overwrite=True))
        self.assertEqual(output.read_bytes(), b"original")
        self.assertFalse(list(self.directory.glob("dtstomkv-*")))

    def test_filesystem_without_hardlinks(self):
        output = self.directory / "exfat.mkv"
        with patch("dtstomkv.core.os.link", side_effect=OSError("hard links unsupported")):
            self.engine.convert(self.source, output)
        self.assertEqual(self.engine.probe(output)["streams"][0]["codec_name"], "pcm_s24le")

    def test_cli(self):
        output = self.directory / "cli.mkv"
        result = subprocess.run([sys.executable, "-m", "dtstomkv", "convert", str(self.source),
                                 str(output), "--codec", "flac"], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.engine.probe(output)["streams"][0]["codec_name"], "flac")


if __name__ == "__main__":
    unittest.main()
