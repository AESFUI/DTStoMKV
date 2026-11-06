# -*- coding: utf-8 -*-
"""Параллельный вариант make_demo.py: аудиодорожки конвертируются в AC3 (lossy).

Пайплайн тот же (CUE-тайминги, анализ треков, цветные сегменты с титрами, склейка),
отличается только кодек выходного аудио и имя выходного файла (*_ac3.mkv).

Замечание: AC3 поддерживает максимум 6 каналов и 48/44.1/32 кГц — при иных
параметрах входа ffmpeg сообщит об ошибке в логе.

Запуск:  python make_demo_ac3.py
"""
import make_demo

CONFIG = dict(make_demo.CONFIG)

# --- отличия от базовой версии ---
CONFIG["audio_codec"] = "ac3"    # конвертация дорожек в AC3 с потерями
CONFIG["audio_bitrate"] = "448k" # битрейт AC3 (для 5.1 обычно 448–640k)
CONFIG["flac_level"] = None      # для AC3 не используется
CONFIG["output_mkv"] = CONFIG["output_mkv"].replace(".mkv", "_ac3.mkv")

def main(cfg=None, log=print):
    make_demo.main(cfg or CONFIG, log)


if __name__ == "__main__":
    print(f"Сборка демо-диска с аудио AC3 (битрейт {CONFIG['audio_bitrate']})")
    print(f"Выход: {CONFIG['output_mkv']}\n")
    make_demo.main(CONFIG)
