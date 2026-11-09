"""Tkinter front end. All widget access stays on the main thread."""
import queue
import threading
import tkinter as tk
from dataclasses import replace
from pathlib import Path
from tkinter import ttk, filedialog, messagebox, scrolledtext

from .core import Converter, Options, Cancelled, SUPPORTED_EXTS, default_output, detect_bit_depth


class App:
    def __init__(self, root):
        self.root = root
        root.title("Audio → MKV — Linux / Windows")
        root.geometry("980x850")
        self.stop_event = threading.Event()
        self.is_running = False
        self.closing = False
        self.events = queue.Queue()
        self.nb = ttk.Notebook(root)
        self.nb.pack(fill="both", expand=True, padx=5, pady=5)
        self.tab_simple, self.tab_visual, self.tab_batch = [ttk.Frame(self.nb) for _ in range(3)]
        for tab, title in ((self.tab_simple, "Один файл"), (self.tab_visual, "Визуализация по CUE"),
                           (self.tab_batch, "Пакетная обработка")):
            self.nb.add(tab, text=title)
        self.build_simple()
        self.build_visual()
        self.build_batch()
        settings = ttk.Frame(root)
        settings.pack(fill="x", padx=5)
        ttk.Label(settings, text="Битрейт AC3:").pack(side="left")
        self.bitrate = tk.StringVar(value="448k")
        ttk.Combobox(settings, textvariable=self.bitrate, values=["192k", "384k", "448k", "640k"],
                     width=8, state="readonly").pack(side="left", padx=5)
        ttk.Label(settings, text="Максимум каналов:").pack(side="left", padx=5)
        self.max_channels = tk.StringVar(value="5.1")
        ttk.Combobox(settings, textvariable=self.max_channels, values=["5.1", "7.1"],
                     width=5, state="readonly").pack(side="left")
        self.overwrite = tk.BooleanVar(value=False)
        self.normalize_channels = tk.BooleanVar(value=True)
        ttk.Checkbutton(root, text="Назначать каналы до выбранного максимума, отсутствующие — тишина (моно и стерео сохраняются)",
                        variable=self.normalize_channels).pack(anchor="w")
        ttk.Checkbutton(root, text="Перезаписывать существующие выходные файлы", variable=self.overwrite).pack(anchor="w")
        self.log = scrolledtext.ScrolledText(root, height=10)
        self.log.pack(fill="both", expand=True, padx=5, pady=5)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(100, self.pump)

    def converter(self):
        return Converter(log=self.log_msg, stop_event=self.stop_event)

    def options(self, prefix, **kwargs):
        video = getattr(self, prefix + "_video_mode", None)
        return Options(codec=getattr(self, prefix + "_codec").get(),
                       bit_depth=getattr(self, prefix + "_bit").get(),
                       sample_rate=getattr(self, prefix + "_rate").get(),
                       normalize_channels=self.normalize_channels.get(),
                       max_channels={"5.1": 6, "7.1": 8}[self.max_channels.get()],
                       overwrite=self.overwrite.get(), bitrate=self.bitrate.get(),
                       **({"video": video.get()} | kwargs if video else kwargs))

    def validate_paths(self, source, output):
        if not source or not output:
            raise ValueError("Укажите входной и выходной файлы")

    def log_msg(self, message):
        self.events.put(("log", message))

    def clear_log(self):
        self.log.delete("1.0", "end")

    def browse_file(self, var):
        path = filedialog.askopenfilename()
        if path:
            var.set(path)

    def browse_dir(self, var):
        path = filedialog.askdirectory()
        if path:
            var.set(path)

    def browse_save(self, var):
        current = Path(var.get()).expanduser() if var.get() else None
        path = filedialog.asksaveasfilename(defaultextension=".mkv", filetypes=[("MKV", "*.mkv")],
                                          **({"initialfile": current.name, "initialdir": str(current.parent)} if current else {}))
        if path:
            var.set(path)

    def update_output_name(self, prefix, *_):
        source = getattr(self, prefix + "_in").get()
        output = getattr(self, prefix + "_out")
        previous = getattr(self, prefix + "_last_source", "")
        if source:
            directory = Path(source).expanduser().parent
            if output.get() and previous:
                current_directory = Path(output.get()).expanduser().parent
                if current_directory != Path(previous).expanduser().parent:
                    directory = current_directory
            output.set(str(default_output(source, directory)))
        else:
            output.set("")
        setattr(self, prefix + "_last_source", source)

    def start_task(self, factory):
        if self.is_running:
            return
        try:
            task = factory()  # Snapshot every Tk variable before starting a worker.
        except Exception as error:
            messagebox.showerror("Ошибка", str(error))
            return
        self.clear_log()
        self.stop_event.clear()
        self.is_running = True
        self.toggle_buttons("disable")
        threading.Thread(target=self._task_wrapper, args=(task,), daemon=False).start()

    def _task_wrapper(self, task):
        try:
            task()
        except Cancelled:
            self.log_msg("Обработка остановлена пользователем.")
        except Exception as error:
            self.log_msg(f"Ошибка: {error}")
        finally:
            self.events.put(("done", None))

    def toggle_buttons(self, state):
        for prefix in ("simple", "visual", "batch"):
            getattr(self, "btn_" + prefix + "_start").configure(state="normal" if state == "enable" else "disabled")
            getattr(self, "btn_" + prefix + "_stop").configure(state="disabled" if state == "enable" else "normal")

    def stop_processing(self):
        if self.is_running:
            self.stop_event.set()

    def pump(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "done":
                    self.is_running = False
                    self.toggle_buttons("enable")
                    self.stop_event.clear()
                else:
                    self.log.insert("end", value + "\n")
                    self.log.see("end")
        except queue.Empty:
            pass
        if self.closing and not self.is_running:
            self.root.destroy()
            return
        self.root.after(100, self.pump)

    def on_close(self):
        self.closing = True
        self.stop_processing()
        if not self.is_running:
            self.root.destroy()

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
        self.s_in.trace_add("write", lambda *_: self.update_output_name("s"))

        vid_frame = ttk.LabelFrame(f, text="Видеодорожка")
        vid_frame.grid(row=r, column=0, columnspan=3, sticky='we', **pad); r+=1
        self.s_video_mode = tk.StringVar(value="none")
        ttk.Radiobutton(vid_frame, text="Без видео", variable=self.s_video_mode, value="none", command=self.toggle_simple_visual).pack(anchor='w')
        ttk.Radiobutton(vid_frame, text="Копировать видео (без перекодирования)", variable=self.s_video_mode, value="copy", command=self.toggle_simple_visual).pack(anchor='w')
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

        af = ttk.LabelFrame(f, text="Аудио (битность применяется к LPCM)")
        af.grid(row=r, column=0, columnspan=3, sticky='we', **pad); r+=1
        ttk.Label(af, text="Битность:").grid(row=0, column=0, sticky='w', padx=5)
        self.s_bit = tk.StringVar(value="Auto"); ttk.Combobox(af, textvariable=self.s_bit, values=["Auto","16","24","32"], state='readonly', width=8).grid(row=0, column=1, sticky='w', padx=5)
        ttk.Label(af, text="Частота:").grid(row=0, column=2, sticky='w', padx=5)
        self.s_rate = tk.StringVar(value="Auto"); ttk.Combobox(af, textvariable=self.s_rate, values=["Auto","44100","48000","96000"], state='readonly', width=10).grid(row=0, column=3, sticky='w', padx=5)
        ttk.Label(af, text="Кодек:").grid(row=1, column=0, sticky='w', padx=5)
        self.s_codec = tk.StringVar(value="lpcm")
        ttk.Combobox(af, textvariable=self.s_codec, values=["lpcm", "flac", "ac3", "truehd"],
                     state='readonly', width=10).grid(row=1, column=1, sticky='w', padx=5)


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
            d = self.converter().probe(p)
            lines = []
            for s in d.get("streams", []):
                ct = s.get("codec_type")
                if ct == "audio":
                    depth = detect_bit_depth(s)
                    automatic = not any(int(s.get(key) or 0) > 0
                                        for key in ("bits_per_raw_sample", "bits_per_sample"))
                    depth_label = f"{depth} bit" + (" (Auto)" if automatic else "")
                    lines.append(f"AUDIO: {s.get('codec_name')} | {s.get('sample_rate')} Hz | {s.get('channels')} ch | {depth_label}")
                elif ct == "video":
                    lines.append(f"VIDEO: {s.get('codec_name')} | {s.get('width')}x{s.get('height')}")
            self.s_info.config(text="\n".join(lines))
            self.log_msg("\n".join(lines))
        except Exception as e: messagebox.showerror("Ошибка", str(e))

    def run_simple(self):
        options = self.options("s", cue=self.s_cue.get() if self.s_use_cue.get() else "",
                               album=self.s_album.get(), title=self.s_track_title.get(),
                               resolution=self.s_res.get())
        source, output = self.s_in.get(), self.s_out.get()
        self.validate_paths(source, output)
        return lambda: self.converter().convert(source, output, options)

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
        self.v_in.trace_add("write", lambda *_: self.update_output_name("v"))

        self.v_visualize = tk.BooleanVar(value=True)
        ttk.Checkbutton(f, text="Включить визуализацию (без нее просто добавит главы)", variable=self.v_visualize, command=self.toggle_visual_options).grid(row=r, column=0, columnspan=3, sticky='w', **pad); r+=1

        self.v_vis_frame = ttk.LabelFrame(f, text="Параметры визуализации")
        self.v_vis_frame.grid(row=r, column=0, columnspan=3, sticky='we', **pad); r+=1
        ttk.Label(self.v_vis_frame, text="Альбом:").grid(row=0, column=0, sticky='w', **pad)
        self.v_album = tk.StringVar(value="Unknown Album"); ttk.Entry(self.v_vis_frame, textvariable=self.v_album, width=50).grid(row=0, column=1, **pad)
        ttk.Label(self.v_vis_frame, text="Разрешение:").grid(row=1, column=0, sticky='w', **pad)
        self.v_res = tk.StringVar(value="1280x720"); ttk.Combobox(self.v_vis_frame, textvariable=self.v_res, values=["1280x720","1920x1080"], width=14).grid(row=1, column=1, sticky='w', **pad)

        af = ttk.LabelFrame(f, text="Аудио (битность применяется к LPCM)")
        af.grid(row=r, column=0, columnspan=3, sticky='we', **pad); r+=1
        ttk.Label(af, text="Битность:").grid(row=0, column=0, sticky='w', padx=5)
        self.v_bit = tk.StringVar(value="Auto"); ttk.Combobox(af, textvariable=self.v_bit, values=["Auto","16","24","32"], state='readonly', width=8).grid(row=0, column=1, sticky='w', padx=5)
        ttk.Label(af, text="Частота:").grid(row=0, column=2, sticky='w', padx=5)
        self.v_rate = tk.StringVar(value="Auto"); ttk.Combobox(af, textvariable=self.v_rate, values=["Auto","44100","48000","96000"], state='readonly', width=10).grid(row=0, column=3, sticky='w', padx=5)
        ttk.Label(af, text="Кодек:").grid(row=1, column=0, sticky='w', padx=5)
        self.v_codec = tk.StringVar(value="lpcm")
        ttk.Combobox(af, textvariable=self.v_codec, values=["lpcm", "flac", "ac3", "truehd"],
                     state='readonly', width=10).grid(row=1, column=1, sticky='w', padx=5)


        bf = ttk.Frame(f); bf.grid(row=r, column=0, columnspan=3, pady=10); r+=1
        self.btn_visual_start = ttk.Button(bf, text="▶ Создать MKV", command=lambda: self.start_task(self.run_visual))
        self.btn_visual_start.pack(side='left', padx=5)
        self.btn_visual_stop = ttk.Button(bf, text="⏹ Стоп", command=self.stop_processing, state='disabled')
        self.btn_visual_stop.pack(side='left', padx=5)

    def toggle_visual_options(self):
        if self.v_visualize.get(): self.v_vis_frame.grid()
        else: self.v_vis_frame.grid_remove()

    def run_visual(self):
        source, output, cue = self.v_in.get(), self.v_out.get(), self.v_cue.get()
        self.validate_paths(source, output)
        if not cue:
            raise ValueError("Выберите CUE файл")
        options = self.options("v", video="visual" if self.v_visualize.get() else "copy",
                               cue=cue, album=self.v_album.get(), resolution=self.v_res.get())
        return lambda: self.converter().convert(source, output, options)

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
        
        af = ttk.LabelFrame(f, text="Аудио (битность применяется к LPCM)")
        af.grid(row=r, column=0, columnspan=3, sticky='we', **pad); r+=1
        ttk.Label(af, text="Битность:").grid(row=0, column=0, sticky='w', padx=5)
        self.b_bit = tk.StringVar(value="Auto"); ttk.Combobox(af, textvariable=self.b_bit, values=["Auto","16","24","32"], state='readonly', width=8).grid(row=0, column=1, sticky='w', padx=5)
        ttk.Label(af, text="Частота:").grid(row=0, column=2, sticky='w', padx=5)
        self.b_rate = tk.StringVar(value="Auto"); ttk.Combobox(af, textvariable=self.b_rate, values=["Auto","44100","48000","96000"], state='readonly', width=10).grid(row=0, column=3, sticky='w', padx=5)
        ttk.Label(af, text="Кодек:").grid(row=1, column=0, sticky='w', padx=5)
        self.b_codec = tk.StringVar(value="lpcm")
        ttk.Combobox(af, textvariable=self.b_codec, values=["lpcm", "flac", "ac3", "truehd"],
                     state='readonly', width=10).grid(row=1, column=1, sticky='w', padx=5)

        
        ttk.Label(f, text="Поиск CUE: одноимённый .cue добавляет главы и названия треков.", foreground="gray").grid(row=r, column=0, columnspan=3, sticky='w', **pad); r+=1

        bf = ttk.Frame(f); bf.grid(row=r, column=0, columnspan=3, pady=15); r+=1
        self.btn_batch_start = ttk.Button(bf, text="▶ Запустить пакетную обработку", command=lambda: self.start_task(self.run_batch))
        self.btn_batch_start.pack(side='left', padx=5)
        self.btn_batch_stop = ttk.Button(bf, text="⏹ Стоп", command=self.stop_processing, state='disabled')
        self.btn_batch_stop.pack(side='left', padx=5)

    def run_batch(self):
        source = Path(self.b_in_dir.get()).expanduser()
        if not self.b_in_dir.get() or not source.is_dir():
            raise ValueError("Выберите корректную исходную папку")
        output = Path(self.b_out_dir.get()).expanduser() if self.b_out_dir.get() else source
        options = self.options("b")
        files = sorted(p for p in source.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS)
        # Do not feed outputs from previous runs back into the next run.
        generated = {default_output(p) for p in files if p.suffix.lower() != ".mkv"}
        if source.resolve() == output.resolve():
            files = [p for p in files if p not in generated]
            if any(p.suffix.lower() == ".mkv" for p in files):
                raise ValueError("Для входных MKV выберите другую папку сохранения: имя результата сохраняется.")
        destinations = {}
        for path in files:
            target = default_output(path, output).resolve()
            if target in destinations:
                raise ValueError(f"Совпадает выходное имя: {destinations[target].name} и {path.name} → {target.name}")
            destinations[target] = path
        cues = {p.stem.casefold(): p for p in source.iterdir() if p.suffix.lower() == ".cue"}

        def work():
            engine = self.converter()
            errors = 0
            self.log_msg(f"Найдено файлов: {len(files)}")
            for i, path in enumerate(files, 1):
                if self.stop_event.is_set():
                    raise Cancelled()
                cue = str(cues[path.stem.casefold()]) if path.stem.casefold() in cues else ""
                self.log_msg(f"[{i}/{len(files)}] {path.name}")
                try:
                    engine.convert(path, default_output(path, output), replace(options, cue=cue, album=path.stem))
                except Cancelled:
                    raise
                except Exception as error:
                    errors += 1
                    self.log_msg(f"Ошибка {path.name}: {error}")
            self.log_msg(f"Пакетная обработка завершена. Ошибок: {errors}")
        return work


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()
