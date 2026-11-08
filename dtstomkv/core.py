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


@dataclass(frozen=True)
class VideoProfile:
    label: str
    encoder: str
    arguments: tuple[str, ...]


VIDEO_PROFILES = {
    "av1": VideoProfile("AV1 — libaom, screen, CRF 24, cpu-used 6", "libaom-av1",
                        ("-crf", "24", "-b:v", "0", "-cpu-used", "6", "-row-mt", "1",
                         "-threads:v", "0", "-aom-params", "tune-content=screen")),
    "hevc": VideoProfile("HEVC / H.265 — x265 slow, CRF 27", "libx265",
                         ("-preset", "slow", "-crf", "27")),
    "vp9": VideoProfile("VP9 — libvpx, screen, CRF 17", "libvpx-vp9",
                        ("-crf", "17", "-b:v", "0", "-tune-content", "screen")),
    "h264": VideoProfile("H.264 — x264 ultrafast, stillimage, CRF 23 (прежний)", "libx264", ()),
}


class Cancelled(RuntimeError):
    pass


def default_output(source, directory=None):
    source = Path(source).expanduser()
    return (Path(directory).expanduser() if directory is not None else source.parent) / source.with_suffix(".mkv").name


class EncodingProgress:
    """Tail FFmpeg's progress file without blocking pipes or platform-specific I/O."""
    def __init__(self, path, duration, log):
        self.path, self.duration, self.log = Path(path), duration, log
        self.offset, self.pending, self.values = 0, b"", {}
        self.seconds, self.speed = 0.0, ""

    @staticmethod
    def timestamp(seconds):
        seconds = max(0, int(seconds))
        return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"

    def report(self, complete=False):
        if complete:
            status = "100.0%" if self.duration > 0 else "завершено"
        else:
            status = f"{min(99.9, 100 * self.seconds / self.duration):.1f}%" if self.duration > 0 else ""
        timing = self.timestamp(self.seconds)
        if self.duration > 0:
            timing += " / " + self.timestamp(self.duration)
        parts = [part for part in (status, timing, f"скорость {self.speed}" if self.speed else "") if part]
        self.log("Кодирование: " + " · ".join(parts))

    def poll(self):
        try:
            with self.path.open("rb") as stream:
                stream.seek(self.offset)
                chunk = stream.read()
                self.offset = stream.tell()
        except FileNotFoundError:
            return
        lines = (self.pending + chunk).split(b"\n")
        self.pending = lines.pop()
        for line in lines:
            key, separator, value = line.decode("ascii", errors="replace").strip().partition("=")
            if not separator:
                continue
            self.values[key] = value
            if key != "progress":
                continue
            try:
                # Despite its name, FFmpeg's legacy out_time_ms is also in microseconds.
                micros = int(self.values.get("out_time_us", self.values.get("out_time_ms", "0")))
                self.seconds = max(self.seconds, micros / 1_000_000)
            except ValueError:  # N/A before the first encoded frame.
                pass
            speed = self.values.get("speed", "").strip()
            if speed and speed != "N/A":
                self.speed = speed
            if value == "continue":
                self.report()
            self.values.clear()

    def complete(self):
        if self.duration > 0:
            self.seconds = self.duration
        self.report(complete=True)


def detect_bit_depth(stream):
    for key in ("bits_per_raw_sample", "bits_per_sample"):
        value = int(stream.get(key) or 0)
        if value > 0:
            return value
    return 24 if stream.get("codec_name") in ("mlp", "truehd", "flac", "dts") else 16


