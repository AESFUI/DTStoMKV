"""Run under a display (or xvfb-run); asserts construction of all three tabs."""
import tkinter as tk
from pathlib import Path

from dtstomkv.gui import App
from dtstomkv.core import VIDEO_PROFILES

root = tk.Tk()
root.withdraw()
app = App(root)
root.update()
assert len(app.nb.tabs()) == 3
for prefix in ("s", "v", "b"):
    assert app.options(prefix).codec == "lpcm"
    assert app.options(prefix).video_profile == "h264"
    assert app.options(prefix).normalize_channels is True
    app.normalize_channels.set(False)
    assert app.options(prefix).normalize_channels is False
    app.normalize_channels.set(True)
    for key, profile in VIDEO_PROFILES.items():
        getattr(app, prefix + "_video_profile").set(profile.label)
        assert app.options(prefix).video_profile == key
for prefix in ("s", "v"):
    getattr(app, prefix + "_in").set(str(Path("music") / "Альбом.dts"))
    assert Path(getattr(app, prefix + "_out").get()) == Path("music/Альбом.mkv")
app.b_video_mode.set("visual")
app.toggle_batch_visual()
root.update()
assert app.b_vis_frame.winfo_manager() == "grid"
app.b_video_mode.set("none")
app.toggle_batch_visual()
assert app.b_vis_frame.winfo_manager() == ""
app.on_close()
