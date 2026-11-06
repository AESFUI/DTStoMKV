import subprocess
import os
import re
import json
import numpy as np
import librosa
import soundfile as sf
import colorsys

# ------------------- НАСТРОЙКИ -------------------
CONFIG = {
    "wav_path": r"d:\Torrents\Herbie Hancock_Trust_DTSCD\ThrustDTS.wav",
    "cue_path": r"d:\Torrents\Herbie Hancock_Trust_DTSCD\ThrustDTS.cue",
    "output_mkv": r"d:\Torrents\Herbie Hancock_Trust_DTSCD\Thrust_demo.mkv",
    "ffmpeg": r"c:\progs\ffmpeg\bin\ffmpeg.exe",
    "ffprobe": r"c:\progs\ffmpeg\bin\ffprobe.exe",
    "font": r"C\:/Windows/Fonts/arial.ttf",
    "album_title": "Herbie Hancock - Thrust",
    "res": "1280x720",
    "fps": 10,
    "crf": 23,
    "preset": "ultrafast",
    "audio_codec": None,   # None = автоопределение, или 'pcm_s16le', 'pcm_s24le', 'pcm_s32le'
    "audio_bitrate": None, # Только для конвертации в AC3 и т.п. Для LPCM не используется
}
# ------------------------------------------------

def run_logged(cmd, log=print):
    """Запускает команду, стримит её вывод в log и возбуждает ошибку при неуспехе."""
    log("$ " + " ".join(cmd))
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1
    )
    for line in proc.stdout:
        log(line.rstrip("\n"))
    proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f"Команда завершилась с кодом {proc.returncode}: {cmd[0]}")

