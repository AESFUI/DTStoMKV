# -*- coding: utf-8 -*-
"""Параллельный вариант make_demo.py: аудиодорожки конвертируются в Dolby TrueHD (lossless).

Пайплайн тот же (CUE-тайминги, анализ треков, цветные сегменты с титрами, склейка),
отличается только кодек выходного аудио и имя выходного файла (*_truehd.mkv).

Особенности кодировщика TrueHD в ffmpeg (проверено на локальном ffmpeg):
  - требует флаг "-strict experimental" (добавляется автоматически);
  - поддерживает до 6 каналов (тест не прошёл на 8);
  - поддерживает 44.1/48 кГц (более высокие могут не пройти).

Для воспроизведения по HDMI ресиверу (Denon AVR-X1800H) нужен плеер с
битстримом/passthrough TrueHD (Kodi, mpv, Shield и т.п.).

Запуск:  python make_demo_truehd.py
"""
import make_demo

CONFIG = dict(make_demo.CONFIG)

# --- отличия от базовой версии ---
CONFIG["audio_codec"] = "truehd"    # конвертация дорожек в TrueHD без потерь
CONFIG["audio_bitrate"] = None      # для TrueHD не используется
CONFIG["flac_level"] = None         # для TrueHD не используется
CONFIG["output_mkv"] = CONFIG["output_mkv"].replace(".mkv", "_truehd.mkv")


def main(cfg=None, log=print):
    make_demo.main(cfg or CONFIG, log)


if __name__ == "__main__":
    print("Сборка демо-диска с аудио Dolby TrueHD (lossless, experimental encoder)")
    print(f"Выход: {CONFIG['output_mkv']}\n")
    main()
