"""Run under a display (or xvfb-run); asserts construction of all three tabs."""
import tkinter as tk
from pathlib import Path

from dtstomkv.gui import App

root = tk.Tk()
root.withdraw()
app = App(root)
root.update()
assert len(app.nb.tabs()) == 3
for prefix in ("s", "v", "b"):
    assert app.options(prefix).codec == "lpcm"
    assert not hasattr(app, prefix + "_video_profile")
    assert app.options(prefix).normalize_channels is True
    assert app.options(prefix).max_channels == 6
    app.max_channels.set("7.1")
    assert app.options(prefix).max_channels == 8
    app.max_channels.set("5.1")
    app.normalize_channels.set(False)
    assert app.options(prefix).normalize_channels is False
    app.normalize_channels.set(True)
for prefix in ("s", "v"):
    getattr(app, prefix + "_in").set(str(Path("music") / "Альбом.dts"))
    assert Path(getattr(app, prefix + "_out").get()) == Path("music/Альбом.mkv")
app.on_close()
