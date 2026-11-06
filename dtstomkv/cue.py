"""Single-file CUE sheets and FFmetadata chapter serialization."""
import re
from pathlib import Path


def parse_cue(path):
    raw = Path(path).read_bytes()
    try:
        content = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        content = raw.decode("cp1251")
    tracks, current, files = [], None, 0
    for line in content.splitlines():
        line = line.strip()
        if re.match(r"FILE\s", line, re.I):
            files += 1
            if files > 1:
                raise ValueError("CUE с несколькими FILE не поддерживается; выберите CUE для одного файла.")
        match = re.match(r"TRACK\s+(\d+)\s+AUDIO", line, re.I)
        if match:
            current = {"num": int(match[1]), "title": "", "start": None}
            tracks.append(current)
        elif current is not None:
            match = re.match(r'TITLE\s+"(.*)"', line, re.I)
            if match:
                current["title"] = match[1]
            match = re.match(r"INDEX\s+01\s+(\d+):(\d+):(\d+)", line, re.I)
            if match:
                minutes, seconds, frames = map(int, match.groups())
                if seconds >= 60 or frames >= 75:
                    raise ValueError("Некорректное время INDEX в CUE")
                current["start"] = minutes * 60 + seconds + frames / 75
    if not tracks or any(t["start"] is None for t in tracks):
        raise ValueError("CUE не содержит полных AUDIO-треков с INDEX 01")
    if any(a["start"] >= b["start"] for a, b in zip(tracks, tracks[1:])):
        raise ValueError("Время треков в CUE должно возрастать")
    return tracks


def metadata_escape(value):
    return str(value).replace("\\", "\\\\").replace("\n", "\\\n").replace("\r", "").replace("=", "\\=").replace(";", "\\;").replace("#", "\\#")


def write_chapters(path, tracks, duration):
    if tracks[-1]["start"] >= duration:
        raise ValueError("CUE выходит за длительность аудио")
    lines = [";FFMETADATA1"]
    for i, track in enumerate(tracks):
        end = tracks[i + 1]["start"] if i + 1 < len(tracks) else duration
        lines += ["[CHAPTER]", "TIMEBASE=1/1000", f"START={round(track['start'] * 1000)}",
                  f"END={round(end * 1000)}", f"title={metadata_escape(track['title'])}"]
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
