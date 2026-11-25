"""Audio information uses the same bit-depth selection as conversion."""
import queue
import threading
import unittest
from unittest.mock import Mock, patch

try:
    import tkinter
except ImportError:
    tkinter = None


@unittest.skipUnless(tkinter, "Tkinter required")
class AudioInfoTests(unittest.TestCase):
    def make_app(self):
        from dtstomkv.gui import App
        app = App.__new__(App)
        app.s_in = Mock()
        app.s_in.get.return_value = "audio.dts"
        app.s_info = Mock()
        app.root = Mock()
        app.log = Mock()
        app.events = queue.Queue()
        app.stop_event = threading.Event()
        app.is_running = app.closing = False
        app.clear_log = Mock()
        app.toggle_buttons = Mock()
        return app

    def finish_task(self, app):
        events = []
        while True:
            event = app.events.get(timeout=5)
            events.append(event)
            if event[0] == "done":
                break
        for event in events:
            app.events.put(event)
        app.pump()
        self.assertFalse(app.is_running)

    def test_probed_and_automatic_depth(self):
        for fields, expected in (({}, "24 bit (Auto)"),
                                 ({"bits_per_raw_sample": "0", "bits_per_sample": 0}, "24 bit (Auto)"),
                                 ({"bits_per_sample": 16}, "16 bit"),
                                 ({"bits_per_raw_sample": "24", "bits_per_sample": 16}, "24 bit")):
            with self.subTest(fields=fields):
                app = self.make_app()
                engine = Mock()
                engine.probe.return_value = {"streams": [dict(codec_type="audio", codec_name="dts",
                                                              sample_rate="44100", channels=7, **fields)]}
                app.converter = Mock(return_value=engine)
                app.probe_simple()
                self.finish_task(app)
                text = f"AUDIO: dts | 44100 Hz | 7 ch | {expected}"
                app.s_info.config.assert_called_once_with(text=text)
                app.log.insert.assert_called_once_with("end", text + "\n")

    def test_probe_does_not_block_gui_and_ignores_stale_result(self):
        app = self.make_app()
        entered, release = threading.Event(), threading.Event()
        def probe(path):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("Test did not release probe")
            return {"streams": []}
        app.converter = Mock(return_value=Mock(probe=probe))
        app.probe_simple()
        try:
            self.assertTrue(entered.wait(5))
            self.assertTrue(app.is_running)
            app.s_info.config.assert_not_called()
            app.probe_simple()  # A second click must not launch another process.
            self.assertEqual(app.converter.call_count, 1)
            app.s_in.get.return_value = "another.dts"
        finally:
            release.set()
            self.finish_task(app)
        app.s_info.config.assert_not_called()

    def test_probe_error_is_displayed_on_gui_thread(self):
        app = self.make_app()
        app.converter = Mock(return_value=Mock(probe=Mock(side_effect=RuntimeError("bad input"))))
        with patch("dtstomkv.gui.messagebox.showerror") as showerror:
            app.probe_simple()
            self.finish_task(app)
            showerror.assert_called_once_with("Ошибка", "bad input")
        app.s_info.config.assert_not_called()
