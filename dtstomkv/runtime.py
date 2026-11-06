"""The only OS-dependent defaults used by the application."""
import os
import shutil
from pathlib import Path


def executable(name, explicit=None):
    value = explicit or os.environ.get(f"DTSTOMKV_{name.upper()}") or name
    found = shutil.which(str(value))
    if not found:
        raise RuntimeError(f"Не найден {value}. Установите FFmpeg и добавьте его в PATH "
                           f"или задайте DTSTOMKV_{name.upper()}.")
    return str(Path(found).resolve())


def font_path(explicit=None):
    value = explicit or os.environ.get("DTSTOMKV_FONT")
    if value:
        path = Path(value).expanduser()
        if not path.is_file():
            raise ValueError(f"Шрифт не найден: {path}")
        return path
    candidates = [
        Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/arial.ttf",
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/dejavu-sans-fonts/DejaVuSans.ttf"),
        Path("/usr/share/fonts/TTF/DejaVuSans.ttf"),
    ]
    for path in candidates:
        if path.is_file():
            return path
    raise RuntimeError("Не найден шрифт. Задайте путь к TTF через DTSTOMKV_FONT или --font.")


def process_options():
    return {"creationflags": 0x08000000} if os.name == "nt" else {}
