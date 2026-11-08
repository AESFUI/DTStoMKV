"""List DTS files whose first audio stream is not 5.1."""
import argparse
import json
import subprocess
import sys
from pathlib import Path

from dtstomkv.runtime import executable, process_options


def is_5_1(stream):
    return stream.get("channels") == 6 and stream.get("channel_layout") in ("5.1", "5.1(side)")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Список DTS-файлов с раскладкой каналов, отличной от 5.1")
    parser.add_argument("source", type=Path, help="Входная папка (включая подпапки)")
    parser.add_argument("output", type=Path, help="Выходной текстовый файл UTF-8")
    parser.add_argument("--ffprobe", help="Путь к ffprobe")
    args = parser.parse_args(argv)
    source, output = args.source.expanduser().resolve(), args.output.expanduser().resolve()
    try:
        if not source.is_dir():
            raise ValueError(f"Входная папка не найдена: {source}")
        ffprobe = executable("ffprobe", args.ffprobe)
        files = sorted((path for path in source.rglob("*") if path.is_file() and path.suffix.lower() == ".dts"),
                       key=lambda path: str(path))
        if any(output == path or (output.exists() and output.samefile(path)) for path in files):
            raise ValueError("Выходной файл совпадает с одним из исходных DTS-файлов")
        groups, errors = {}, 0
        for path in files:
            try:
                result = subprocess.run(
                    [ffprobe, "-v", "error", "-select_streams", "a:0", "-show_entries",
                     "stream=channels,channel_layout", "-of", "json", str(path)],
                    stdin=subprocess.DEVNULL, capture_output=True, text=True,
                    encoding="utf-8", errors="replace", check=True, **process_options())
                streams = json.loads(result.stdout).get("streams", [])
                if not streams:
                    raise ValueError("Нет аудиопотока")
                if not streams[0].get("channels"):
                    raise ValueError("Не удалось определить число каналов")
                if not is_5_1(streams[0]):
                    layout = streams[0].get("channel_layout") or "Неизвестная раскладка"
                    key = (layout, streams[0]["channels"])
                    groups.setdefault(key, []).append(str(path))
                    if not streams[0].get("channel_layout"):
                        print(f"Неизвестная раскладка, файл включён в список: {path}", file=sys.stderr)
            except (OSError, ValueError, subprocess.CalledProcessError) as error:
                errors += 1
                detail = error.stderr.strip() if isinstance(error, subprocess.CalledProcessError) else str(error)
                print(f"Ошибка проверки {path}: {detail}", file=sys.stderr)
        output.parent.mkdir(parents=True, exist_ok=True)
        sections = [f"[{layout} | {channels} ch]\n" + "\n".join(groups[(layout, channels)]) + "\n"
                    for layout, channels in sorted(groups)]
        output.write_text("\n".join(sections), encoding="utf-8")
        count = sum(len(paths) for paths in groups.values())
        print(f"Проверено: {len(files)}; не 5.1: {count}; ошибок: {errors}. Список: {output}")
        return 1 if errors else 0
    except (OSError, RuntimeError, ValueError) as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
