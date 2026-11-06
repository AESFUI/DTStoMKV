import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import subprocess, os, re, json, tempfile, threading, time
import numpy as np
import librosa
import colorsys

# ------------------- НАСТРОЙКИ -------------------
FFMPEG  = r"c:\progs\ffmpeg\bin\ffmpeg.exe"
FFPROBE = r"c:\progs\ffmpeg\bin\ffprobe.exe"
FONT    = r"C\:/Windows/Fonts/arial.ttf"
SUPPORTED_EXTS = ('.mkv', '.aob', '.wav', '.dts', '.mlp', '.mka', '.ts')
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
        root.title("Audio → MKV Converter (с поддержкой отмены)")
        root.geometry("980x800")

        self.stop_event = threading.Event()
        self.is_running = False

        self.nb = ttk.Notebook(root)
        self.nb.pack(fill='both', expand=True, padx=5, pady=5)

        self.tab_simple = ttk.Frame(self.nb)
        self.tab_visual = ttk.Frame(self.nb)
        self.tab_batch = ttk.Frame(self.nb)
        
        self.nb.add(self.tab_simple, text="  LPCM → MKV (один файл)  ")
        self.nb.add(self.tab_visual, text="  Визуализация по CUE  ")
        self.nb.add(self.tab_batch, text="  Пакетная обработка папки  ")

        self.build_simple()
        self.build_visual()
        self.build_batch()

        self.log = scrolledtext.ScrolledText(root, height=12)
        self.log.pack(fill='both', expand=True, padx=5, pady=5)

    def log_msg(self, msg):
        self.log.insert('end', msg + '\n'); self.log.see('end'); self.root.update_idletasks()

    def clear_log(self):
        self.log.delete('1.0', 'end')

    def browse_file(self, var):
        p = filedialog.askopenfilename()
        if p: var.set(p)

    def browse_dir(self, var):
        p = filedialog.askdirectory()
        if p: var.set(p)

    def browse_save(self, var):
        p = filedialog.asksaveasfilename(defaultextension=".mkv", filetypes=[("MKV", "*.mkv")])
        if p: var.set(p)

    # ---------- УПРАВЛЕНИЕ ПРОЦЕССАМИ ----------
    def run_ffmpeg(self, cmd, log_prefix=""):
        """Запускает ffmpeg через Popen, безопасно для pipe-буферов."""
        if self.stop_event.is_set():
            raise Exception("Остановлено пользователем")

        creationflags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0

        # stderr пишем во временный файл — это исключает дедлок на заполненном pipe.
        with tempfile.TemporaryFile(mode='w+', encoding='utf-8', errors='replace') as errf:
            try:
                process = subprocess.Popen(
                    cmd,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=errf,
                    text=True,
                    creationflags=creationflags
                )
            except Exception as e:
                raise Exception(f"Не удалось запустить ffmpeg: {e}")

            while True:
                retcode = process.poll()
                if retcode is not None:
                    if retcode != 0:
                        errf.seek(0)
                        err_text = errf.read()
                        tail = err_text[-1500:] if len(err_text) > 1500 else err_text
                        raise Exception(f"FFmpeg error (code {retcode}):\n{tail}")
                    return

                if self.stop_event.is_set():
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                    raise Exception("Остановлено пользователем")

                time.sleep(0.1)

    def stop_processing(self):
        if self.is_running:
            self.log_msg("\n⏹️ Отправка сигнала на остановку...")
            self.stop_event.set()

    def start_task(self, target_func):
        if self.is_running:
            messagebox.showwarning("Внимание", "Процесс уже выполняется.")
            return
        self.stop_event.clear()
        self.is_running = True
        self.toggle_buttons("disable")
        threading.Thread(target=self._task_wrapper, args=(target_func,), daemon=True).start()

    def _task_wrapper(self, target_func):
        try:
            target_func()
        except Exception as e:
            self.log_msg(f"\n❌ ОШИБКА: {e}")
        finally:
            self.is_running = False
            self.toggle_buttons("enable")
            if self.stop_event.is_set():
                self.log_msg("\n⏹️ Обработка была прервана пользователем.")
            self.stop_event.clear()

    def toggle_buttons(self, state):
        st = 'normal' if state == "enable" else 'disabled'
        self.btn_simple_start.config(state=st)
        self.btn_simple_stop.config(state='normal' if state == "disable" else 'disabled')
        
        self.btn_visual_start.config(state=st)
        self.btn_visual_stop.config(state='normal' if state == "disable" else 'disabled')
        
        self.btn_batch_start.config(state=st)
        self.btn_batch_stop.config(state='normal' if state == "disable" else 'disabled')

    # =================================================================
    # ВКЛАДКА 1: ОДИН ФАЙЛ
    # =================================================================
    def build_simple(self):
        f, pad = self.tab_simple, {'padx':5, 'pady':3}
        r = 0

        ttk.Label(f, text="Входной файл:").grid(row=r, column=0, sticky='w', **pad)
        self.s_in = tk.StringVar()
        ttk.Entry(f, textvariable=self.s_in, width=80).grid(row=r, column=1, **pad)
        ttk.Button(f, text="...", width=3, command=lambda: self.browse_file(self.s_in)).grid(row=r, column=2, **pad); r+=1

        ttk.Label(f, text="Выходной MKV:").grid(row=r, column=0, sticky='w', **pad)
        self.s_out = tk.StringVar()
        ttk.Entry(f, textvariable=self.s_out, width=80).grid(row=r, column=1, **pad)
        ttk.Button(f, text="...", width=3, command=lambda: self.browse_save(self.s_out)).grid(row=r, column=2, **pad); r+=1

        vid_frame = ttk.LabelFrame(f, text="Видеодорожка")
        vid_frame.grid(row=r, column=0, columnspan=3, sticky='we', **pad); r+=1
        self.s_video_mode = tk.StringVar(value="none")
        ttk.Radiobutton(vid_frame, text="Без видео", variable=self.s_video_mode, value="none").pack(anchor='w')
        ttk.Radiobutton(vid_frame, text="Копировать видео (без перекодирования)", variable=self.s_video_mode, value="copy").pack(anchor='w')
        ttk.Radiobutton(vid_frame, text="Визуализация", variable=self.s_video_mode, value="visual", command=self.toggle_simple_visual).pack(anchor='w')

        self.s_vis_frame = ttk.LabelFrame(f, text="Параметры визуализации")
        self.s_vis_frame.grid(row=r, column=0, columnspan=3, sticky='we', **pad); r+=1
        self.s_vis_frame.grid_remove()
        ttk.Label(self.s_vis_frame, text="Альбом:").grid(row=0, column=0, sticky='w', **pad)
        self.s_album = tk.StringVar(); ttk.Entry(self.s_vis_frame, textvariable=self.s_album, width=50).grid(row=0, column=1, **pad)
        ttk.Label(self.s_vis_frame, text="Трек:").grid(row=1, column=0, sticky='w', **pad)
        self.s_track_title = tk.StringVar(value="Full Album"); ttk.Entry(self.s_vis_frame, textvariable=self.s_track_title, width=50).grid(row=1, column=1, **pad)
        ttk.Label(self.s_vis_frame, text="Разрешение:").grid(row=2, column=0, sticky='w', **pad)
        self.s_res = tk.StringVar(value="1280x720"); ttk.Combobox(self.s_vis_frame, textvariable=self.s_res, values=["1280x720","1920x1080"], width=14).grid(row=2, column=1, sticky='w', **pad)

        self.s_use_cue = tk.BooleanVar(value=False)
        ttk.Checkbutton(f, text="Добавить главы из CUE (без разбивки)", variable=self.s_use_cue, command=self.toggle_simple_cue).grid(row=r, column=0, columnspan=3, sticky='w', **pad); r+=1
        self.s_cue = tk.StringVar()
        self.s_cue_entry = ttk.Entry(f, textvariable=self.s_cue, width=80, state='disabled'); self.s_cue_entry.grid(row=r, column=1, **pad)
        self.s_cue_btn = ttk.Button(f, text="...", width=3, command=lambda: self.browse_file(self.s_cue), state='disabled'); self.s_cue_btn.grid(row=r, column=2, **pad); r+=1

        af = ttk.LabelFrame(f, text="Аудио (LPCM)")
        af.grid(row=r, column=0, columnspan=3, sticky='we', **pad); r+=1
        ttk.Label(af, text="Битность:").grid(row=0, column=0, sticky='w', padx=5)
        self.s_bit = tk.StringVar(value="Auto"); ttk.Combobox(af, textvariable=self.s_bit, values=["Auto","16","24","32"], state='readonly', width=8).grid(row=0, column=1, sticky='w', padx=5)
        ttk.Label(af, text="Частота:").grid(row=0, column=2, sticky='w', padx=5)
        self.s_rate = tk.StringVar(value="Auto"); ttk.Combobox(af, textvariable=self.s_rate, values=["Auto","44100","48000","96000"], state='readonly', width=10).grid(row=0, column=3, sticky='w', padx=5)

        bf = ttk.Frame(f); bf.grid(row=r, column=0, columnspan=3, pady=10); r+=1
        ttk.Button(bf, text="Показать инфо", command=self.probe_simple).pack(side='left', padx=5)
        self.btn_simple_start = ttk.Button(bf, text="▶ Конвертировать", command=lambda: self.start_task(self.run_simple))
        self.btn_simple_start.pack(side='left', padx=5)
        self.btn_simple_stop = ttk.Button(bf, text="⏹ Стоп", command=self.stop_processing, state='disabled')
        self.btn_simple_stop.pack(side='left', padx=5)
        
        self.s_info = ttk.Label(f, text="", foreground="blue", wraplength=900)
        self.s_info.grid(row=r, column=0, columnspan=3, sticky='w', **pad)

    def toggle_simple_visual(self):
        if self.s_video_mode.get() == "visual": self.s_vis_frame.grid()
        else: self.s_vis_frame.grid_remove()

    def toggle_simple_cue(self):
        st = 'normal' if self.s_use_cue.get() else 'disabled'
        self.s_cue_entry.config(state=st); self.s_cue_btn.config(state=st)

    def probe_simple(self):
        p = self.s_in.get()
        if not p: return
        try:
            d = probe(p)
            lines = []
            for s in d.get("streams", []):
                ct = s.get("codec_type")
                if ct == "audio":
                    lines.append(f"AUDIO: {s.get('codec_name')} | {s.get('sample_rate')} Hz | {s.get('channels')} ch | {s.get('bits_per_raw_sample','?')} bit")
                elif ct == "video":
                    lines.append(f"VIDEO: {s.get('codec_name')} | {s.get('width')}x{s.get('height')}")
            self.s_info.config(text="\n".join(lines))
            self.log_msg("\n".join(lines))
        except Exception as e: messagebox.showerror("Ошибка", str(e))

    def run_simple(self):
        inp, outp = self.s_in.get(), self.s_out.get()
        if not inp or not outp: raise Exception("Укажите файлы")
        self.clear_log()
        self.process_file(inp, outp, self.s_video_mode.get(), self.s_album.get(), self.s_track_title.get(), self.s_res.get(), 
                          self.s_bit.get(), self.s_rate.get(), self.s_use_cue.get(), self.s_cue.get())

    # =================================================================
    # ВКЛАДКА 2: ВИЗУАЛИЗАЦИЯ
    # =================================================================
    def build_visual(self):
        f, pad = self.tab_visual, {'padx':5, 'pady':3}
        r = 0
        ttk.Label(f, text="Входной файл:").grid(row=r, column=0, sticky='w', **pad)
        self.v_in = tk.StringVar(); ttk.Entry(f, textvariable=self.v_in, width=80).grid(row=r, column=1, **pad)
        ttk.Button(f, text="...", width=3, command=lambda: self.browse_file(self.v_in)).grid(row=r, column=2, **pad); r+=1
        ttk.Label(f, text="CUE файл:").grid(row=r, column=0, sticky='w', **pad)
        self.v_cue = tk.StringVar(); ttk.Entry(f, textvariable=self.v_cue, width=80).grid(row=r, column=1, **pad)
        ttk.Button(f, text="...", width=3, command=lambda: self.browse_file(self.v_cue)).grid(row=r, column=2, **pad); r+=1
        ttk.Label(f, text="Выходной MKV:").grid(row=r, column=0, sticky='w', **pad)
        self.v_out = tk.StringVar(); ttk.Entry(f, textvariable=self.v_out, width=80).grid(row=r, column=1, **pad)
        ttk.Button(f, text="...", width=3, command=lambda: self.browse_save(self.v_out)).grid(row=r, column=2, **pad); r+=1

        self.v_visualize = tk.BooleanVar(value=True)
        ttk.Checkbutton(f, text="Включить визуализацию (без нее просто добавит главы)", variable=self.v_visualize, command=self.toggle_visual_options).grid(row=r, column=0, columnspan=3, sticky='w', **pad); r+=1

        self.v_vis_frame = ttk.LabelFrame(f, text="Параметры визуализации")
        self.v_vis_frame.grid(row=r, column=0, columnspan=3, sticky='we', **pad); r+=1
        ttk.Label(self.v_vis_frame, text="Альбом:").grid(row=0, column=0, sticky='w', **pad)
        self.v_album = tk.StringVar(value="Unknown Album"); ttk.Entry(self.v_vis_frame, textvariable=self.v_album, width=50).grid(row=0, column=1, **pad)
        ttk.Label(self.v_vis_frame, text="Разрешение:").grid(row=1, column=0, sticky='w', **pad)
        self.v_res = tk.StringVar(value="1280x720"); ttk.Combobox(self.v_vis_frame, textvariable=self.v_res, values=["1280x720","1920x1080"], width=14).grid(row=1, column=1, sticky='w', **pad)

        af = ttk.LabelFrame(f, text="Аудио (LPCM)")
        af.grid(row=r, column=0, columnspan=3, sticky='we', **pad); r+=1
        ttk.Label(af, text="Битность:").grid(row=0, column=0, sticky='w', padx=5)
        self.v_bit = tk.StringVar(value="Auto"); ttk.Combobox(af, textvariable=self.v_bit, values=["Auto","16","24","32"], state='readonly', width=8).grid(row=0, column=1, sticky='w', padx=5)
        ttk.Label(af, text="Частота:").grid(row=0, column=2, sticky='w', padx=5)
        self.v_rate = tk.StringVar(value="Auto"); ttk.Combobox(af, textvariable=self.v_rate, values=["Auto","44100","48000","96000"], state='readonly', width=10).grid(row=0, column=3, sticky='w', padx=5)

        bf = ttk.Frame(f); bf.grid(row=r, column=0, columnspan=3, pady=10); r+=1
        self.btn_visual_start = ttk.Button(bf, text="▶ Создать MKV", command=lambda: self.start_task(self.run_visual))
        self.btn_visual_start.pack(side='left', padx=5)
        self.btn_visual_stop = ttk.Button(bf, text="⏹ Стоп", command=self.stop_processing, state='disabled')
        self.btn_visual_stop.pack(side='left', padx=5)

    def toggle_visual_options(self):
        if self.v_visualize.get(): self.v_vis_frame.grid()
        else: self.v_vis_frame.grid_remove()

    def run_visual(self):
        inp, cue, outp = self.v_in.get(), self.v_cue.get(), self.v_out.get()
        if not inp or not cue or not outp: raise Exception("Заполните все поля")
        self.clear_log()
        self.process_file(inp, outp, "visual" if self.v_visualize.get() else "copy",
                          self.v_album.get(), "Full Album", self.v_res.get(), 
                          self.v_bit.get(), self.v_rate.get(), True, cue)

    # =================================================================
    # ВКЛАДКА 3: ПАКЕТНАЯ ОБРАБОТКА
    # =================================================================
    def build_batch(self):
        f, pad = self.tab_batch, {'padx':5, 'pady':3}
        r = 0

        ttk.Label(f, text="Исходная папка:").grid(row=r, column=0, sticky='w', **pad)
        self.b_in_dir = tk.StringVar()
        ttk.Entry(f, textvariable=self.b_in_dir, width=80).grid(row=r, column=1, **pad)
        ttk.Button(f, text="...", width=3, command=lambda: self.browse_dir(self.b_in_dir)).grid(row=r, column=2, **pad); r+=1

        ttk.Label(f, text="Папка сохранения:").grid(row=r, column=0, sticky='w', **pad)
        self.b_out_dir = tk.StringVar()
        ttk.Entry(f, textvariable=self.b_out_dir, width=80).grid(row=r, column=1, **pad)
        ttk.Button(f, text="...", width=3, command=lambda: self.browse_dir(self.b_out_dir)).grid(row=r, column=2, **pad); r+=1

        vid_frame = ttk.LabelFrame(f, text="Видеодорожка для всех файлов")
        vid_frame.grid(row=r, column=0, columnspan=3, sticky='we', **pad); r+=1
        self.b_video_mode = tk.StringVar(value="none")
        ttk.Radiobutton(vid_frame, text="Без видео (только аудио)", variable=self.b_video_mode, value="none").pack(anchor='w')
        ttk.Radiobutton(vid_frame, text="Копировать видео (если есть)", variable=self.b_video_mode, value="copy").pack(anchor='w')
        ttk.Radiobutton(vid_frame, text="Создать визуализацию", variable=self.b_video_mode, value="visual").pack(anchor='w')
        
        af = ttk.LabelFrame(f, text="Аудио (LPCM)")
        af.grid(row=r, column=0, columnspan=3, sticky='we', **pad); r+=1
        ttk.Label(af, text="Битность:").grid(row=0, column=0, sticky='w', padx=5)
        self.b_bit = tk.StringVar(value="Auto"); ttk.Combobox(af, textvariable=self.b_bit, values=["Auto","16","24","32"], state='readonly', width=8).grid(row=0, column=1, sticky='w', padx=5)
        ttk.Label(af, text="Частота:").grid(row=0, column=2, sticky='w', padx=5)
        self.b_rate = tk.StringVar(value="Auto"); ttk.Combobox(af, textvariable=self.b_rate, values=["Auto","44100","48000","96000"], state='readonly', width=10).grid(row=0, column=3, sticky='w', padx=5)
        
        ttk.Label(f, text="Поиск CUE: скрипт автоматически ищет файл .cue с тем же именем для разбивки на треки.", foreground="gray").grid(row=r, column=0, columnspan=3, sticky='w', **pad); r+=1

        bf = ttk.Frame(f); bf.grid(row=r, column=0, columnspan=3, pady=15); r+=1
        self.btn_batch_start = ttk.Button(bf, text="▶ Запустить пакетную обработку", command=lambda: self.start_task(self.run_batch))
        self.btn_batch_start.pack(side='left', padx=5)
        self.btn_batch_stop = ttk.Button(bf, text="⏹ Стоп", command=self.stop_processing, state='disabled')
        self.btn_batch_stop.pack(side='left', padx=5)

    def run_batch(self):
        in_dir = self.b_in_dir.get()
        out_dir = self.b_out_dir.get() or in_dir
        
        if not in_dir or not os.path.isdir(in_dir):
            raise Exception("Выберите корректную исходную папку")
        if not os.path.exists(out_dir):
            os.makedirs(out_dir)

        files = [f for f in os.listdir(in_dir) if f.lower().endswith(SUPPORTED_EXTS)]
        if not files:
            self.log_msg("В папке не найдено поддерживаемых файлов.")
            return

        self.clear_log()
        self.log_msg(f"Найдено файлов для обработки: {len(files)}")
        
        for idx, filename in enumerate(files, 1):
            if self.stop_event.is_set():
                break
                
            inp_path = os.path.join(in_dir, filename)
            base_name = os.path.splitext(filename)[0]
            out_name = f"{base_name}_lpcm.mkv" if filename.lower().endswith('.mkv') else f"{base_name}.mkv"
            out_path = os.path.join(out_dir, out_name)

            self.log_msg(f"\n[{idx}/{len(files)}] Обработка: {filename}")
            
            cue_path = os.path.splitext(inp_path)[0] + ".cue"
            use_cue = os.path.exists(cue_path)
            
            try:
                self.process_file(inp_path, out_path, self.b_video_mode.get(), 
                                  base_name, "Full Album", "1280x720", 
                                  self.b_bit.get(), self.b_rate.get(), use_cue, cue_path if use_cue else "")
            except Exception as e:
                if "Остановлено" in str(e):
                    self.log_msg("  ⏹️ Остановлено пользователем.")
                    break
                self.log_msg(f"  ❌ Ошибка при обработке {filename}: {e}")
        
        if not self.stop_event.is_set():
            self.log_msg("\n✅ Пакетная обработка завершена.")

    # =================================================================
    # ОБЩАЯ ЛОГИКА ОБРАБОТКИ
    # =================================================================
    def prepare_source(self, inp, log_func):
        try:
            d = probe(inp)
            streams = d.get("streams", [])
            audio = next((s for s in streams if s.get("codec_type")=="audio"), None)
            if not audio: return inp
            
            codec = audio.get("codec_name", "")
            ext = os.path.splitext(inp)[1].lower()
            
            raw_ext = None
            if codec == "mlp" or ext == ".aob": raw_ext = ".mlp"
            elif codec == "dts" or ext in (".dts", ".wav") and codec == "dts": raw_ext = ".dts"
            
            if raw_ext:
                log_func(f"  [Подготовка] Извлечение {raw_ext} из {os.path.basename(inp)}...")
                tmp_dir = tempfile.gettempdir()
                raw_file = os.path.join(tmp_dir, f"gui_temp_{threading.get_ident()}{raw_ext}")
                wav_file = os.path.join(tmp_dir, f"gui_temp_{threading.get_ident()}.wav")
                
                cmd1 = [FFMPEG, "-y", "-i", inp, "-c:a", "copy", "-f", raw_ext[1:], raw_file]
                self.run_ffmpeg(cmd1)
                
                log_func(f"  [Подготовка] Конвертация в WAV...")
                cmd2 = [FFMPEG, "-y", "-i", raw_file, "-c:a", "pcm_s24le", wav_file]
                self.run_ffmpeg(cmd2)
                
                if os.path.exists(raw_file): os.remove(raw_file)
                return wav_file
        except Exception as e:
            log_func(f"  [Предупреждение] Не удалось подготовить {inp}: {e}")
        return inp

    def analyze_mood(self, inp, sample_sec=120):
        tmp_wav = os.path.join(tempfile.gettempdir(), f"mood_{threading.get_ident()}.wav")
        try:
            cmd = [FFMPEG, "-y", "-ss", "0", "-t", str(sample_sec), "-i", inp, "-c:a", "pcm_s16le", "-ar", "22050", "-ac", "1", tmp_wav]
            self.run_ffmpeg(cmd)
            y, _ = librosa.load(tmp_wav, sr=22050)
            cent = float(np.mean(librosa.feature.spectral_centroid(y=y, sr=22050)[0]))
            rms  = float(np.mean(librosa.feature.rms(y=y)[0]))
            return cent, rms
        finally:
            if os.path.exists(tmp_wav): os.remove(tmp_wav)

    def process_file(self, inp, outp, video_mode, album, track, res, bit, rate, use_cue, cue_path):
        temp_wav = None
        temp_meta = None
        try:
            self.log_msg(f"Анализ: {os.path.basename(inp)}")
            prepared_inp = self.prepare_source(inp, self.log_msg)
            if prepared_inp != inp: temp_wav = prepared_inp
            inp = prepared_inp

            d = probe(inp)
            audio = next((s for s in d.get("streams",[]) if s.get("codec_type")=="audio"), None)
            if not audio: raise Exception("Нет аудиопотоков")

            bd = int(bit) if bit != "Auto" else detect_bit_depth(audio)
            lpcm = choose_lpcm(bd)
            sr = int(rate) if rate != "Auto" else int(audio.get("sample_rate", 48000))
            ch = audio.get("channels", 2)

            if use_cue and os.path.exists(cue_path):
                temp_meta = make_chapters_xml(cue_path, inp)

            if video_mode == "visual":
                self._run_visual_fancy(inp, outp, d, lpcm, sr, ch, temp_meta, album, track, res, cue_path)
            else:
                self._run_plain(inp, outp, d, lpcm, sr, ch, temp_meta, copy_video=(video_mode=="copy"))
                
        except Exception as e:
            if "Остановлено" in str(e):
                self.log_msg("  ⏹️ Процесс остановлен.")
                raise e
            self.log_msg(f"❌ Ошибка: {e}")
        finally:
            if temp_wav and os.path.exists(temp_wav): os.remove(temp_wav)
            if temp_meta and os.path.exists(temp_meta): os.remove(temp_meta)

    def _run_plain(self, inp, outp, d, lpcm, sr, ch, meta, copy_video):
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
        self.log_msg(f"  Конвертация...")
        self.run_ffmpeg(cmd)
        self.log_msg(f"  ✅ Сохранено: {os.path.basename(outp)}")

    def _run_visual_fancy(self, inp, outp, d, lpcm, sr, ch, meta, album, track, res, cue_path=None):
        if not cue_path or not os.path.exists(cue_path):
            duration = float(d["format"]["duration"])
            tracks = [{"num": 1, "title": track or "Full Album", "start": 0.0}]
        else:
            tracks = parse_cue(cue_path)
            
        if not tracks:
            self.log_msg("CUE пуст или не распознан"); return
        total = len(tracks)
        duration = float(d["format"]["duration"])

        if total == 1:
            self.log_msg("  Генерация сплошной визуализации...")
            cent, rms = self.analyze_mood(inp, sample_sec=min(60, int(duration)))
            rr, gg, bb = mood_to_color(cent, rms)
            hexc = f"0x{rr:02x}{gg:02x}{bb:02x}"
            elapsed, total_fmt = timer_strings(duration)
            album = album or "Album"
            t = tracks[0]['title']

            vf = (
                f"drawtext=fontfile='{FONT}':text='{album}':fontcolor=white:fontsize=48:x=(w-text_w)/2:y=(h-text_h)/2-80,"
                f"drawtext=fontfile='{FONT}':text='{t}':fontcolor=white:fontsize=36:x=(w-text_w)/2:y=(h-text_h)/2+20,"
                f"drawtext=fontfile='{FONT}':text='{elapsed} / {total_fmt}':fontcolor=white:fontsize=36:x=(w-text_w)/2:y=(h-text_h)/2+80"
            )

            cmd = [FFMPEG, "-y", "-f", "lavfi", "-i", f"color=c={hexc}:s={res}:r=10", "-i", inp]
            if meta: cmd += ["-i", meta]

            cmd += ["-map", "0:v", "-map", "1:a:0", "-vf", vf,
                    "-c:v", "libx264", "-preset", "ultrafast", "-tune", "stillimage",
                    "-crf", "23", "-pix_fmt", "yuv420p",
                    "-c:a", lpcm, "-ar", str(sr), "-ac", str(ch)]
            if meta: cmd += ["-map_metadata", "2", "-map_chapters", "2"]
            cmd += ["-shortest", outp]

            self.run_ffmpeg(cmd)
            self.log_msg(f"  ✅ Сохранено: {os.path.basename(outp)}")
            return

        tmpdir = tempfile.mkdtemp()
        analysis = []
        try:
            for i, t in enumerate(tracks):
                if self.stop_event.is_set(): raise Exception("Остановлено пользователем")
                s, e = t["start"], (tracks[i+1]["start"] if i+1<total else duration)
                self.log_msg(f"  Анализ {t['num']:02d} '{t['title']}'...")
                tmp_wav_seg = os.path.join(tmpdir, f"seg{i}.wav")
                cmd = [FFMPEG,"-y","-ss",str(s),"-t",str(e-s),"-i",inp,"-c:a","pcm_s16le","-ar","44100","-ac","1",tmp_wav_seg]
                self.run_ffmpeg(cmd)
                y,_ = librosa.load(tmp_wav_seg, sr=44100)
                cent = float(np.mean(librosa.feature.spectral_centroid(y=y, sr=44100)[0]))
                rms  = float(np.mean(librosa.feature.rms(y=y)[0]))
                analysis.append({"centroid":cent,"rms":rms})
        finally:
            for fn in os.listdir(tmpdir):
                try: os.remove(os.path.join(tmpdir, fn))
                except: pass
            try: os.rmdir(tmpdir)
            except: pass

        order = sorted(range(total), key=lambda i: analysis[i]["centroid"])
        ranks = [0]*total
        for r_i, i in enumerate(order): ranks[i] = r_i

        colors = []
        for i in range(total):
            hue = ranks[i] / max(total-1,1)
            sat = float(np.clip(analysis[i]["rms"]*5.0, 0.4, 1.0))
            rr,gg,bb = colorsys.hsv_to_rgb(hue, sat, 0.4)
            colors.append((int(rr*255), int(gg*255), int(bb*255)))
            self.log_msg(f"  Трек {tracks[i]['num']:02d}: rank={ranks[i]} RGB={colors[i]}")

        segments = []
        for i,t in enumerate(tracks):
            if self.stop_event.is_set(): raise Exception("Остановлено пользователем")
            s, e = t["start"], (tracks[i+1]["start"] if i+1<total else duration)
            dur = e - s
            seg = os.path.join(tempfile.gettempdir(), f"vis_part_{threading.get_ident()}_{i:02d}.mkv")
            rr,gg,bb = colors[i]
            hexc = f"0x{rr:02x}{gg:02x}{bb:02x}"
            elapsed, total_fmt = timer_strings(dur)

            title = f"{t['num']:02d}. {t['title']}"
            album = album or "Album"

            vf = (
                f"drawtext=fontfile='{FONT}':text='{album}':fontcolor=white:fontsize=48:x=(w-text_w)/2:y=(h-text_h)/2-80,"
                f"drawtext=fontfile='{FONT}':text='{title}':fontcolor=white:fontsize=36:x=(w-text_w)/2:y=(h-text_h)/2+20,"
                f"drawtext=fontfile='{FONT}':text='{elapsed} / {total_fmt}':fontcolor=white:fontsize=36:x=(w-text_w)/2:y=(h-text_h)/2+80"
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
            self.log_msg(f"  Сегмент {i+1}/{total}...")
            self.run_ffmpeg(cmd)
            segments.append(seg)

        concat = os.path.join(tempfile.gettempdir(), f"vis_concat_{threading.get_ident()}.txt")
        with open(concat,"w",encoding="utf-8") as f:
            for sg in segments: f.write(f"file '{sg}'\n")
        self.log_msg("  Склейка...")
        self.run_ffmpeg([FFMPEG,"-y","-f","concat","-safe","0","-i",concat,"-c","copy",outp])

        for sg in segments:
            if os.path.exists(sg): os.remove(sg)
        if os.path.exists(concat): os.remove(concat)
        self.log_msg(f"  ✅ Сохранено: {os.path.basename(outp)}")

# =====================================================================
if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()