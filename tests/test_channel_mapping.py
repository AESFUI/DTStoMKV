"""Verify speaker assignment and silence using distinct signals per channel."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from dtstomkv.core import Converter, Options, normalized_channel_map
from dtstomkv.runtime import process_options


class ChannelMapTests(unittest.TestCase):
    def test_unknown_and_unsupported_maps_are_not_guessed(self):
        for layout, channels in ((None, 5), ("unknown", 7), ("5.0", 6), ("7.1(wide)", 8)):
            with self.subTest(layout=layout, channels=channels):
                with self.assertRaisesRegex(ValueError, "Нельзя безопасно назначить"):
                    normalized_channel_map(layout, channels, "lpcm")

    def test_standard_layouts_are_preserved(self):
        for layout, channels in ((None, 1), (None, 2), ("mono", 1), ("stereo", 2),
                                 ("5.1", 6), ("5.1(side)", 6)):
            self.assertEqual(normalized_channel_map(layout, channels, "lpcm"), (channels, layout, ""))
        self.assertEqual(normalized_channel_map("7.1", 8, "lpcm", 8), (8, "7.1", ""))


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
class ChannelSignalTests(unittest.TestCase):
    def test_6_1_mapping_past_thirteen_seconds(self):
        import numpy as np
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            engine = Converter(log=lambda message: None)
            def decode(path, channels):
                result = subprocess.run([engine.ffmpeg, "-v", "error", "-i", str(path),
                                         "-map", "0:a:0", "-c:a", "pcm_f32le", "-f", "f32le", "-"],
                                        check=True, capture_output=True, **process_options())
                return np.frombuffer(result.stdout, dtype="<f4").reshape(-1, channels)
            for rate in (44100, 48000):
                with self.subTest(rate=rate):
                    source, output = folder / f"{rate}.wav", folder / f"{rate}.mkv"
                    tones = "|".join(f"0.05*sin(2*PI*{220 + 110 * i}*t)" for i in range(7))
                    engine.run([engine.ffmpeg, "-v", "error", "-f", "lavfi", "-i",
                                f"aevalsrc={tones}:s={rate}:d=20:c=6.1", "-c:a", "pcm_s24le", str(source)])
                    engine.convert(source, output, Options(max_channels=8))
                    original, converted = decode(source, 7), decode(output, 8)
                    self.assertEqual(len(converted), 20 * rate)
                    for destination, origin in ((0, 0), (1, 1), (2, 2), (3, 3), (6, 5), (7, 6)):
                        np.testing.assert_array_equal(converted[:, destination], original[:, origin])
                    for destination in (4, 5):
                        np.testing.assert_allclose(converted[:, destination], original[:, 4] / 2 ** 0.5,
                                                   rtol=0, atol=3e-7)
                    compatible = folder / f"{rate}-5.1.mkv"
                    engine.convert(source, compatible)
                    mixed = decode(compatible, 6)
                    self.assertEqual(len(mixed), 20 * rate)
                    np.testing.assert_array_equal(mixed[:, :4], original[:, :4])
                    for destination, surround in ((4, 5), (5, 6)):
                        expected = (original[:, surround] + original[:, 4] / 2 ** 0.5) / (1 + 1 / 2 ** 0.5)
                        np.testing.assert_allclose(mixed[:, destination], expected, rtol=0, atol=3e-7)

    def test_output_speakers_and_silent_channels(self):
        import numpy as np
        # Each input channel contains its own constant signal, 0.03 * (index + 1).
        # Expectations use those indices, with zero for silent channels.
        bc4 = 4 / 2 ** 0.5
        bc5 = 5 / 2 ** 0.5
        bc6 = 6 / 2 ** 0.5
        cases = (
            ("5.0", 5, (1, 2, 3, 0, 4, 5)),
            ("5.0(side)", 5, (1, 2, 3, 0, 4, 5)),
            ("quad", 4, (1, 2, 0, 0, 3, 4)),
            ("quad(side)", 4, (1, 2, 0, 0, 3, 4)),
            ("4.0", 4, (1, 2, 3, 0, bc4, bc4)),
            ("6.1", 7, (1, 2, 3, 4, bc5, bc5, 6, 7)),
            ("6.1(back)", 7, (1, 2, 3, 4, 7 / 2 ** 0.5, 7 / 2 ** 0.5, 5, 6)),
            ("6.0", 6, (1, 2, 3, 0, bc4, bc4, 5, 6)),
            ("hexagonal", 6, (1, 2, 3, 0, bc6, bc6, 4, 5)),
            ("7.0", 7, (1, 2, 3, 0, 4, 5, 6, 7)),
        )
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            engine = Converter(log=lambda message: None)
            for layout, channels, expected in cases:
                with self.subTest(layout=layout):
                    source, output = folder / f"{layout}.wav", folder / f"{layout}.mkv"
                    values = "|".join(str(0.03 * (i + 1)) for i in range(channels))
                    engine.run([engine.ffmpeg, "-v", "error", "-f", "lavfi", "-i",
                                f"aevalsrc={values}:s=48000:d=0.1:c={layout}",
                                "-c:a", "pcm_s24le", str(source)])
                    engine.convert(source, output, Options(max_channels=8))
                    decoded = subprocess.run([engine.ffmpeg, "-v", "error", "-i", str(output),
                                              "-map", "0:a:0", "-c:a", "pcm_f32le", "-f", "f32le", "-"],
                                             check=True, capture_output=True, **process_options())
                    samples = np.frombuffer(decoded.stdout, dtype="<f4").reshape(-1, len(expected))
                    np.testing.assert_allclose(samples, np.broadcast_to(np.array(expected) * 0.03, samples.shape),
                                               rtol=0, atol=3e-7)
                    # Disabling the option must retain every original channel signal.
                    original = folder / f"{layout}-original.mkv"
                    engine.convert(source, original, Options(normalize_channels=False))
                    self.assertEqual(engine.probe(original)["streams"][0]["channels"], channels)

    def test_5_1_surround_mix_and_correlated_peaks(self):
        import numpy as np
        gain = 1 / 2 ** 0.5
        cases = (
            ("6.1", list(range(1, 8)), [1, 2, 3, 4, (6 + gain * 5) / (1 + gain), (7 + gain * 5) / (1 + gain)]),
            ("6.1(back)", list(range(1, 8)), [1, 2, 3, 4, (5 + gain * 7) / (1 + gain), (6 + gain * 7) / (1 + gain)]),
            ("7.1", list(range(1, 9)), [1, 2, 3, 4, 6, 7]),
            ("6.1", [39.6] * 7, [39.6] * 6),
            ("7.1", [39.6] * 8, [39.6] * 6),
        )
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            engine = Converter(log=lambda message: None)
            for index, (layout, values, expected) in enumerate(cases):
                with self.subTest(layout=layout, values=values):
                    source, output = folder / f"{index}.wav", folder / f"{index}.mkv"
                    # These constants avoid a repeated 0x47 pattern mistaken for MPEG-TS by probing.
                    constants = "|".join(str(value * 0.025) for value in values)
                    engine.run([engine.ffmpeg, "-v", "error", "-f", "lavfi", "-i",
                                f"aevalsrc={constants}:s=48000:d=0.1:c={layout}",
                                "-c:a", "pcm_s24le", str(source)])
                    engine.convert(source, output)
                    decoded = subprocess.run([engine.ffmpeg, "-v", "error", "-i", str(output),
                                              "-map", "0:a:0", "-c:a", "pcm_f32le", "-f", "f32le", "-"],
                                             check=True, capture_output=True, **process_options())
                    samples = np.frombuffer(decoded.stdout, dtype="<f4").reshape(-1, 6)
                    np.testing.assert_allclose(samples, np.broadcast_to(np.array(expected) * 0.025, samples.shape),
                                               rtol=0, atol=3e-7)
