import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import subprocess, os, re, json, tempfile, threading
import numpy as np
import librosa
import colorsys

# ------------------- НАСТРОЙКИ -------------------
FFMPEG  = r"c:\progs\ffmpeg\bin\ffmpeg.exe"
FFPROBE = r"c:\progs\ffmpeg\bin\ffprobe.exe"
FONT    = r"C\:/Windows/Fonts/arial.ttf"
# ------------------------------------------------

def probe(path):
    cmd = [FFPROBE, "-v", "quiet", "-print_format", "json",
           "-show_streams", "-show_format", path]
    r = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return json.loads(r.stdout)

def parse_cue(cue_path):
    tracks, current = [], None
    with open(cue_path, "r", encoding="utf-8-sig", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line: continue
            m = re.match(r'TRACK\s+(\d+)\s+AUDIO', line, re.IGNORECASE)
            if m:
                if current and current.get("start") is not None:
                    tracks.append(current)
                current = {"num": int(m.group(1)), "title": "", "start": None}
                continue
            if current is None: continue
            m = re.match(r'TITLE\s+"(.+)"', line, re.IGNORECASE)
            if m and not current.get("title"):
                current["title"] = m.group(1)
                continue
            m = re.match(r'INDEX\s+01\s+(\d+):(\d+):(\d+)', line, re.IGNORECASE)
            if m:
                mm, ss, ff = map(int, m.groups())
                current["start"] = mm*60 + ss + ff/75.0
    if current and current.get("start") is not None:
        tracks.append(current)
    return tracks

def choose_lpcm(bd):
    if bd >= 32: return "pcm_s32le"
    if bd >= 24: return "pcm_s24le"
    return "pcm_s16le"

def detect_bit_depth(audio_stream):
    bd = audio_stream.get("bits_per_raw_sample") or audio_stream.get("bits_per_sample")
    if bd:
        return int(bd)
    codec = audio_stream.get("codec_name", "")
    return 24 if codec in ("mlp", "truehd", "flac") else 16

def prepare_source(inp, log_func):
    """
    Проверяет формат. Если это AOB, MLP, DTS или контейнер MPEG,
    извлекает чистый WAV во временную папку и возвращает путь к нему.
    """
    try:
        d = probe(inp)
        streams = d.get("streams", [])
        audio = next((s for s in streams if s.get("codec_type")=="audio"), None)
        if not audio: return inp
        
        codec = audio.get("codec_name", "")
        fmt = d.get("format", {}).get("format_name", "")
        
        # Если кодек сжатый или контейнер сложный — извлекаем в WAV
        if codec in ("mlp", "dts", "truehd") or "mpeg" in fmt:
            log_func(f"Обнаружен {codec} в контейнере {fmt}. Подготовка чистого WAV...")
            tmp_wav = os.path.join(tempfile.gettempdir(), "gui_temp_extracted.wav")
            # Распаковываем в 24-бит PCM, сохраняя оригинальную частоту и каналы
            cmd = [FFMPEG, "-y", "-i", inp, "-c:a", "pcm_s24le", tmp_wav]
            subprocess.run(cmd, check=True, capture_output=True)
            return tmp_wav
    except Exception as e:
        log_func(f"Предварительный анализ не удался: {e}")
    return inp

def make_chapters_xml(cue_path, media_path):
    tracks = parse_cue(cue_path)
    if not tracks: return None
    dur = float(probe(media_path)["format"]["duration"])
    lines = [";FFMETADATA1"]
    for i, t in enumerate(tracks):
        start = t["start"]
        end = tracks[i+1]["start"] if i+1 < len(tracks) else dur
        lines += ["[CHAPTER]", "TIMEBASE=1/1000",
                  f"START={int(start*1000)}", f"END={int(end*1000)}",
                  f"title={t['title']}"]
    tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8')
    tmp.write("\n".join(lines)); tmp.close()
    return tmp.name

def analyze_mood(inp, sample_sec=120):
    tmp_wav = os.path.join(tempfile.gettempdir(), "mood_sample.wav")
    try:
        subprocess.run([FFMPEG, "-y", "-ss", "0", "-t", str(sample_sec),
                        "-i", inp, "-c:a", "pcm_s16le", "-ar", "22050", "-ac", "1", tmp_wav],
                       check=True, capture_output=True)
        y, _ = librosa.load(tmp_wav, sr=22050)
        cent = float(np.mean(librosa.feature.spectral_centroid(y=y, sr=22050)[0]))
        rms  = float(np.mean(librosa.feature.rms(y=y)[0]))
        return cent, rms
    finally:
        if os.path.exists(tmp_wav): os.remove(tmp_wav)

def mood_to_color(centroid, rms):
    norm = np.clip((centroid - 200) / (4000 - 200), 0.0, 1.0)
    sat = np.clip(rms * 5.0, 0.4, 1.0)
    r, g, b = colorsys.hsv_to_rgb(norm, sat, 0.4)
    return int(r*255), int(g*255), int(b*255)

def timer_strings(total_dur):
    if total_dur >= 3600:
        total_fmt = f"{int(total_dur//3600):02d}\\:{int((total_dur%3600)//60):02d}\\:{int(total_dur%60):02d}"
        elapsed = "%{pts\\:gmtime\\:0\\:%H\\\\\\:%M\\\\\\:%S}"
    else:
        total_fmt = f"{int(total_dur//60):02d}\\:{int(total_dur%60):02d}"
        elapsed = "%{pts\\:gmtime\\:0\\:%M\\\\\\:%S}"
    return elapsed, total_fmt

# =====================================================================
class App:
    def __init__(self, root):
        self.root = root
        root.title("Audio → MKV Converter")
        root.geometry("960x780")

        self.nb = ttk.Notebook(root)
        self.nb.pack(fill='both', expand=True, padx=5, pady=5)

        self.tab_simple = ttk.Frame(self.nb)
        self.tab_visual = ttk.Frame(self.nb)
        self.nb.add(self.tab_simple, text="  LPCM → MKV (простой)  ")
        self.nb.add(self.tab_visual, text="  Визуализация по CUE  ")

        self.build_simple()
        self.build_visual()

        self.log = scrolledtext.ScrolledText(root, height=12)
        self.log.pack(fill='both', expand=True, padx=5, pady=5)

    def log_msg(self, msg):
        self.log.insert('end', msg + '\n'); self.log.see('end'); self.root.update_idletasks()

    def clear_log(self):
        self.log.delete('1.0', 'end')

    def browse_file(self, var):
        p = filedialog.askopenfilename()
        if p: var.set(p)

    def browse_save(self, var):
        p = filedialog.asksaveasfilename(defaultextension=".mkv",
                                         filetypes=[("MKV", "*.mkv")])
        if p: var.set(p)

    # =================================================================
    # ВКЛАДКА 1
    # =================================================================
    def build_simple(self):
        f, pad = self.tab_simple, {'padx':5, 'pady':3}
        r = 0

        ttk.Label(f, text="Входной файл:").grid(row=r, column=0, sticky='w', **pad)
        self.s_in = tk.StringVar()
        ttk.Entry(f, textvariable=self.s_in, width=78).grid(row=r, column=1, **pad)
        ttk.Button(f, text="...", width=3, command=lambda: self.browse_file(self.s_in)).grid(row=r, column=2, **pad)
        r += 1

        ttk.Label(f, text="Выходной MKV:").grid(row=r, column=0, sticky='w', **pad)
        self.s_out = tk.StringVar()
        ttk.Entry(f, textvariable=self.s_out, width=78).grid(row=r, column=1, **pad)
        ttk.Button(f, text="...", width=3, command=lambda: self.browse_save(self.s_out)).grid(row=r, column=2, **pad)
        r += 1

        vid_frame = ttk.LabelFrame(f, text="Видеодорожка")
        vid_frame.grid(row=r, column=0, columnspan=3, sticky='we', **pad); r += 1

        self.s_video_mode = tk.StringVar(value="none")
        ttk.Radiobutton(vid_frame, text="Без видео (только аудио)",
                        variable=self.s_video_mode, value="none").pack(anchor='w', padx=5, pady=2)
        ttk.Radiobutton(vid_frame, text="Копировать видео из источника (без перекодирования)",
                        variable=self.s_video_mode, value="copy").pack(anchor='w', padx=5, pady=2)
        ttk.Radiobutton(vid_frame, text="Создать визуализацию (цветной фон + титры + таймер)",
                        variable=self.s_video_mode, value="visual",
                        command=self.toggle_simple_visual).pack(anchor='w', padx=5, pady=2)

        self.s_vis_frame = ttk.LabelFrame(f, text="Параметры визуализации")
        self.s_vis_frame.grid(row=r, column=0, columnspan=3, sticky='we', **pad); r += 1
        self.s_vis_frame.grid_remove()

        ttk.Label(self.s_vis_frame, text="Название альбома:").grid(row=0, column=0, sticky='w', **pad)
        self.s_album = tk.StringVar(value="")
        ttk.Entry(self.s_vis_frame, textvariable=self.s_album, width=50).grid(row=0, column=1, **pad)

        ttk.Label(self.s_vis_frame, text="Название трека:").grid(row=1, column=0, sticky='w', **pad)
        self.s_track_title = tk.StringVar(value="Full Album")
        ttk.Entry(self.s_vis_frame, textvariable=self.s_track_title, width=50).grid(row=1, column=1, **pad)

        ttk.Label(self.s_vis_frame, text="Разрешение:").grid(row=2, column=0, sticky='w', **pad)
        self.s_res = tk.StringVar(value="1280x720")
        ttk.Combobox(self.s_vis_frame, textvariable=self.s_res,
                     values=["1280x720","1920x1080","854x480"], width=14).grid(row=2, column=1, sticky='w', **pad)

        self.s_use_cue = tk.BooleanVar(value=False)
        ttk.Checkbutton(f, text="Добавить главы из CUE (без разбивки на треки)",
                        variable=self.s_use_cue, command=self.toggle_simple_cue).grid(row=r, column=0, columnspan=3, sticky='w', **pad); r += 1

        ttk.Label(f, text="CUE файл:").grid(row=r, column=0, sticky='w', **pad)
        self.s_cue = tk.StringVar()
        self.s_cue_entry = ttk.Entry(f, textvariable=self.s_cue, width=78, state='disabled')
        self.s_cue_entry.grid(row=r, column=1, **pad)
        self.s_cue_btn = ttk.Button(f, text="...", width=3,
                                    command=lambda: self.browse_file(self.s_cue), state='disabled')
        self.s_cue_btn.grid(row=r, column=2, **pad); r += 1

        af = ttk.LabelFrame(f, text="Аудио (LPCM)")
        af.grid(row=r, column=0, columnspan=3, sticky='we', **pad); r += 1

        ttk.Label(af, text="Битность:").grid(row=0, column=0, sticky='w', padx=5, pady=2)
        self.s_bit = tk.StringVar(value="Auto")
        ttk.Combobox(af, textvariable=self.s_bit, values=["Auto","16","24","32"],
                     state='readonly', width=8).grid(row=0, column=1, sticky='w', padx=5)

        ttk.Label(af, text="Частота (Гц):").grid(row=0, column=2, sticky='w', padx=5)
        self.s_rate = tk.StringVar(value="Auto")
        ttk.Combobox(af, textvariable=self.s_rate,
                     values=["Auto","44100","48000","96000","192000"],
                     state='readonly', width=10).grid(row=0, column=3, sticky='w', padx=5)

        bf = ttk.Frame(f); bf.grid(row=r, column=0, columnspan=3, pady=10); r += 1
        ttk.Button(bf, text="Показать характеристики",
                   command=self.probe_simple).pack(side='left', padx=5)
        ttk.Button(bf, text="▶ Конвертировать",
                   command=lambda: threading.Thread(target=self.run_simple, daemon=True).start()).pack(side='left', padx=5)

        self.s_info = ttk.Label(f, text="", foreground="blue", wraplength=900)
        self.s_info.grid(row=r, column=0, columnspan=3, sticky='w', **pad)

    def toggle_simple_visual(self):
        if self.s_video_mode.get() == "visual": self.s_vis_frame.grid()
        else: self.s_vis_frame.grid_remove()

    def toggle_simple_cue(self):
        st = 'normal' if self.s_use_cue.get() else 'disabled'
        self.s_cue_entry.config(state=st)
        self.s_cue_btn.config(state=st)

    def probe_simple(self):
        p = self.s_in.get()
        if not p: return
        try:
            d = probe(p)
            lines = []
            for s in d.get("streams", []):
                ct = s.get("codec_type")
                if ct == "audio":
                    lines.append(f"AUDIO: {s.get('codec_name')} | {s.get('sample_rate')} Hz | "
                                 f"{s.get('channels')} ch | {s.get('bits_per_raw_sample','?')} bit")
                elif ct == "video":
                    lines.append(f"VIDEO: {s.get('codec_name')} | {s.get('width')}x{s.get('height')} | {s.get('r_frame_rate')} fps")
            self.s_info.config(text="\n".join(lines))
            self.log_msg("\n".join(lines))
        except Exception as e:
            messagebox.showerror("Ошибка", str(e))

    def run_simple(self):
        inp, outp = self.s_in.get(), self.s_out.get()
        if not inp or not outp:
            messagebox.showerror("Ошибка", "Укажите входной и выходной файлы"); return
        self.clear_log()
        self.log_msg(f"Файл: {inp}")

        temp_wav = None
        try:
            # ПОДГОТОВКА: если AOB или DTS, распаковываем в WAV
            prepared_inp = prepare_source(inp, self.log_msg)
            if prepared_inp != inp:
                temp_wav = prepared_inp
                inp = prepared_inp

            d = probe(inp)
            audio = next((s for s in d.get("streams",[]) if s.get("codec_type")=="audio"), None)
            if not audio:
                self.log_msg("Нет аудиопотоков"); return

            bd = int(self.s_bit.get()) if self.s_bit.get()!="Auto" else detect_bit_depth(audio)
            lpcm = choose_lpcm(bd)
            sr = int(self.s_rate.get()) if self.s_rate.get()!="Auto" else int(audio.get("sample_rate", 48000))
            ch = audio.get("channels", 2)

            meta = None
            if self.s_use_cue.get() and self.s_cue.get():
                meta = make_chapters_xml(self.s_cue.get(), inp)

            mode = self.s_video_mode.get()
            if mode == "visual":
                self._run_simple_visual(inp, outp, d, lpcm, sr, ch, meta)
            else:
                self._run_simple_plain(inp, outp, d, lpcm, sr, ch, meta, copy_video=(mode=="copy"))

        except Exception as e:
            self.log_msg(f"Критическая ошибка: {e}")
        finally:
            if temp_wav and os.path.exists(temp_wav): os.remove(temp_wav)

    def _run_simple_plain(self, inp, outp, d, lpcm, sr, ch, meta, copy_video):
        video = next((s for s in d.get("streams",[]) if s.get("codec_type")=="video"), None)
        cmd = [FFMPEG, "-y", "-i", inp]
        if meta: cmd += ["-i", meta]

        maps, codecs = [], []
        if video and copy_video:
            maps += ["-map", "0:v:0"]; codecs += ["-c:v", "copy"]
        maps += ["-map", "0:a:0"]
        codecs += ["-c:a", lpcm, "-ar", str(sr), "-ac", str(ch)]
        if meta:
            maps += ["-map_metadata", "1", "-map_chapters", "1"]

        cmd += maps + codecs + [outp]
        self.log_msg("Команда: " + " ".join(cmd))
        try:
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode != 0:
                self.log_msg("ОШИБКА:\n" + r.stderr[-2000:])
            else:
                self.log_msg(f"\n✅ Готово: {outp}")
        finally:
            if meta and os.path.exists(meta): os.remove(meta)

    def _run_simple_visual(self, inp, outp, d, lpcm, sr, ch, meta):
        total_dur = float(d["format"]["duration"])
        self.log_msg("Анализ настроения файла...")
        try:
            cent, rms = analyze_mood(inp, sample_sec=min(120, int(total_dur)))
            self.log_msg(f"  centroid={cent:.0f} Hz, RMS={rms:.3f}")
        except Exception as e:
            self.log_msg(f"  Анализ не удался: {e}")
            cent, rms = 1000, 0.05

        rr, gg, bb = mood_to_color(cent, rms)
        hexc = f"0x{rr:02x}{gg:02x}{bb:02x}"
        self.log_msg(f"  Цвет фона: {hexc}")

        album = self.s_album.get() or "Album"
        track = self.s_track_title.get() or "Full Album"
        res   = self.s_res.get()
        elapsed, total_fmt = timer_strings(total_dur)

        vf = (
            f"drawtext=fontfile='{FONT}':text='{album}':fontcolor=white:fontsize=48:"
            f"x=(w-text_w)/2:y=(h-text_h)/2-80,"
            f"drawtext=fontfile='{FONT}':text='{track}':fontcolor=white:fontsize=36:"
            f"x=(w-text_w)/2:y=(h-text_h)/2+20,"
            f"drawtext=fontfile='{FONT}':text='{elapsed} / {total_fmt}':fontcolor=white:fontsize=36:"
            f"x=(w-text_w)/2:y=(h-text_h)/2+80"
        )

        cmd = [FFMPEG, "-y",
               "-f", "lavfi", "-i", f"color=c={hexc}:s={res}:r=10",
               "-i", inp]
        if meta: cmd += ["-i", meta]

        cmd += ["-map", "0:v", "-map", "1:a:0", "-vf", vf,
                "-c:v", "libx264", "-preset", "ultrafast", "-tune", "stillimage",
                "-crf", "23", "-pix_fmt", "yuv420p",
                "-c:a", lpcm, "-ar", str(sr), "-ac", str(ch)]
        if meta: cmd += ["-map_metadata", "2", "-map_chapters", "2"]
        cmd += ["-shortest", outp]

        self.log_msg("Команда: " + " ".join(cmd))
        try:
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode != 0:
                self.log_msg("ОШИБКА:\n" + r.stderr[-2000:])
            else:
                self.log_msg(f"\n✅ Готово: {outp}")
        finally:
            if meta and os.path.exists(meta): os.remove(meta)

    # =================================================================
    # ВКЛАДКА 2
    # =================================================================
    def build_visual(self):
        f, pad = self.tab_visual, {'padx':5, 'pady':3}
        r = 0

        ttk.Label(f, text="Входной WAV / DTS-WAV / AOB:").grid(row=r, column=0, sticky='w', **pad)
        self.v_in = tk.StringVar()
        ttk.Entry(f, textvariable=self.v_in, width=78).grid(row=r, column=1, **pad)
        ttk.Button(f, text="...", width=3, command=lambda: self.browse_file(self.v_in)).grid(row=r, column=2, **pad)
        r += 1

        ttk.Label(f, text="CUE файл:").grid(row=r, column=0, sticky='w', **pad)
        self.v_cue = tk.StringVar()
        ttk.Entry(f, textvariable=self.v_cue, width=78).grid(row=r, column=1, **pad)
        ttk.Button(f, text="...", width=3, command=lambda: self.browse_file(self.v_cue)).grid(row=r, column=2, **pad)
        r += 1

        ttk.Label(f, text="Выходной MKV:").grid(row=r, column=0, sticky='w', **pad)
        self.v_out = tk.StringVar()
        ttk.Entry(f, textvariable=self.v_out, width=78).grid(row=r, column=1, **pad)
        ttk.Button(f, text="...", width=3, command=lambda: self.browse_save(self.v_out)).grid(row=r, column=2, **pad)
        r += 1

        self.v_visualize = tk.BooleanVar(value=True)
        ttk.Checkbutton(f, text="Включить визуализацию (цвета фона + титры + таймер по трекам)",
                        variable=self.v_visualize, command=self.toggle_visual_options).grid(row=r, column=0, columnspan=3, sticky='w', **pad); r += 1

        self.v_vis_frame = ttk.LabelFrame(f, text="Параметры визуализации")
        self.v_vis_frame.grid(row=r, column=0, columnspan=3, sticky='we', **pad); r += 1

        ttk.Label(self.v_vis_frame, text="Название альбома:").grid(row=0, column=0, sticky='w', **pad)
        self.v_album = tk.StringVar(value="Herbie Hancock - Thrust")
        ttk.Entry(self.v_vis_frame, textvariable=self.v_album, width=50).grid(row=0, column=1, **pad)

        ttk.Label(self.v_vis_frame, text="Разрешение:").grid(row=1, column=0, sticky='w', **pad)
        self.v_res = tk.StringVar(value="1280x720")
        ttk.Combobox(self.v_vis_frame, textvariable=self.v_res,
                     values=["1280x720","1920x1080","854x480"], width=14).grid(row=1, column=1, sticky='w', **pad)

        af = ttk.LabelFrame(f, text="Аудио (LPCM)")
        af.grid(row=r, column=0, columnspan=3, sticky='we', **pad); r += 1

        ttk.Label(af, text="Битность:").grid(row=0, column=0, sticky='w', padx=5, pady=2)
        self.v_bit = tk.StringVar(value="Auto")
        ttk.Combobox(af, textvariable=self.v_bit, values=["Auto","16","24","32"],
                     state='readonly', width=8).grid(row=0, column=1, sticky='w', padx=5)

        ttk.Label(af, text="Частота (Гц):").grid(row=0, column=2, sticky='w', padx=5)
        self.v_rate = tk.StringVar(value="Auto")
        ttk.Combobox(af, textvariable=self.v_rate,
                     values=["Auto","44100","48000","96000","192000"],
                     state='readonly', width=10).grid(row=0, column=3, sticky='w', padx=5)

        ttk.Button(f, text="▶ Создать MKV",
                   command=lambda: threading.Thread(target=self.run_visual, daemon=True).start()).grid(row=r, column=0, columnspan=3, pady=10); r += 1

        self.v_info = ttk.Label(f, text="", foreground="blue", wraplength=900)
        self.v_info.grid(row=r, column=0, columnspan=3, sticky='w', **pad)

    def toggle_visual_options(self):
        if self.v_visualize.get(): self.v_vis_frame.grid()
        else: self.v_vis_frame.grid_remove()

    def run_visual(self):
        inp, cue, outp = self.v_in.get(), self.v_cue.get(), self.v_out.get()
        if not inp or not cue or not outp:
            messagebox.showerror("Ошибка", "Заполните входной файл, CUE и выходной MKV"); return
        self.clear_log()
        self.log_msg("Анализ входного файла...")

        temp_wav = None
        try:
            prepared_inp = prepare_source(inp, self.log_msg)
            if prepared_inp != inp:
                temp_wav = prepared_inp
                inp = prepared_inp

            d = probe(inp)
            audio = next((s for s in d.get("streams",[]) if s.get("codec_type")=="audio"), None)
            if not audio:
                self.log_msg("Нет аудио"); return

            bd = int(self.v_bit.get()) if self.v_bit.get()!="Auto" else detect_bit_depth(audio)
            lpcm = choose_lpcm(bd)
            sr = int(self.v_rate.get()) if self.v_rate.get()!="Auto" else int(audio.get("sample_rate", 48000))
            ch = audio.get("channels", 2)

            if not self.v_visualize.get():
                self._run_visual_plain(inp, cue, outp, d, lpcm, sr, ch)
            else:
                self._run_visual_fancy(inp, cue, outp, d, lpcm, sr, ch)

        except Exception as e:
            self.log_msg(f"Критическая ошибка: {e}")
        finally:
            if temp_wav and os.path.exists(temp_wav): os.remove(temp_wav)

    def _run_visual_plain(self, inp, cue, outp, d, lpcm, sr, ch):
        video = next((s for s in d.get("streams",[]) if s.get("codec_type")=="video"), None)
        meta = make_chapters_xml(cue, inp)

        cmd = [FFMPEG, "-y", "-i", inp]
        if meta: cmd += ["-i", meta]

        maps, codecs = [], []
        if video:
            maps += ["-map", "0:v:0"]; codecs += ["-c:v", "copy"]
        maps += ["-map", "0:a:0"]
        codecs += ["-c:a", lpcm, "-ar", str(sr), "-ac", str(ch)]
        if meta:
            maps += ["-map_metadata", "1", "-map_chapters", "1"]

        cmd += maps + codecs + [outp]
        self.log_msg("Команда: " + " ".join(cmd))
        try:
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode != 0:
                self.log_msg("ОШИБКА:\n" + r.stderr[-2000:])
            else:
                self.log_msg(f"\n✅ Готово: {outp}")
        finally:
            if meta and os.path.exists(meta): os.remove(meta)

    def _run_visual_fancy(self, inp, cue, outp, d, lpcm, sr, ch):
        tracks = parse_cue(cue)
        if not tracks:
            self.log_msg("CUE пуст или не распознан"); return
        total = len(tracks)
        duration = float(d["format"]["duration"])

        tmpdir = tempfile.mkdtemp()
        analysis = []
        try:
            for i, t in enumerate(tracks):
                s, e = t["start"], (tracks[i+1]["start"] if i+1<total else duration)
                self.log_msg(f"Анализ {t['num']:02d} '{t['title']}' ({s:.1f}–{e:.1f})...")
                tmp_wav_seg = os.path.join(tmpdir, f"seg{i}.wav")
                subprocess.run([FFMPEG,"-y","-ss",str(s),"-t",str(e-s),"-i",inp,
                                "-c:a","pcm_s16le","-ar","44100","-ac","1",tmp_wav_seg],
                               check=True, capture_output=True)
                y,_ = librosa.load(tmp_wav_seg, sr=44100)
                cent = float(np.mean(librosa.feature.spectral_centroid(y=y, sr=44100)[0]))
                rms  = float(np.mean(librosa.feature.rms(y=y)[0]))
                analysis.append({"centroid":cent,"rms":rms})
                self.log_msg(f"  centroid={cent:.0f} Hz, RMS={rms:.3f}")
        finally:
            for fn in os.listdir(tmpdir):
                try: os.remove(os.path.join(tmpdir, fn))
                except: pass
            try: os.rmdir(tmpdir)
            except: pass

        order = sorted(range(total), key=lambda i: analysis[i]["centroid"])
        ranks = [0]*total
        for r,i in enumerate(order): ranks[i] = r

        colors = []
        for i in range(total):
            hue = ranks[i] / max(total-1,1)
            sat = float(np.clip(analysis[i]["rms"]*5.0, 0.4, 1.0))
            rr,gg,bb = colorsys.hsv_to_rgb(hue, sat, 0.4)
            colors.append((int(rr*255), int(gg*255), int(bb*255)))
            self.log_msg(f"  Трек {tracks[i]['num']:02d}: rank={ranks[i]} RGB={colors[i]}")

        segments = []
        for i,t in enumerate(tracks):
            s, e = t["start"], (tracks[i+1]["start"] if i+1<total else duration)
            dur = e - s
            seg = os.path.join(tempfile.gettempdir(), f"vis_part{i:02d}.mkv")
            rr,gg,bb = colors[i]
            hexc = f"0x{rr:02x}{gg:02x}{bb:02x}"
            elapsed, total_fmt = timer_strings(dur)

            title = f"{t['num']:02d}. {t['title']}"
            album = self.v_album.get()
            res   = self.v_res.get()

            vf = (
                f"drawtext=fontfile='{FONT}':text='{album}':fontcolor=white:fontsize=48:"
                f"x=(w-text_w)/2:y=(h-text_h)/2-80,"
                f"drawtext=fontfile='{FONT}':text='{title}':fontcolor=white:fontsize=36:"
                f"x=(w-text_w)/2:y=(h-text_h)/2+20,"
                f"drawtext=fontfile='{FONT}':text='{elapsed} / {total_fmt}':fontcolor=white:fontsize=36:"
                f"x=(w-text_w)/2:y=(h-text_h)/2+80"
            )

            cmd = [FFMPEG,"-y",
                   "-f","lavfi","-i",f"color=c={hexc}:s={res}:r=10",
                   "-ss",str(s),"-t",str(dur),"-i",inp,
                   "-map","0:v","-map","1:a",
                   "-vf",vf,
                   "-c:v","libx264","-preset","ultrafast","-tune","stillimage",
                   "-crf","23","-pix_fmt","yuv420p",
                   "-c:a",lpcm,"-ar",str(sr),"-ac",str(ch),
                   "-shortest",seg]
            self.log_msg(f"Сегмент {i+1}/{total}...")
            subprocess.run(cmd, check=True, capture_output=True)
            segments.append(seg)

        concat = os.path.join(tempfile.gettempdir(), "vis_concat.txt")
        with open(concat,"w",encoding="utf-8") as f:
            for sg in segments: f.write(f"file '{sg}'\n")
        self.log_msg("Склейка...")
        subprocess.run([FFMPEG,"-y","-f","concat","-safe","0","-i",concat,"-c","copy",outp],
                       check=True, capture_output=True)

        for sg in segments:
            if os.path.exists(sg): os.remove(sg)
        if os.path.exists(concat): os.remove(concat)
        self.log_msg(f"\n✅ Готово: {outp}")

# =====================================================================
if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()