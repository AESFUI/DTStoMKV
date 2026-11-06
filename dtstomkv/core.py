"""Conversion engine shared by the GUI and CLI; never imports Tkinter."""
import colorsys
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import wave
from dataclasses import dataclass
from pathlib import Path

from .cue import parse_cue, write_chapters
from .runtime import executable, font_path, process_options

SUPPORTED_EXTS = (".mkv", ".aob", ".wav", ".dts", ".mlp", ".mka", ".ts", ".flac", ".ac3", ".m4a", ".truehd")


class Cancelled(RuntimeError):
    pass


def detect_bit_depth(stream):
    for key in ("bits_per_raw_sample", "bits_per_sample"):
        value = int(stream.get(key) or 0)
        if value > 0:
            return value
    return 24 if stream.get("codec_name") in ("mlp", "truehd", "flac", "dts") else 16


@dataclass(frozen=True)
class Options:
    video: str = "none"
    codec: str = "lpcm"
    bit_depth: str = "Auto"
    sample_rate: str = "Auto"
    cue: str = ""
    album: str = ""
    title: str = "Full Album"
    resolution: str = "1280x720"
    bitrate: str = "448k"
    fps: int = 10
    crf: int = 23
    preset: str = "ultrafast"
    flac_level: int = 8
    overwrite: bool = False


