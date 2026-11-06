# -*- coding: utf-8 -*-
"""Минималистичный GUI для make_demo.py (стандартный tkinter, без зависимостей).

Запуск:  python make_demo_gui.py
"""
import json
import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import make_demo

SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "make_demo_gui.json")

RESOLUTIONS = ["1280x720", "1920x1080", "1280x540", "640x480", "640x360"]
FPS = ["10", "15", "24", "25", "30"]
PRESETS = ["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"]
ACODECS = [  # (отображаемое имя, значение для CONFIG)
    ("Авто (по битности)", None),
    ("LPCM 16 bit", "pcm_s16le"),
    ("LPCM 24 bit", "pcm_s24le"),
    ("LPCM 32 bit", "pcm_s32le"),
    ("AC3 (с потерями)", "ac3"),
]
CODEC_BY_NAME = dict(ACODECS)
NAME_BY_CODEC = {v: l for l, v in ACODECS}

DEFAULTS = dict(make_demo.CONFIG)
DEFAULTS["fps"] = str(DEFAULTS["fps"])
DEFAULTS["crf"] = str(DEFAULTS["crf"])
DEFAULTS["preset"] = "ultrafast"
DEFAULTS["audio_bitrate"] = "192k"


class App:
    def __init__(self, root):
        self.root = root
        self.q = queue.Queue()
        self.running = False
        self.vars = {}

        root.title("make_demo — WAV + CUE → демо MKV")
        root.minsize(600, 440)

        outer = ttk.Frame(root, padding=8)
        outer.pack(fill="both", expand=True)

        left = ttk.Frame(outer)
        left.pack(side="left", fill="y", padx=(0, 8))
        right = ttk.Frame(outer)
        right.pack(side="left", fill="both", expand=True)

        # ---------- настройки ----------
        def path_row(label, key, mode, patterns=(("Все файлы", "*.*"),)):
            ttk.Label(left, text=label).grid(sticky="w", pady=(6, 0))
            row = ttk.Frame(left)
            row.grid(sticky="ew", pady=2)
            var = self.vars[key] = tk.StringVar(value=str(DEFAULTS.get(key, "")))
            ttk.Entry(row, textvariable=var, width=50).pack(side="left", fill="x", expand=True)

            def browse():
                if mode == "save":
                    p = filedialog.asksaveasfilename(initialfile=var.get(), defaultextension=".mkv")
                else:
                    p = filedialog.askopenfilename(initialfile=var.get(), filetypes=list(patterns))
                if p:
                    var.set(os.path.normpath(p))

            ttk.Button(row, text="…", width=3, command=browse).pack(side="left", padx=(4, 0))

        path_row("WAV-файл", "wav_path", "open", (("WAV", "*.wav"), ("Все файлы", "*.*")))
        path_row("CUE-файл", "cue_path", "open", (("CUE", "*.cue"), ("Все файлы", "*.*")))
        path_row("Выходной MKV", "output_mkv", "save")
        path_row("ffmpeg.exe", "ffmpeg", "open", (("ffmpeg.exe", "ffmpeg.exe"),))
        path_row("ffprobe.exe", "ffprobe", "open", (("ffprobe.exe", "ffprobe.exe"),))
        path_row("Шрифт для титров (TTF)", "font", "open", (("TTF", "*.ttf"),))

        ttk.Label(left, text="Название альбома (титр)").grid(sticky="w", pady=(6, 0))
        self.vars["album_title"] = tk.StringVar(value=DEFAULTS["album_title"])
        ttk.Entry(left, textvariable=self.vars["album_title"]).grid(sticky="ew", pady=2)

        vid = ttk.LabelFrame(left, text="Видео", padding=6)
        vid.grid(sticky="ew", pady=(8, 0))
        for col, text in enumerate(["Разрешение", "FPS", "CRF", "Preset"]):
            ttk.Label(vid, text=text).grid(row=0, column=col, sticky="w")
        for key, values, col, width in (("res", RESOLUTIONS, 0, 12), ("fps", FPS, 1, 6), ("preset", PRESETS, 3, 10)):
            var = self.vars[key] = tk.StringVar(value=str(DEFAULTS[key]))
            ttk.Combobox(vid, textvariable=var, values=values, width=width, state="readonly") \
                .grid(row=1, column=col, sticky="ew", padx=(0, 4))
        self.vars["crf"] = tk.StringVar(value=DEFAULTS["crf"])
        ttk.Spinbox(vid, from_=1, to=51, textvariable=self.vars["crf"], width=5) \
            .grid(row=1, column=2, sticky="w", padx=(0, 4))

        ac = ttk.LabelFrame(left, text="Аудио", padding=6)
        ac.grid(sticky="ew", pady=(8, 0))
        ttk.Label(ac, text="Кодек выходного трека").grid(sticky="w")
        self.ac_name = tk.StringVar(value=NAME_BY_CODEC[None])
        ac_combo = ttk.Combobox(ac, textvariable=self.ac_name, width=20, state="readonly",
                                values=[l for l, _ in ACODECS])
        ac_combo.grid(sticky="ew", pady=2)
        ac_combo.bind("<<ComboboxSelected>>", lambda e: self._toggle_bitrate())
        ttk.Label(ac, text="Битрейт (только AC3)").grid(sticky="w")
        self.vars["audio_bitrate"] = tk.StringVar(value=DEFAULTS["audio_bitrate"])
        self.br_entry = ttk.Entry(ac, textvariable=self.vars["audio_bitrate"], width=8)
        self.br_entry.grid(sticky="w", pady=2)
        self._toggle_bitrate()

        btns = ttk.Frame(left)
        btns.grid(sticky="ew", pady=(10, 0))
        self.start_btn = ttk.Button(btns, text="Старт", command=self.start)
        self.start_btn.pack(side="left", fill="x", expand=True)
        ttk.Button(btns, text="Сброс", command=self.reset).pack(side="left", padx=(6, 0))
        left.columnconfigure(0, weight=1)

        # ---------- лог ----------
        self.log = tk.Text(right, bg="#101010", fg="#c8c8c8", insertbackground="#c8c8c8",
                           wrap="word", state="disabled", font=("Consolas", 9))
        scroll = ttk.Scrollbar(right, command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        self.log.pack(side="left", fill="both", expand=True)
        scroll.pack(side="left", fill="y")

        # ---------- индикатор прогресса ----------
        self.stage_label = ttk.Label(outer, text="Ожидание запуска…", anchor="w")
        self.stage_label.pack(fill="x", pady=(8, 0))
        self.bar = ttk.Progressbar(outer)
        self.bar.pack(fill="x", pady=(2, 0))

        self.load_settings()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.pump()

    # ---------- вспомогательное ----------
    def _toggle_bitrate(self):
        self.br_entry.configure(state="normal" if CODEC_BY_NAME[self.ac_name.get()] == "ac3" else "disabled")

    def build_cfg(self):
        cfg = {k: v.get() for k, v in self.vars.items()}
        cfg["audio_codec"] = CODEC_BY_NAME[self.ac_name.get()]
        cfg["audio_bitrate"] = self.vars["audio_bitrate"].get().strip() or None
        cfg["fps"] = int(cfg["fps"])
        cfg["crf"] = int(cfg["crf"])
        return cfg

    # ---------- запуск ----------
    def start(self):
        if self.running:
            return
        cfg = self.build_cfg()
        for key in ("wav_path", "cue_path", "ffmpeg", "ffprobe", "font"):
            if not os.path.isfile(cfg[key]):
                messagebox.showerror("Ошибка", f"Не найден файл ({key}):\n{cfg[key]}")
                return
        if not cfg["output_mkv"].lower().endswith(".mkv"):
            messagebox.showerror("Ошибка", "Выходной файл должен быть .mkv")
            return
        self.save_settings()
        self.running = True
        self.start_btn.configure(state="disabled")
        self.stage_label.configure(text="Подготовка…")
        self.bar.stop()
        self.bar.configure(mode="determinate", value=0, maximum=100)
        threading.Thread(target=self._worker, args=(cfg,), daemon=True).start()

    def _worker(self, cfg):
        try:
            make_demo.main(
                cfg,
                log=self.q.put,
                progress=lambda stage, done, total: self.q.put(("progress", stage, done, total)),
            )
        except Exception as e:
            self.q.put(f"\n[ОШИБКА] {e}")
        self.q.put(None)  # сигнал завершения

    def pump(self):
        self.log.configure(state="normal")
        try:
            while True:
                item = self.q.get_nowait()
                if item is None:
                    self.running = False
                    self.start_btn.configure(state="normal")
                    self.bar.stop()
                    self.bar.configure(mode="determinate")
                    self.stage_label.configure(text="Завершено (см. лог)")
                elif isinstance(item, tuple) and item[0] == "progress":
                    _, stage, done, total = item
                    if stage == "concat":
                        self.bar.stop()
                        self.bar.configure(mode="indeterminate")
                        self.bar.start(40)
                        self.stage_label.configure(text="Склейка сегментов…")
                    elif stage == "done":
                        self.bar.stop()
                        self.bar.configure(mode="determinate", value=self.bar["maximum"])
                        self.stage_label.configure(text="Готово")
                    elif stage == "analysis":
                        self.bar.configure(mode="determinate", maximum=total * 2, value=done)
                        pct = done / (total * 2) * 100
                        self.stage_label.configure(text=f"Анализ трека: {done} / {total} ({pct:.0f}%)")
                    elif stage == "segments":
                        self.bar.configure(mode="determinate", maximum=total * 2, value=total + done)
                        pct = (total + done) / (total * 2) * 100
                        self.stage_label.configure(text=f"Создание сегмента: {done} / {total} ({pct:.0f}%)")
                else:
                    self.log.insert("end", item + "\n")
        except queue.Empty:
            pass
        self.log.see("end")
        self.log.configure(state="disabled")
        self.root.after(120, self.pump)

    # ---------- настройки ----------
    def collect_settings(self):
        s = {k: v.get() for k, v in self.vars.items()}
        s["audio_codec"] = CODEC_BY_NAME[self.ac_name.get()]
        return s

    def save_settings(self):
        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(self.collect_settings(), f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    def load_settings(self):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                s = json.load(f)
        except (OSError, json.JSONDecodeError):
            return
        for key in ("ffmpeg", "ffprobe"):
            if not s.get(key) or not os.path.isfile(s[key]):
                s[key] = DEFAULTS[key]
        for k, v in s.items():
            if k in self.vars:
                self.vars[k].set(str(v))
        self.ac_name.set(NAME_BY_CODEC.get(s.get("audio_codec"), NAME_BY_CODEC[None]))
        self._toggle_bitrate()

    def reset(self):
        for k, v in DEFAULTS.items():
            if k in self.vars:
                self.vars[k].set(str(v))
        self.ac_name.set(NAME_BY_CODEC[None])
        self._toggle_bitrate()
        self.save_settings()

    def on_close(self):
        self.save_settings()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except tk.TclError:
        pass
    App(root)
    root.mainloop()
