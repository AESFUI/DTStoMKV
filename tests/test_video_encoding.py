"""H.264 visualization preserves audio and chapters and decodes with FFmpeg."""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from dtstomkv.core import Converter, Options
from dtstomkv.runtime import font_path, process_options


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
class VideoEncodingTests(unittest.TestCase):
    def setUp(self):
        try:
            font_path()
        except RuntimeError as error:
            self.skipTest(str(error))
        self.temp = tempfile.TemporaryDirectory(prefix="Видео ' test ")
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.messages = []
        self.engine = Converter(log=self.messages.append)
        self.source = self.folder / "source.wav"
        self.engine.run([self.engine.ffmpeg, "-v", "error", "-f", "lavfi", "-i",
                         "sine=duration=0.8:sample_rate=48000", "-ac", "2", str(self.source)])
        self.cue = self.folder / "album.cue"
        self.cue.write_text('TRACK 01 AUDIO\nTITLE "Длинное название: 100% музыки на экране"\n'
                            'INDEX 01 00:00:00\nTRACK 02 AUDIO\nTITLE "Второй"\n'
                            'INDEX 01 00:00:30\n', encoding="utf-8")

    def test_h264_preserves_audio_and_chapters_and_decodes(self):
        output = self.folder / "h264.mkv"
        self.engine.convert(self.source, output, Options(video="visual", cue=str(self.cue), resolution="320x240"))
        data = self.engine.probe(output)
        video = next(stream for stream in data["streams"] if stream["codec_type"] == "video")
        audio = next(stream for stream in data["streams"] if stream["codec_type"] == "audio")
        self.assertEqual(video["codec_name"], "h264")
        self.assertEqual((video["width"], video["height"]), (320, 240))
        self.assertEqual((audio["codec_name"], audio["sample_rate"], audio["channels"]), ("pcm_s16le", "48000", 2))
        self.assertAlmostEqual(float(data["format"]["duration"]), 0.8, delta=0.15)
        chapters = json.loads(self.engine.run([self.engine.ffprobe, "-v", "error", "-show_chapters",
                                               "-of", "json", str(output)], capture=True))["chapters"]
        self.assertEqual(len(chapters), 2)
        self.assertEqual(chapters[1]["tags"]["title"], "Второй")
        self.engine.run([self.engine.ffmpeg, "-v", "error", "-i", str(output),
                         "-map", "0:v:0", "-f", "null", "-"])
        def audio_hash(path):
            return self.engine.run([self.engine.ffmpeg, "-v", "error", "-i", str(path),
                                    "-map", "0:a:0", "-f", "hash", "-"], capture=True)
        self.assertEqual(audio_hash(self.source), audio_hash(output))

    def test_missing_encoder_fails_before_staging_and_does_not_affect_audio_mode(self):
        self.engine._video_encoders = set()
        output = self.folder / "keep.mkv"
        output.write_bytes(b"original")
        with self.assertRaisesRegex(ValueError, "libx264"):
            self.engine.convert(self.source, output, Options(video="visual", overwrite=True))
        self.assertEqual(output.read_bytes(), b"original")
        self.assertFalse(list(self.folder.glob("dtstomkv-*")))
        for mode in ("none", "copy"):
            self.engine.convert(self.source, self.folder / f"{mode}.mkv", Options(video=mode))

    def test_cli_uses_h264(self):
        output = self.folder / "cli.mkv"
        result = subprocess.run([sys.executable, "-m", "dtstomkv", "convert", str(self.source), str(output),
                                 "--video", "visual", "--resolution", "320x240"],
                                capture_output=True, **process_options())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("h264", {stream["codec_name"] for stream in self.engine.probe(output)["streams"]})
