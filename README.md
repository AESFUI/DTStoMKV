# DTStoMKV

Конвертер аудио в Matroska для **Linux и Windows**, Python 3.10+.
Общий движок используется графическим интерфейсом Tkinter и командной строкой.

Возможности:

- LPCM 16/24/32 bit, FLAC, AC3 и TrueHD; выбор частоты, сохранение числа каналов.
- Конвертация без видео, копирование первой видеодорожки или цветная визуализация H.264.
- Главы и названия треков из однофайлового CUE, UTF-8/BOM и Windows-1251.
- Визуализация с названием альбома, текущего трека, общим таймером и цветом по анализу аудио.
- Пакетная обработка папки, автоматический поиск одноимённого CUE, остановка обработки.
- Существующие результаты защищены от перезаписи по умолчанию; при отмене временные файлы удаляются.

Обрабатывается первый аудиопоток; в режиме копирования — первый видеопоток.
Субтитры, вложения и дополнительные аудиопотоки в результат не переносятся.
Частые входные форматы: WAV (включая DTS-CD), DTS, MLP, AOB, MKV, MKA, TS, FLAC, AC3.
Доступность декодеров и кодировщиков определяется установленной сборкой FFmpeg.

## Linux

Пример для Debian/Ubuntu:

```bash
sudo apt install python3-venv python3-tk ffmpeg fonts-dejavu-core
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m dtstomkv gui
```

Для других дистрибутивов установите Python с Tkinter, FFmpeg/ffprobe и TTF-шрифт
с кириллицей средствами своего пакетного менеджера.

## Windows

Установите Python 3.10+ с компонентом Tcl/Tk и FFmpeg. Добавьте каталог `bin`,
содержащий `ffmpeg.exe` и `ffprobe.exe`, в PATH. В PowerShell из папки проекта:

```powershell
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -e .
.venv\Scripts\python.exe -m dtstomkv gui
```

Старый запуск `python DTStoMKV_gui.py` также работает на обеих ОС.

## Командная строка

```bash
python -m dtstomkv convert input.dts output.mkv
python -m dtstomkv convert input.mkv output.mkv --video copy --bit-depth 24
python -m dtstomkv convert album.wav album.mkv --video visual --cue album.cue --album "Название альбома"
python -m dtstomkv convert input.wav output.mkv --codec flac --flac-level 8
python -m dtstomkv convert input.wav output.mkv --codec ac3 --bitrate 640k --sample-rate 48000
python -m dtstomkv convert input.wav output.mkv --codec truehd --sample-rate 48000
python -m dtstomkv convert --help
```

Для разрешённой перезаписи добавьте `--overwrite` или включите соответствующий
флажок в GUI. `Ctrl+C` останавливает CLI и завершает дочерний процесс.
В GUI закрытие окна во время работы сначала останавливает FFmpeg и очищает временные файлы.
Выходной файл не может совпадать с входным.

FFmpeg и ffprobe ищутся через PATH. Произвольные пути задаются переменными
`DTSTOMKV_FFMPEG`, `DTSTOMKV_FFPROBE`, `DTSTOMKV_FONT` либо параметрами CLI
`--ffmpeg`, `--ffprobe`, `--font`. В GUI используются те же переменные окружения.
Автоматический шрифт: Arial в Windows, DejaVu Sans в Linux.
Визуализация требует фильтр `drawtext` и кодировщик `libx264`.

Для AC3 поддерживаются до 6 каналов и частоты 32/44.1/48 кГц. TrueHD включается
с `-strict experimental`; приложение ограничивает его шестью каналами и частотами
44.1/48 кГц. Автоматического понижения частоты или числа каналов нет.
`Auto` сохраняет определённую FFprobe битность; если она неизвестна, для DTS,
MLP, TrueHD и FLAC выбираются 24 bit, для остальных — 16 bit.
Это выбор формата PCM, а не восстановление информации, отсутствующей в источнике.

Пакетный режим обрабатывает только выбранную папку, без рекурсии. WAV/DTS и другие
аудиофайлы получают имя `<имя>.mkv`, входные MKV — `<имя>_lpcm.mkv` (историческое имя,
даже при выборе другого кодека). При совпадении входной и выходной папок уже
созданные результаты исключаются из списка, чтобы не конвертировать их повторно.

## Архитектура и очистка

| Файл | Назначение |
| --- | --- |
| `dtstomkv/core.py` | Опции, запуск/отмена FFmpeg, конвертация, анализ и визуализация |
| `dtstomkv/cue.py` | Разбор CUE, проверка таймингов, сериализация глав |
| `dtstomkv/runtime.py` | Поиск программ и шрифтов, параметры запуска для ОС |
| `dtstomkv/gui.py` | Три вкладки Tkinter; снимок настроек перед запуском потока, очередь событий |
| `dtstomkv/__main__.py` | CLI и запуск GUI |
| `DTStoMKV_gui.py` | Совместимый старый способ запуска |

Удалены ранние реализации `make_demo.py`, `make_demo_gui.py`, `make_demo_gui2.py`
и обёртки `make_demo_ac3.py`, `make_demo_flac.py`, `make_demo_truehd.py`.
Их кодеки объединены в общем движке; параметры FPS, CRF, preset, битрейта и уровня
FLAC доступны в CLI. Вместо склейки отдельных MKV визуализация создаётся за один
проход, аудио не режется на сегменты. Анализ цвета использует до 30 секунд каждого
трека и NumPy; зависимости librosa и SoundFile больше не нужны.
Титры читаются из текстовых файлов, поэтому кавычки, двоеточия и `%` не ломают фильтры.
Личный `make_demo_gui.json` с путями к диску D: и `convert-to-mkv.txt` с разовой
командой заменены примерами выше. Автосохранение настроек старого demo-GUI не переносится.

## Проверки

```bash
python -m unittest discover -s tests -v
```

Тесты генерируют короткие аудио/видеофайлы и проверяют все четыре кодека,
главы, кириллицу и спецсимволы, копирование видео из DTS-контейнера,
визуализацию, CLI, отмену и защиту выходных файлов. Нужны FFmpeg и ffprobe.
Без них интеграционные проверки пропускаются.
Отдельная проверка GUI: `python tests/gui_smoke.py` (нужен дисплей; Linux —
`xvfb-run -a python tests/gui_smoke.py`). Workflow GitHub Actions запускает проверки
на Linux и Windows с Python 3.10 и 3.13.
