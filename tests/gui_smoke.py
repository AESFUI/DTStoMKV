"""Run under a display (or xvfb-run); asserts construction of all three tabs."""
import tkinter as tk

from dtstomkv.gui import App

root = tk.Tk()
root.withdraw()
app = App(root)
root.update()
assert len(app.nb.tabs()) == 3
for prefix in ("s", "v", "b"):
    assert app.options(prefix).codec == "lpcm"
app.on_close()
