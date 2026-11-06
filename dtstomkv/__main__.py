"""python -m dtstomkv [gui|convert]."""
import argparse
import sys
import threading

from .core import Cancelled, Converter, Options


def main(argv=None):
    parser = argparse.ArgumentParser(description="Audio → MKV (Linux / Windows)")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("gui", help="Открыть графический интерфейс")
    convert = commands.add_parser("convert", help="Конвертировать один файл")
    convert.add_argument("source")
    convert.add_argument("destination")
    convert.add_argument("--video", choices=("none", "copy", "visual"), default="none")
    convert.add_argument("--codec", choices=("lpcm", "flac", "ac3", "truehd"), default="lpcm")
    convert.add_argument("--bit-depth", choices=("Auto", "16", "24", "32"), default="Auto")
    convert.add_argument("--sample-rate", default="Auto")
    convert.add_argument("--cue", default="")
    convert.add_argument("--album", default="")
    convert.add_argument("--title", default="Full Album")
    convert.add_argument("--resolution", default="1280x720")
    convert.add_argument("--bitrate", default="448k")
    convert.add_argument("--fps", type=int, default=10)
    convert.add_argument("--crf", type=int, default=23)
    convert.add_argument("--preset", default="ultrafast")
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
        engine.convert(args.source, args.destination, options)
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