def normalized_channel_map(layout, channels, codec):
    """Return an explicit speaker map, preserving signals and padding with silence."""
    if not layout and channels in (1, 2):
        return channels, layout, ""
    if (layout, channels) in (("mono", 1), ("stereo", 2), ("5.1", 6),
                              ("5.1(side)", 6), ("7.1", 8)):
        return channels, layout, ""
    # A missing speaker map cannot safely be inferred from the channel count.
    layouts = {
        "2.1": "FL FR LFE", "3.0": "FL FR FC", "3.0(back)": "FL FR BC",
        "3.1": "FL FR FC LFE", "4.0": "FL FR FC BC", "4.1": "FL FR FC LFE BC",
        "quad": "FL FR BL BR", "quad(side)": "FL FR SL SR",
        "5.0": "FL FR FC BL BR", "5.0(side)": "FL FR FC SL SR",
        "6.0": "FL FR FC BC SL SR", "hexagonal": "FL FR FC BL BR BC",
        "6.1": "FL FR FC LFE BC SL SR", "6.1(back)": "FL FR FC LFE BL BR BC",
        "7.0": "FL FR FC BL BR SL SR",
    }
    speakers = layouts.get(layout, "").split()
    if len(speakers) != channels:
        raise ValueError(f"Нельзя безопасно назначить каналы для раскладки {layout or 'неизвестно'} "
                         f"({channels} ch). Отключите нормализацию каналов, чтобы сохранить исходник.")
    surround = set(speakers) & {"BL", "BR", "SL", "SR"}
    target = "7.1" if channels > 6 or ("BC" in speakers and surround) else "5.1"
    if target == "5.1" and codec == "truehd":
        target = "5.1(side)"
    outputs = ("FL FR FC LFE BL BR SL SR" if target == "7.1" else
               "FL FR FC LFE SL SR" if target == "5.1(side)" else "FL FR FC LFE BL BR").split()
    assignments = {speaker: speaker for speaker in speakers if speaker in outputs}
    if target != "7.1":
        for rear, side in (("BL", "SL"), ("BR", "SR")):
            destination, alternate = (side, rear) if target == "5.1(side)" else (rear, side)
            if alternate in speakers:
                assignments[destination] = alternate
    if "BC" in speakers:
        if target == "7.1" and "BL" in speakers:
            assignments.update(SL="BL", SR="BR")
        left, right = ("SL", "SR") if target == "5.1(side)" else ("BL", "BR")
        assignments.update({left: "0.7071067811865476*BC", right: "0.7071067811865476*BC"})
    # Refer to a real input channel with a zero coefficient for silent outputs.
    pan = "pan=" + target + "|" + "|".join(
        f"{speaker}={assignments.get(speaker, '0*FL')}" for speaker in outputs)
    return len(outputs), target, pan


def audio_format(codec, rate, channels, depth=None, layout=None):
    name = {"lpcm": "LPCM", "ac3": "AC3", "truehd": "TrueHD"}.get(codec, codec.upper())
    if codec.startswith("pcm_"):
        name = "LPCM"
    parts = [name]
    if depth and (name in ("LPCM", "FLAC", "TrueHD", "MLP")):
        parts.append(f"{depth} bit")
    parts.extend((f"{rate / 1000:g} kHz", layout or f"{channels} ch"))
    return " · ".join(parts)


def fit_text(text, font, size, width, lines=2):
    """Measure the actual TTF glyphs; keep all text within at most two lines."""
    from PIL import ImageFont
    text = " ".join(text.split())
    for current in range(size, 0, -1):
        face = ImageFont.truetype(str(font), current)
        if face.getlength(text) <= width:
            return text, current
        if lines == 1:
            continue
        breaks = [i for i, char in enumerate(text) if char == " "]
        # Unbroken titles (catalogue numbers, CJK, etc.) can wrap between glyphs.
        if not breaks:
            breaks = range(1, len(text))
        if breaks:
            split = min(breaks, key=lambda i: max(face.getlength(text[:i].rstrip()),
                                                 face.getlength(text[i:].lstrip())))
            first, second = text[:split].rstrip(), text[split:].lstrip()
            if max(face.getlength(first), face.getlength(second)) <= width:
                return first + "\n" + second, current
    raise ValueError("Название слишком длинное для выбранного разрешения")