class Converter:
    def __init__(self, ffmpeg=None, ffprobe=None, font=None, log=print, stop_event=None):
        self.ffmpeg = executable("ffmpeg", ffmpeg)
        self.ffprobe = executable("ffprobe", ffprobe)
        self.font = font
        self.log = log
        self.stop_event = stop_event if stop_event is not None else threading.Event()

    def run(self, command, cwd=None, capture=False):
        if self.stop_event.is_set():
            raise Cancelled("Остановлено пользователем")
        with tempfile.TemporaryFile() as errors, tempfile.TemporaryFile() as output:
            process = subprocess.Popen(command, cwd=cwd, stdin=subprocess.DEVNULL,
                                       stdout=output if capture else subprocess.DEVNULL,
                                       stderr=errors, **process_options())
            try:
                while process.poll() is None:
                    if self.stop_event.wait(0.1):
                        raise Cancelled("Остановлено пользователем")
                if self.stop_event.is_set():
                    raise Cancelled("Остановлено пользователем")
                if process.returncode:
                    errors.seek(0, os.SEEK_END)
                    errors.seek(max(0, errors.tell() - 4000))
                    raise RuntimeError(errors.read().decode("utf-8", errors="replace"))
                if capture:
                    output.seek(0)
                    return output.read().decode("utf-8")
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()

    def probe(self, path):
        return json.loads(self.run([self.ffprobe, "-v", "error", "-of", "json",
                                    "-show_streams", "-show_format", str(path)], capture=True))

    def analyze(self, source, start, duration, work):
        # Bounded memory and analysis time, independent of the length of the album.
        import numpy as np
        self.run([self.ffmpeg, "-v", "error", "-y", "-ss", str(start), "-i", str(source),
                  "-t", str(min(30, duration)), "-map", "0:a:0", "-ac", "1", "-ar", "22050",
                  "-c:a", "pcm_s16le", "analysis.wav"], cwd=work)
        with wave.open(str(work / "analysis.wav")) as audio:
            samples = np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2").astype(float) / 32768
        rms = float(np.sqrt(np.mean(samples ** 2))) if len(samples) else 0
        if len(samples) < 2048:
            samples = np.pad(samples, (0, 2048 - len(samples)))
        frames = samples[:len(samples) // 2048 * 2048].reshape(-1, 2048)
        spectrum = np.abs(np.fft.rfft(frames * np.hanning(2048), axis=1))
        weights = spectrum.sum(axis=0)
        centroid = float(np.dot(weights, np.fft.rfftfreq(2048, 1 / 22050)) / max(weights.sum(), 1e-12))
        return centroid, rms

    def visualization(self, source, tracks, duration, options, work):
        shutil.copyfile(font_path(self.font), work / "font.ttf")
        (work / "album.txt").write_text(options.album or source.stem, encoding="utf-8")
        analysis = []
        for i, track in enumerate(tracks):
            end = tracks[i + 1]["start"] if i + 1 < len(tracks) else duration
            self.log(f"Анализ трека {track['num']}: {track['title']}")
            analysis.append(self.analyze(source, track["start"], end - track["start"], work))
        ranked = sorted(range(len(tracks)), key=lambda i: analysis[i][0])
        ranks = {index: rank for rank, index in enumerate(ranked)}
        filters = []
        for i, track in enumerate(tracks):
            start = 0 if i == 0 else track["start"]
            end = tracks[i + 1]["start"] if i + 1 < len(tracks) else duration
            hue = ranks[i] / max(1, len(tracks) - 1)
            rgb = colorsys.hsv_to_rgb(hue, min(1, max(0.4, analysis[i][1] * 5)), 0.4)
            color = "0x" + "".join(f"{round(v * 255):02x}" for v in rgb)
            enable = f"gte(t,{start})*lt(t,{end})"
            filters.append(f"drawbox=color={color}:t=fill:enable='{enable}'")
        filters.append("drawtext=fontfile=font.ttf:textfile=album.txt:expansion=none:"
                       "fontcolor=white:fontsize=48:x=(w-text_w)/2:y=(h-text_h)/2-80")
        for i, track in enumerate(tracks):
            start = 0 if i == 0 else track["start"]
            end = tracks[i + 1]["start"] if i + 1 < len(tracks) else duration
            title = f"{track['num']:02d}. {track['title']}" if len(tracks) > 1 else track["title"]
            (work / f"track{i}.txt").write_text(title, encoding="utf-8")
            filters.append(f"drawtext=fontfile=font.ttf:textfile=track{i}.txt:expansion=none:"
                           f"fontcolor=white:fontsize=36:x=(w-text_w)/2:y=(h-text_h)/2+20:"
                           f"enable='gte(t,{start})*lt(t,{end})'")
        total = f"{int(duration)//3600:02d}\\:{int(duration)%3600//60:02d}\\:{int(duration)%60:02d}"
        elapsed = r"%{pts\:hms}"
        filters.append(f"drawtext=fontfile=font.ttf:text='{elapsed} / {total}':"
                       "fontcolor=white:fontsize=36:x=(w-text_w)/2:y=(h-text_h)/2+80")
        (work / "visual.txt").write_text(",".join(filters), encoding="utf-8")

    def convert(self, source, destination, options=None):
        options = options or Options()
        source, destination = Path(source).expanduser().resolve(), Path(destination).expanduser().resolve()
        if not source.is_file():
            raise ValueError(f"Входной файл не найден: {source}")
        if source == destination or (destination.exists() and os.path.samefile(source, destination)):
            raise ValueError("Входной и выходной файлы совпадают")
        if destination.suffix.lower() != ".mkv":
            raise ValueError("Выходной файл должен иметь расширение .mkv")
        if destination.exists() and not options.overwrite:
            raise FileExistsError(f"Файл уже существует: {destination}")
        if options.video not in ("none", "copy", "visual"):
            raise ValueError("Неизвестный режим видео")
        if options.codec not in ("lpcm", "flac", "ac3", "truehd"):
            raise ValueError("Неизвестный аудиокодек")
        if options.bit_depth not in ("Auto", "16", "24", "32"):
            raise ValueError("Битность должна быть Auto, 16, 24 или 32")
        if not re.fullmatch(r"[1-9]\d{1,4}x[1-9]\d{1,4}", options.resolution):
            raise ValueError("Некорректное разрешение")
        if options.video == "visual" and any(int(n) % 2 for n in options.resolution.split("x")):
            raise ValueError("Ширина и высота должны быть чётными")
        if not 1 <= options.fps <= 120 or not 0 <= options.crf <= 51 or not 0 <= options.flac_level <= 12:
            raise ValueError("Проверьте FPS (1–120), CRF (0–51) и уровень FLAC (0–12)")
        self.log(f"Анализ: {source.name}")
        data = self.probe(source)
        audio = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
        if audio is None:
            raise ValueError("Нет аудиопотоков")
        depth = detect_bit_depth(audio) if options.bit_depth == "Auto" else int(options.bit_depth)
        rate = int(audio.get("sample_rate", 48000)) if options.sample_rate == "Auto" else int(options.sample_rate)
        if rate <= 0:
            raise ValueError("Частота должна быть положительной")
        channels = int(audio.get("channels", 2))
        codec = options.codec
        if codec == "lpcm":
            codec = "pcm_s32le" if depth >= 32 else "pcm_s24le" if depth >= 24 else "pcm_s16le"
        if codec in ("ac3", "truehd") and channels > 6:
            raise ValueError(f"{codec}: поддерживается максимум 6 каналов")
        if codec == "ac3" and rate not in (32000, 44100, 48000):
            raise ValueError("Для AC3 выберите частоту 32000, 44100 или 48000")
        if codec == "truehd" and rate not in (44100, 48000):
            raise ValueError("Для TrueHD выберите частоту 44100 или 48000")
        tracks = parse_cue(options.cue) if options.cue else []
        duration = float(data.get("format", {}).get("duration") or audio.get("duration") or 0)
        if (tracks or options.video == "visual") and duration <= 0:
            raise ValueError("Не удалось определить длительность аудио")
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Stage beside the destination: failure/cancellation never destroys an existing file.
        with tempfile.TemporaryDirectory(prefix="dtstomkv-", dir=destination.parent) as directory:
            work = Path(directory)
            command = [self.ffmpeg, "-v", "error", "-nostdin", "-y", "-i", str(source)]
            metadata_index = 1
            if options.video == "visual":
                visual_tracks = tracks or [{"num": 1, "title": options.title, "start": 0.0}]
                if tracks and tracks[-1]["start"] >= duration:
                    raise ValueError("CUE выходит за длительность аудио")
                self.visualization(source, visual_tracks, duration, options, work)
                command += ["-f", "lavfi", "-i", f"color=c=black:s={options.resolution}:r={options.fps}"]
                metadata_index = 2
            if tracks:
                write_chapters(work / "chapters.txt", tracks, duration)
                command += ["-f", "ffmetadata", "-i", "chapters.txt"]
            command += ["-map", "0:a:0", "-map_metadata", "0"]
            if tracks:
                command += ["-map_chapters", str(metadata_index)]
            else:
                command += ["-map_chapters", "0"]
            if options.video == "copy":
                command += ["-map", "0:v:0?", "-c:v", "copy"]
            elif options.video == "visual":
                command += ["-map", "1:v:0", "-filter_script:v", "visual.txt", "-c:v", "libx264",
                            "-preset", options.preset, "-tune", "stillimage", "-crf", str(options.crf),
                            "-pix_fmt", "yuv420p", "-t", str(duration)]
            command += ["-c:a", codec, "-ar", str(rate), "-ac", str(channels)]
            if codec == "flac":
                command += ["-compression_level", str(options.flac_level)]
            elif codec == "ac3":
                command += ["-b:a", options.bitrate]
            elif codec == "truehd":
                command += ["-strict", "experimental"]
            command += ["result.mkv"]
            self.log("Конвертация...")
            self.run(command, cwd=work)
            if self.stop_event.is_set():
                raise Cancelled("Остановлено пользователем")
            if options.overwrite:
                os.replace(work / "result.mkv", destination)
            else:
                # Exclusive creation prevents overwriting a file created during conversion.
                try:
                    os.link(work / "result.mkv", destination)
                except FileExistsError:
                    raise
                except OSError:
                    # FAT/exFAT and some network shares do not support hard links.
                    with destination.open("xb") as target:
                        try:
                            with (work / "result.mkv").open("rb") as result:
                                while chunk := result.read(1024 * 1024):
                                    if self.stop_event.is_set():
                                        raise Cancelled("Остановлено пользователем")
                                    target.write(chunk)
                        except BaseException:
                            target.close()
                            destination.unlink()
                            raise
        self.log(f"Сохранено: {destination}")
        return destination
