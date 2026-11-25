"""AC3-in-WAV is remuxed intact, including with CUE and generated video."""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from dtstomkv.core import Converter, Options
from dtstomkv.runtime import font_path, process_options


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
class Ac3CopyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="AC3 WAV тест ")
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.messages = []
        self.engine = Converter(log=self.messages.append)
        payload = self.folder / "audio.spdif"
        signal = "|".join(f"0.1*sin(2*PI*{frequency}*t)" for frequency in (220, 330, 440, 550, 660))
        self.engine.run([self.engine.ffmpeg, "-v", "error", "-f", "lavfi", "-i",
                         f"aevalsrc={signal}:s=44100:d=1.1:c=5.0(side)",
                         "-c:a", "ac3", "-b:a", "448k", "-f", "spdif", str(payload)])
        self.source = self.folder / "Dolby Digital.wav"
        # Dolby Digital CD WAVs carry compressed frames in a stereo PCM wrapper.
        with wave.open(str(self.source), "wb") as output:
            output.setparams((2, 2, 44100, 0, "NONE", "not compressed"))
            output.writeframes(payload.read_bytes())
        stream = self.engine.probe(self.source)["streams"][0]
        self.assertEqual((stream["codec_name"], stream["channels"], stream["channel_layout"]),
                         ("ac3", 5, "5.0(side)"))
        self.cue = self.folder / "album.cue"
        self.cue.write_text('TRACK 01 AUDIO\nTITLE "Первый"\nINDEX 01 00:00:00\n'
                            'TRACK 02 AUDIO\nTITLE "Второй"\nINDEX 01 00:00:30\n', encoding="utf-8")

    def packet_hash(self, path):
        return self.engine.run([self.engine.ffmpeg, "-v", "error", "-i", str(path),
                                "-map", "0:a:0", "-c:a", "copy", "-f", "hash", "-"], capture=True)

    def test_preserves_ac3_packets_and_channels_in_all_video_modes(self):
        expected_hash = self.packet_hash(self.source)
        original_visualization = self.engine.visualization
        labels = []

        def inspect_visualization(*args):
            labels.append(args[5])
            return original_visualization(*args)

        for mode in ("none", "copy", "visual"):
            with self.subTest(mode=mode):
                if mode == "visual":
                    try:
                        font_path()
                    except RuntimeError as error:
                        self.skipTest(str(error))
                output = self.folder / f"{mode}.mkv"
                self.messages.clear()
                with patch.object(self.engine, "visualization", side_effect=inspect_visualization):
                    self.engine.convert(self.source, output, Options(
                        video=mode, codec="lpcm", bit_depth="32", sample_rate="48000",
                        bitrate="192k", cue=str(self.cue), resolution="320x240"))
                info = self.engine.probe(output)
                audio = next(stream for stream in info["streams"] if stream["codec_type"] == "audio")
                self.assertEqual((audio["codec_name"], audio["sample_rate"], audio["channels"],
                                  audio["channel_layout"], audio["bit_rate"]),
                                 ("ac3", "44100", 5, "5.0(side)", "448000"))
                self.assertEqual(self.packet_hash(output), expected_hash)
                self.assertTrue(any(message.startswith("Выходной звук: AC3") for message in self.messages))
                self.assertFalse(any(message.startswith("Назначение каналов:") for message in self.messages))
                chapters = json.loads(self.engine.run([
                    self.engine.ffprobe, "-v", "error", "-show_chapters", "-of", "json", str(output)
                ], capture=True))["chapters"]
                self.assertEqual([chapter["tags"]["title"] for chapter in chapters], ["Первый", "Второй"])
                if mode == "visual":
                    self.assertEqual(labels, ["AC3 -> AC3 · 44.1 kHz · 5.0(side)"])
                    self.assertIn("h264", {stream["codec_name"] for stream in info["streams"]})
                    self.engine.run([self.engine.ffmpeg, "-v", "error", "-i", str(output),
                                     "-map", "0:v:0", "-f", "null", "-"])

    def test_copy_can_be_disabled_for_explicit_conversion(self):
        output = self.folder / "lpcm.mkv"
        self.engine.convert(self.source, output, Options(copy_ac3=False, bit_depth="24", sample_rate="48000"))
        audio = self.engine.probe(output)["streams"][0]
        self.assertEqual((audio["codec_name"], audio["sample_rate"], audio["channels"]),
                         ("pcm_s24le", "48000", 6))

    def test_short_video_duration_does_not_truncate_copied_audio(self):
        try:
            font_path()
        except RuntimeError as error:
            self.skipTest(str(error))
        data = self.engine.probe(self.source)
        data["format"]["duration"] = "0.4"
        output = self.folder / "short-video.mkv"
        with patch.object(self.engine, "probe", return_value=data):
            self.engine.convert(self.source, output, Options(video="visual", resolution="320x240"))
        self.assertEqual(self.packet_hash(self.source), self.packet_hash(output))
        self.assertGreater(float(self.engine.probe(output)["format"]["duration"]), 1)

    def test_cli_copies_by_default_and_accepts_no_copy_ac3(self):
        for extra, codec, channels in (([], "ac3", 5), (["--no-copy-ac3"], "pcm_s24le", 6)):
            with self.subTest(extra=extra):
                output = self.folder / f"cli-{codec}.mkv"
                result = subprocess.run([
                    sys.executable, "-m", "dtstomkv", "convert", str(self.source), str(output),
                    "--bit-depth", "24", "--sample-rate", "48000", *extra
                ], capture_output=True, **process_options())
                self.assertEqual(result.returncode, 0, result.stderr)
                audio = self.engine.probe(output)["streams"][0]
                self.assertEqual((audio["codec_name"], audio["channels"]), (codec, channels))
                if codec == "ac3":
                    self.assertEqual(self.packet_hash(self.source), self.packet_hash(output))
