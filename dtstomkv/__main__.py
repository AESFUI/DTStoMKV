"""python -m dtstomkv [gui|convert]."""
import argparse
import sys
import threading
from pathlib import Path

from .core import Cancelled, Converter, Options, VIDEO_PROFILES, default_output


def main(argv=None):
    parser = argparse.ArgumentParser(description="Audio → MKV (Linux / Windows)")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("gui", help="Открыть графический интерфейс")
    convert = commands.add_parser("convert", help="Конвертировать один файл")
    convert.add_argument("source")
    convert.add_argument("destination", nargs="?", help="Выходной файл или папка; по умолчанию <имя входного файла>.mkv")
    convert.add_argument("--video", choices=("none", "copy", "visual"), default="none")
    convert.add_argument("--video-profile", choices=tuple(VIDEO_PROFILES), default="h264",
                         help="Профиль визуализации: av1 (screen/24, cpu-used 6), hevc (slow/27), vp9 (screen/17), h264 (прежний)")
    convert.add_argument("--codec", choices=("lpcm", "flac", "ac3", "truehd"), default="lpcm")
    convert.add_argument("--bit-depth", choices=("Auto", "16", "24", "32"), default="Auto")
    convert.add_argument("--sample-rate", default="Auto")
    convert.add_argument("--normalize-channels", "--downmix-5-1", dest="normalize_channels",
                         action=argparse.BooleanOptionalAction, default=True,
                         help="Назначать каналы в 5.1/7.1, заполняя отсутствующие тишиной (включено по умолчанию; "
                              "--downmix-5-1 — прежнее имя опции)")
    convert.add_argument("--cue", default="")
    convert.add_argument("--album", default="")
    convert.add_argument("--title", default="Full Album")
    convert.add_argument("--resolution", default="1280x720")
    convert.add_argument("--bitrate", default="448k")
    convert.add_argument("--fps", type=int, default=10)
    convert.add_argument("--crf", type=int, default=23, help="CRF профиля h264 (по умолчанию 23)")
    convert.add_argument("--preset", default="ultrafast", help="Preset профиля h264 (по умолчанию ultrafast)")
    convert.add_argument("--flac-level", type=int, default=8)
    convert.add_argument("--overwrite", action="store_true")
    convert.add_argument("--ffmpeg")
    convert.add_argument("--ffprobe")
    convert.add_argument("--font")
    args = parser.parse_args(argv)
    if args.command in (None, "gui"):
        from .gui import main as gui_main
        gui_main()
        return 0
    stop = threading.Event()
    try:
        engine = Converter(args.ffmpeg, args.ffprobe, args.font, stop_event=stop)
        options = Options(**{key: getattr(args, key) for key in Options.__dataclass_fields__})
        destination = default_output(args.source) if args.destination is None else Path(args.destination).expanduser()
        if destination.is_dir():
            destination = default_output(args.source, destination)
        engine.convert(args.source, destination, options)
        return 0
    except (KeyboardInterrupt, Cancelled):
        stop.set()
        print("Обработка остановлена.", file=sys.stderr)
        return 130
    except (OSError, RuntimeError, ValueError) as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