def sound_features(samples, sample_rate=22050):
    import numpy as np
    rms = float(np.sqrt(np.mean(samples ** 2))) if len(samples) else 0.0
    peak = float(np.max(np.abs(samples))) if len(samples) else 0.0
    if len(samples) < 2048:
        samples = np.pad(samples, (0, 2048 - len(samples)))
    frames = samples[:len(samples) // 2048 * 2048].reshape(-1, 2048)
    spectrum = np.abs(np.fft.rfft(frames * np.hanning(2048), axis=1))
    power = (spectrum ** 2).mean(axis=0)
    frequencies = np.fft.rfftfreq(2048, 1 / sample_rate)
    total = max(float(power.sum()), 1e-12)
    centroid = float(np.dot(power, frequencies) / total)
    bass = float(power[frequencies < 250].sum() / total)
    treble = float(power[frequencies > 3000].sum() / total)
    normalized = spectrum / np.maximum(spectrum.sum(axis=1, keepdims=True), 1e-12)
    flux = float(np.maximum(np.diff(normalized, axis=0), 0).sum(axis=1).mean()) if len(frames) > 1 else 0.0
    return dict(centroid=centroid, bass=bass, treble=treble, flux=flux,
                rms=rms, crest=peak / max(rms, 1e-12))


def sound_color(features):
    # Absolute acoustic mapping: a track keeps its color in any album/order.
    import math
    brightness = min(1, math.log1p(features["centroid"] / 100) / math.log(81))
    hue = (0.04 + 0.72 * brightness + 0.13 * features["treble"]
           - 0.12 * features["bass"] + 0.10 * features["flux"]) % 1
    saturation = min(0.95, 0.55 + 0.25 * features["bass"] + 0.15 * features["flux"])
    # Dark background preserves contrast with white text, even for loud tracks.
    value = min(0.43, 0.20 + 0.16 * min(1, features["rms"] * 5)
                + 0.07 * min(1, features["crest"] / 12))
    return "0x" + "".join(f"{round(v * 255):02x}" for v in colorsys.hsv_to_rgb(hue, saturation, value))


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
    video_profile: str = "h264"
    normalize_channels: bool = True


class Converter:
    def __init__(self, ffmpeg=None, ffprobe=None, font=None, log=print, stop_event=None):
        self.ffmpeg = executable("ffmpeg", ffmpeg)
        self.ffprobe = executable("ffprobe", ffprobe)
        self.font = font
        self.log = log
        self.stop_event = stop_event if stop_event is not None else threading.Event()
        self._file_filters = None
        self._video_encoders = None

    def run(self, command, cwd=None, capture=False, progress=None):
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
                    if progress is not None:
                        progress.poll()
                if self.stop_event.is_set():
                    raise Cancelled("Остановлено пользователем")
                if process.returncode:
                    errors.seek(0, os.SEEK_END)
                    errors.seek(max(0, errors.tell() - 4000))
                    raise RuntimeError(errors.read().decode("utf-8", errors="replace"))
                if progress is not None:
                    progress.poll()
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

    def video_filter(self, work):
        if self._file_filters is None:
            version = self.run([self.ffmpeg, "-version"], capture=True)
            match = re.search(r"ffmpeg version (?:n)?(\d+)\.", version)
            # File-valued options were introduced in FFmpeg 7. Unknown builds
            # can always accept the filtergraph directly (no shell involved).
            self._file_filters = bool(match and int(match[1]) >= 7)
        if self._file_filters:
            return ["-/filter:v", "visual.txt"]
        return ["-filter:v", (work / "visual.txt").read_text(encoding="utf-8")]

    def visual_encoding(self, options):
        if options.video_profile not in VIDEO_PROFILES:
            raise ValueError("Неизвестный профиль визуализации")
        profile = VIDEO_PROFILES[options.video_profile]
        if self._video_encoders is None:
            listing = self.run([self.ffmpeg, "-hide_banner", "-encoders"], capture=True)
            self._video_encoders = set(re.findall(r"^\s*V\S{5}\s+(\S+)", listing, re.MULTILINE))
        if profile.encoder not in self._video_encoders:
            raise ValueError(f"В установленной сборке FFmpeg нет кодировщика {profile.encoder}. "
                             "Установите сборку с этим кодировщиком или выберите другой профиль визуализации.")
        if options.video_profile == "h264":
            arguments = ("-preset", options.preset, "-tune", "stillimage", "-crf", str(options.crf))
            self.log(f"Видео: H.264 — x264 {options.preset}, stillimage, CRF {options.crf}")
        else:
            arguments = profile.arguments
            self.log("Видео: " + profile.label)
        return ["-c:v", profile.encoder, *arguments]

    def analyze(self, source, start, duration, work):
        # Bounded memory and analysis time, independent of the length of the album.
        import numpy as np
        excerpts = []
        length = min(10, duration) if duration > 30 else duration
        offsets = [fraction * (duration - length) for fraction in (0.1, 0.5, 0.9)] if duration > 30 else [0]
        for offset in offsets:
            self.run([self.ffmpeg, "-v", "error", "-y", "-ss", str(start + offset), "-i", str(source),
                      "-t", str(length), "-map", "0:a:0", "-ac", "1", "-ar", "22050",
                      "-c:a", "pcm_s16le", "analysis.wav"], cwd=work)
            with wave.open(str(work / "analysis.wav")) as audio:
                excerpts.append(np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2").astype(float) / 32768)
        return sound_features(np.concatenate(excerpts))

    def visualization(self, source, tracks, duration, options, work, format_label=""):
        shutil.copyfile(font_path(self.font), work / "font.ttf")
        width, height = map(int, options.resolution.split("x"))
        scale = min(width / 1280, height / 720)
        available = max(1, int(width * 0.9))
        album, album_size = fit_text(options.album or source.stem, work / "font.ttf",
                                     max(1, round(48 * scale)), available, lines=1)
        (work / "album.txt").write_text(album, encoding="utf-8")
        analysis = []
        for i, track in enumerate(tracks):
            end = tracks[i + 1]["start"] if i + 1 < len(tracks) else duration
            self.log(f"Анализ трека {track['num']}: {track['title']}")
            analysis.append(self.analyze(source, track["start"], end - track["start"], work))
        filters = []
        for i, track in enumerate(tracks):
            start = 0 if i == 0 else track["start"]
            end = tracks[i + 1]["start"] if i + 1 < len(tracks) else duration
            color = sound_color(analysis[i])
            enable = f"gte(t,{start})*lt(t,{end})"
            filters.append(f"drawbox=color={color}:t=fill:enable='{enable}'")
        filters.append("drawtext=fontfile=font.ttf:textfile=album.txt:expansion=none:"
                       f"fontcolor=white:fontsize={album_size}:x=(w-text_w)/2:y=h*0.25")
        for i, track in enumerate(tracks):
            start = 0 if i == 0 else track["start"]
            end = tracks[i + 1]["start"] if i + 1 < len(tracks) else duration
            title = f"{track['num']:02d}. {track['title']}" if len(tracks) > 1 else track["title"]
            title, size = fit_text(title, work / "font.ttf", max(1, round(36 * scale)), available)
            (work / f"track{i}.txt").write_text(title, encoding="utf-8")
            filters.append(f"drawtext=fontfile=font.ttf:textfile=track{i}.txt:expansion=none:"
                           f"fontcolor=white:fontsize={size}:text_align=C:line_spacing={max(1, round(8 * scale))}:"
                           f"x=(w-text_w)/2:y=h*0.5-text_h/2:"
                           f"enable='gte(t,{start})*lt(t,{end})'")
        total = f"{int(duration)//3600:02d}\\:{int(duration)%3600//60:02d}\\:{int(duration)%60:02d}"
        elapsed = r"%{pts\:hms}"
        filters.append(f"drawtext=fontfile=font.ttf:text='{elapsed} / {total}':"
                       f"fontcolor=white:fontsize={max(1, round(30 * scale))}:x=(w-text_w)/2:y=h*0.72")
        label, label_size = fit_text(format_label, work / "font.ttf", max(1, round(24 * scale)), available, lines=1)
        (work / "format.txt").write_text(label, encoding="utf-8")
        filters.append("drawtext=fontfile=font.ttf:textfile=format.txt:expansion=none:"
                       f"fontcolor=white:fontsize={label_size}:x=(w-text_w)/2:y=h*0.85")
        (work / "visual.txt").write_text(",".join(filters), encoding="utf-8")

    def convert(self, source, destination, options=None):
        options = options or Options()
        source, destination = Path(source).expanduser().resolve(), Path(destination).expanduser().resolve()
        if not source.is_file():
            raise ValueError(f"Входной файл не найден: {source}")
        if source == destination or (destination.exists() and os.path.samefile(source, destination)):
            raise ValueError("Входной и выходной файлы совпадают. Выберите другую папку для результата MKV.")
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
        video_arguments = self.visual_encoding(options) if options.video == "visual" else []
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
        self.log("Исходный звук: " + audio_format(audio.get("codec_name", "unknown"),
                 int(audio.get("sample_rate") or rate), channels, detect_bit_depth(audio), audio.get("channel_layout")))
        layout = audio.get("channel_layout")
        channel_filter = ""
        if options.normalize_channels:
            channels, layout, channel_filter = normalized_channel_map(layout, channels, options.codec)
        if channel_filter:
            self.log("Назначение каналов: " + channel_filter)
        format_label = audio_format(options.codec, rate, channels,
                                    depth if options.codec == "lpcm" else None, layout)
        self.log("Выходной звук: " + format_label)
        codec = options.codec
        if codec == "lpcm":
            codec = "pcm_s32le" if depth >= 32 else "pcm_s24le" if depth >= 24 else "pcm_s16le"
        if codec in ("ac3", "truehd") and channels > 6:
            raise ValueError(f"{codec}: поддерживается максимум 6 каналов. Для сохранения 7.1 выберите LPCM или FLAC.")
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
            command = [self.ffmpeg, "-v", "error", "-nostdin", "-nostats", "-y",
                       "-stats_period", "1", "-progress", "progress.txt", "-i", str(source)]
            metadata_index = 1
            if options.video == "visual":
                visual_tracks = tracks or [{"num": 1, "title": options.title, "start": 0.0}]
                if tracks and tracks[-1]["start"] >= duration:
                    raise ValueError("CUE выходит за длительность аудио")
                self.visualization(source, visual_tracks, duration, options, work, format_label)
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
                command += ["-map", "1:v:0", *self.video_filter(work), *video_arguments,
                            "-pix_fmt", "yuv420p", "-t", str(duration)]
            command += ["-c:a", codec, "-ar", str(rate), "-ac", str(channels)]
            if channel_filter:
                command += ["-filter:a", channel_filter]
                command += ["-channel_layout:a", layout]
            if codec == "flac":
                command += ["-compression_level", str(options.flac_level)]
            elif codec == "ac3":
                command += ["-b:a", options.bitrate]
            elif codec == "truehd":
                command += ["-strict", "experimental"]
            command += ["result.mkv"]
            self.log("Конвертация...")
            progress = EncodingProgress(work / "progress.txt", duration, self.log)
            progress.report()
            self.run(command, cwd=work, progress=progress)
            if self.stop_event.is_set():
                raise Cancelled("Остановлено пользователем")
            progress.complete()
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
