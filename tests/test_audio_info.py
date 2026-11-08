"""Audio information uses the same bit-depth selection as conversion."""
import unittest
from unittest.mock import Mock

try:
    import tkinter
except ImportError:
    tkinter = None


@unittest.skipUnless(tkinter, "Tkinter required")
class AudioInfoTests(unittest.TestCase):
    def test_probed_and_automatic_depth(self):
        from dtstomkv.gui import App
        for fields, expected in (({}, "24 bit (Auto)"),
                                 ({"bits_per_raw_sample": "0", "bits_per_sample": 0}, "24 bit (Auto)"),
                                 ({"bits_per_sample": 16}, "16 bit"),
                                 ({"bits_per_raw_sample": "24", "bits_per_sample": 16}, "24 bit")):
            with self.subTest(fields=fields):
                app = App.__new__(App)
                app.s_in = Mock()
                app.s_in.get.return_value = "audio.dts"
                app.s_info = Mock()
                app.log_msg = Mock()
                engine = Mock()
                engine.probe.return_value = {"streams": [dict(codec_type="audio", codec_name="dts",
                                                              sample_rate="44100", channels=7, **fields)]}
                app.converter = Mock(return_value=engine)
                app.probe_simple()
                text = f"AUDIO: dts | 44100 Hz | 7 ch | {expected}"
                app.s_info.config.assert_called_once_with(text=text)
                app.log_msg.assert_called_once_with(text)