def get_audio_info(wav_path, ffprobe):
    """Возвращает (sample_rate, channels, bit_depth, codec_name) исходного аудио."""
    cmd = [
        ffprobe, "-v", "quiet", "-print_format", "json",
        "-show_streams", "-select_streams", "a:0", wav_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    data = json.loads(result.stdout)
    stream = data["streams"][0]
    
    sample_rate = int(stream.get("sample_rate", 44100))
    channels = int(stream.get("channels", 2))
    codec_name = stream.get("codec_name", "dts")
    
    # Пытаемся определить битность
    bit_depth = stream.get("bits_per_raw_sample") or stream.get("bits_per_sample")
    if bit_depth:
        bit_depth = int(bit_depth)
    else:
        # Для DTS по умолчанию часто 24 или 16 бит
        # Пробуем угадать по коду
        bit_depth = 16  # fallback
    
    return sample_rate, channels, bit_depth, codec_name

def choose_lpcm_codec(bit_depth):
    """Выбирает кодек LPCM по битности."""
    if bit_depth >= 32:
        return "pcm_s32le"
    elif bit_depth >= 24:
        return "pcm_s24le"
    else:
        return "pcm_s16le"

def parse_cue(cue_path):
    tracks = []
    current = None
    with open(cue_path, "r", encoding="utf-8-sig", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            m = re.match(r'TRACK\s+(\d+)\s+AUDIO', line, re.IGNORECASE)
            if m:
                if current and current.get("start") is not None:
                    tracks.append(current)
                current = {"num": int(m.group(1)), "title": "", "start": None}
                continue
            if current is None:
                continue
            m = re.match(r'TITLE\s+"(.+)"', line, re.IGNORECASE)
            if m and not current.get("title"):
                current["title"] = m.group(1)
                continue
            m = re.match(r'INDEX\s+01\s+(\d+):(\d+):(\d+)', line, re.IGNORECASE)
            if m:
                mm, ss, ff = map(int, m.groups())
                current["start"] = mm * 60 + ss + ff / 75.0
                continue
    if current and current.get("start") is not None:
        tracks.append(current)
    return tracks

def analyze_track(wav_path, start_sec, end_sec):
    with sf.SoundFile(wav_path) as f:
        sr = f.samplerate
        f.seek(int(start_sec * sr))
        n = int((end_sec - start_sec) * sr)
        data = f.read(frames=n, dtype='float32', always_2d=True)
        mono = data.mean(axis=1)

    cent = librosa.feature.spectral_centroid(y=mono, sr=sr)[0]
    centroid = float(np.mean(cent))
    rms = float(np.mean(librosa.feature.rms(y=mono)[0]))
    return centroid, rms

def make_color(rank, total, rms):
    hue = rank / max(total - 1, 1)
    sat = np.clip(rms * 5.0, 0.4, 1.0)
    val = 0.4
    r, g, b = colorsys.hsv_to_rgb(hue, sat, val)
    return int(r * 255), int(g * 255), int(b * 255)

def fmt_time(sec, add_hours=False):
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    if add_hours:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"

def drawtext_escape_text(s):
    """Экранирует текст для значения text= фильтра drawtext.

    Парсинг двухуровневый: кавычки '...' на уровне filtergraph защищают запятую,
    но ':' (разделитель опций), '\\' и '%' (начало текстового расширения) требуют
    экранирования уже на уровне самого drawtext.
    """
    s = s.replace("\\", "\\\\")     # обратный слэш -> удвоенный (на двух уровнях)
    s = s.replace(":", "\\:")      # разделитель опций drawtext
    s = s.replace("%", "\\\\%")    # начало %{...}-расширения расшифровывается как \% на втором уровне
    s = s.replace("'", "’")        # апостроф непредставим без экранирования кавычек — заменяем на типографский ’
    return s

def drawtext_font(cfg):
    """Приводит путь шрифта к форме, безопасной для filtergraph:
    'C:\\Windows\\...arial.ttf' -> 'C\\:/Windows/...arial.ttf'.
    Преобразование идемпотентно для уже экранированных значений."""
    p = str(cfg["font"])
    if "\\:" in p:  # уже экранировано (форма из CONFIG) — не трогаем
        return p
    p = p.replace("\\", "/")
    if p[1:2] == ":":
        p = p[0] + "\\:" + p[2:]
    return p

def create_segment(cfg, wav_path, start, end, rgb, title, total_fmt_hours, audio_info, out_path, log=print):
    r, g, b = rgb
    hex_color = f"0x{r:02x}{g:02x}{b:02x}"
    duration = end - start
    sample_rate, channels, bit_depth, _ = audio_info

    use_hours = duration >= 3600
    if use_hours:
        total_fmt = f"{int(duration//3600):02d}\\:{int((duration%3600)//60):02d}\\:{int(duration%60):02d}"
        elapsed = "%{pts\\:gmtime\\:0\\:%H\\\\\\:%M\\\\\\:%S}"
    else:
        total_fmt = f"{int(duration//60):02d}\\:{int(duration%60):02d}"
        elapsed = "%{pts\\:gmtime\\:0\\:%M\\\\\\:%S}"

    font = drawtext_font(cfg)

    album = drawtext_escape_text(cfg["album_title"])
    track = drawtext_escape_text(title)

    ch_letter = {1: "1.0", 2: "2.0", 6: "5.1", 8: "7.1"}.get(channels, f"{channels} ch")
    tech = drawtext_escape_text(f"{sample_rate} Hz · {bit_depth} bit · {ch_letter}")

    vf = (
        f"drawtext=fontfile='{font}':text='{album}':fontcolor=white:fontsize=48:"
        f"x=(w-text_w)/2:y=(h-text_h)/2-80,"
        f"drawtext=fontfile='{font}':text='{track}':fontcolor=white:fontsize=36:"
        f"x=(w-text_w)/2:y=(h-text_h)/2+20,"
        f"drawtext=fontfile='{font}':text='{elapsed} / {total_fmt}':fontcolor=white:fontsize=36:"
        f"x=(w-text_w)/2:y=(h-text_h)/2+80,"
        f"drawtext=fontfile='{font}':text='{tech}':fontcolor=white@0.8:fontsize=24:"
        f"x=w-text_w-24:y=h-text_h-24"
    )

    # Автоматически выбираем кодек, если не задан явно
    lpcm_codec = cfg.get("audio_codec") or choose_lpcm_codec(bit_depth)

    cmd = [
        cfg["ffmpeg"],
        "-f", "lavfi", "-i", f"color=c={hex_color}:s={cfg['res']}:r={cfg['fps']}",
        "-ss", str(start), "-t", str(duration), "-i", wav_path,
        "-map", "0:v", "-map", "1:a",
        "-vf", vf,
        "-c:v", "libx264", "-preset", cfg["preset"], "-tune", "stillimage",
        "-crf", str(cfg["crf"]), "-pix_fmt", "yuv420p",
        "-c:a", lpcm_codec,
    ]
    # Параметры не-PCM кодеков: FLAC — уровень сжатия, AC3 и др. — битрейт; для LPCM не используется
    if lpcm_codec in ("ac3", "truehd") and channels > 6:
        raise RuntimeError(f"Кодек {lpcm_codec} поддерживает максимум 6 каналов, в источнике {channels}")
    if not lpcm_codec.startswith("pcm"):
        if lpcm_codec == "flac" and cfg.get("flac_level") is not None:
            cmd += ["-compression_level", str(cfg["flac_level"])]
        if lpcm_codec == "truehd":
            cmd += ["-strict", "experimental"]  # кодировщик TrueHD в ffmpeg остаётся экспериментальным
        if lpcm_codec not in ("flac", "truehd") and cfg.get("audio_bitrate"):
            cmd += ["-b:a", str(cfg["audio_bitrate"])]
    cmd += [
        "-ar", str(sample_rate),
        "-ac", str(channels),
        "-shortest", out_path
    ]
    print = log  # весь вывод дальше идёт в переданный логгер
    print(f"Создание сегмента: {out_path}")
    print(f"  Аудио: {lpcm_codec}, {sample_rate} Hz, {channels} ch, {bit_depth} bit")
    run_logged(cmd, log)

def main(cfg=None, log=print, progress=None):
    """progress(stage, done, total) — необязательный колбэк для индикатора.

    stage: 'analysis' | 'segments' | 'concat' | 'done'
    """
    cfg = cfg or CONFIG
    return _run(cfg, log, progress)

def _run(cfg, log, progress=None):
    def report(stage, done, total):
        if progress:
            progress(stage, done, total)

    audio_info = get_audio_info(cfg["wav_path"], cfg["ffprobe"])
    log(f"Входной аудиопоток: {audio_info[3]}, {audio_info[0]} Hz, {audio_info[1]} ch, {audio_info[2]} bit")

    tracks = parse_cue(cfg["cue_path"])
    total = len(tracks)
    info = sf.info(cfg["wav_path"])
    total_duration = info.duration
    log(f"Найдено {total} треков.")

    analysis = []
    for i, t in enumerate(tracks):
        start = t["start"]
        end = tracks[i + 1]["start"] if i + 1 < total else total_duration
        log(f"Анализ трека {t['num']:02d} '{t['title']}' ({start:.1f}–{end:.1f} c)...")
        cent, rms = analyze_track(cfg["wav_path"], start, end)
        analysis.append({"centroid": cent, "rms": rms})
        log(f"  centroid={cent:.0f} Hz, RMS={rms:.3f}")
        report("analysis", i + 1, total)

    sorted_indices = sorted(range(total), key=lambda idx: analysis[idx]["centroid"])
    ranks = [0] * total
    for rank, idx in enumerate(sorted_indices):
        ranks[idx] = rank

    colors = []
    for i in range(total):
        rgb = make_color(ranks[i], total, analysis[i]["rms"])
        colors.append(rgb)
        log(f"  Трек {tracks[i]['num']:02d}: ранг={ranks[i]}, RGB={rgb}")

    segments = []
    for i, t in enumerate(tracks):
        start = t["start"]
        end = tracks[i + 1]["start"] if i + 1 < total else total_duration
        seg_path = cfg["output_mkv"].replace(".mkv", f"_part{i:02d}.mkv")
        title_text = f"{t['num']:02d}. {t['title']}"
        create_segment(cfg, cfg["wav_path"], start, end, colors[i], title_text, None, audio_info, seg_path, log)
        segments.append(seg_path)
        report("segments", i + 1, total)

    concat_list = cfg["output_mkv"].replace(".mkv", "_concat.txt")
    with open(concat_list, "w", encoding="utf-8") as f:
        for seg in segments:
            f.write(f"file '{seg}'\n")

    cmd_concat = [
        cfg["ffmpeg"], "-f", "concat", "-safe", "0", "-i", concat_list,
        "-c", "copy", cfg["output_mkv"]
    ]
    log("Склейка сегментов...")
    report("concat", 0, 1)
    run_logged(cmd_concat, log)

    for seg in segments:
        if os.path.exists(seg):
            os.remove(seg)
    if os.path.exists(concat_list):
        os.remove(concat_list)

    log(f"\nГотово: {cfg['output_mkv']}")
    report("done", 1, 1)

if __name__ == "__main__":
    main()