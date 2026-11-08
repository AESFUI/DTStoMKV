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
                                 ("5.1", 6), ("5.1(side)", 6), ("7.1", 8)):
            self.assertEqual(normalized_channel_map(layout, channels, "lpcm"), (channels, layout, ""))


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
class ChannelSignalTests(unittest.TestCase):
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
                    engine.convert(source, output)
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
