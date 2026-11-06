# -*- coding: utf-8 -*-
"""Параллельный вариант make_demo.py: аудиодорожки конвертируются в FLAC (lossless).

Пайплайн тот же (CUE-тайминги, анализ треков, цветные сегменты с титрами, склейка),
отличается только кодек выходного аудио и имя выходного файла (*_flac.mkv).

Запуск:  python make_demo_flac.py
"""
import make_demo

CONFIG = dict(make_demo.CONFIG)

# --- отличия от базовой версии ---
CONFIG["audio_codec"] = "flac"   # конвертация дорожек в FLAC без потерь
CONFIG["flac_level"] = 8         # уровень сжатия FLAC, 0..8 (8 = максимальное сжатие)
CONFIG["audio_bitrate"] = None   # для FLAC не используется
CONFIG["output_mkv"] = CONFIG["output_mkv"].replace(".mkv", "_flac.mkv")

def main(cfg=None, log=print):
    make_demo.main(cfg or CONFIG, log)


if __name__ == "__main__":
    print(f"Сборка демо-диска с аудио FLAC (уровень сжатия {CONFIG['flac_level']})")
    print(f"Выход: {CONFIG['output_mkv']}\n")
    make_demo.main(CONFIG)
