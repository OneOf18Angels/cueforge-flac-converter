# created by Radko Viacheslav 21.09.2026

import argparse
from datetime import datetime
import json
import logging
import os
import re
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from cueparser import CueSheet
from pydub import AudioSegment
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn, TimeRemainingColumn
from rich.markup import escape

DEFAULT_CONFIG = {
    "workers": 4,
    "source": "",
    "dest": "",
    "logs_dir": "logs",
}
logger = logging.getLogger("flac_converter")


if getattr(sys, "frozen", False):
    AudioSegment.converter = os.path.join(sys._MEIPASS, "ffmpeg.exe")


def app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def config_path():
    return os.path.join(app_dir(), "config.json")


def load_config():
    config = DEFAULT_CONFIG.copy()
    try:
        with open(config_path(), encoding="utf-8") as config_file:
            loaded = json.load(config_file)
        if isinstance(loaded, dict):
            config.update({key: value for key, value in loaded.items() if key in config})
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    if not isinstance(config["workers"], int) or isinstance(config["workers"], bool) or config["workers"] < 1:
        config["workers"] = DEFAULT_CONFIG["workers"]
    for key in ("source", "dest", "logs_dir"):
        if not isinstance(config[key], str):
            config[key] = DEFAULT_CONFIG[key]
    return config


def save_config(source=None, dest=None, workers=None, logs_dir=None):
    config = load_config()
    if source is not None:
        config["source"] = source
    if dest is not None:
        config["dest"] = dest
    if workers is not None:
        config["workers"] = workers
    if logs_dir is not None:
        config["logs_dir"] = logs_dir
    with open(config_path(), "w", encoding="utf-8") as config_file:
        json.dump(config, config_file, ensure_ascii=False, indent=2)
    return config


def setup_logging(logs_dir):
    if not os.path.isabs(logs_dir):
        logs_dir = os.path.join(app_dir(), logs_dir)
    os.makedirs(logs_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    log_path = os.path.join(logs_dir, f"flac-converter_{timestamp}.log")
    logger.setLevel(logging.INFO)
    for old_handler in logger.handlers[:]:
        logger.removeHandler(old_handler)
        old_handler.close()
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] [Потік %(thread)d] %(message)s"))
    logger.addHandler(handler)
    logger.info("Запуск конвертації")
    return log_path


def album_log(album_name, message, *args, level=logging.INFO):
    logger.log(level, "[Альбом] %s | " + message, album_name, *args)


def update_album(progress, task_id, status, album_name, completed=None, total=None):
    description = f"[{status}] {escape(album_name)} (потік {threading.get_ident()})"
    progress.update(task_id, description=description, completed=completed, total=total)


def finish_album(progress, task_id, status, album_name, completed, total):
    update_album(progress, task_id, status, album_name, completed=completed, total=total)
    progress.remove_task(task_id)


def cue_time_to_seconds(value):
    minutes, seconds, frames = (int(part) for part in value.split(":"))
    return minutes * 60 + seconds + frames / 75


def safe_filename(value):
    value = re.sub(r'[<>:"/\\|?*]', "_", value)
    return value.rstrip(" .")


def read_cue_file(cue_path):
    with open(cue_path, "rb") as cue_file:
        data = cue_file.read()
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError("utf-8", data, 0, 1, "unsupported CUE file encoding")


def convert_album(album_path, dest_album, progress, task_id):
    album_name = os.path.basename(album_path)
    album_log(album_name, "Початок: %s", album_path)
    update_album(progress, task_id, "running", album_name)

    cue_files = [f for f in os.listdir(album_path) if f.lower().endswith(".cue")]
    if not cue_files:
        album_log(album_name, "CUE не знайдено: %s", album_path, level=logging.WARNING)
        finish_album(progress, task_id, "skipped", album_name, completed=1, total=1)
        return

    cue_path = os.path.join(album_path, cue_files[0])
    album_log(album_name, "Читання CUE: %s", cue_path)

    cue = CueSheet()
    cue.setOutputFormat("%performer% - %title%")
    cue.setData(read_cue_file(cue_path))
    cue.parse()
    update_album(progress, task_id, "running", album_name, completed=0, total=len(cue.tracks))

    # Визначаємо вихідний аудіофайл
    audio_file = None
    for f in os.listdir(album_path):
        if f.lower().endswith((".wav", ".flac", ".ape", ".wv", ".mp3")):
            audio_file = os.path.join(album_path, f)
            break

    if not audio_file:
        album_log(album_name, "Аудіофайл не знайдено: %s", album_path, level=logging.WARNING)
        finish_album(
            progress,
            task_id,
            "skipped",
            album_name,
            completed=0,
            total=len(cue.tracks) or 1,
        )
        return

    album_log(album_name, "Відкриття аудіо: %s", audio_file)
    audio = AudioSegment.from_file(audio_file)

    os.makedirs(dest_album, exist_ok=True)

    # Пропуск, якщо вже є FLAC
    if sum(f.lower().endswith(".flac") for f in os.listdir(dest_album)) >= len(cue.tracks):
        album_log(album_name, "Пропущено, FLAC вже існують")
        finish_album(
            progress,
            task_id,
            "skipped",
            album_name,
            completed=len(cue.tracks),
            total=len(cue.tracks),
        )
        return

    for track in cue.tracks:
        title = track.title or f"Track {track.number}"
        performer = track.performer or cue.performer or "Unknown"

        start = cue_time_to_seconds(track.offset)
        end = None

        # Визначаємо кінець треку
        next_track = cue.tracks[track.number] if track.number < len(cue.tracks) else None
        if next_track:
            end = cue_time_to_seconds(next_track.offset)
        else:
            end = audio.duration_seconds

        start_ms = int(start * 1000)
        end_ms = int(end * 1000)

        segment = audio[start_ms:end_ms]

        filename = safe_filename(f"{performer} - {track.number:02d} {title}.flac")
        dest_file = os.path.join(dest_album, filename)

        segment.export(dest_file, format="flac")
        album_log(album_name, "Трек готовий: %s", dest_file)
        update_album(progress, task_id, "running", album_name, completed=track.number)

    finish_album(
        progress,
        task_id,
        "done",
        album_name,
        completed=len(cue.tracks),
        total=len(cue.tracks),
    )
    album_log(album_name, "Завершено")


def run_conversion(source_root, dest_root, workers=None, progress=None):
    logger.info("Параметри: source=%s, dest=%s, workers=%s", source_root, dest_root, workers)
    os.makedirs(dest_root, exist_ok=True)

    album_tasks = [
        (
            directory,
            os.path.join(dest_root, os.path.relpath(directory, source_root)),
        )
        for directory, _, files in os.walk(source_root)
        if any(file.lower().endswith(".cue") for file in files)
    ]
    if not album_tasks:
        return

    max_workers = min(workers or len(album_tasks), len(album_tasks))
    if progress is None:
        progress_columns = [
            SpinnerColumn(),
            TextColumn("{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TimeRemainingColumn(),
        ]
        progress_context = Progress(*progress_columns)
    else:
        progress_context = progress

    with progress_context as progress:
        task_ids = [
            progress.add_task(f"[waiting] {escape(os.path.basename(album_path))}", total=1)
            for album_path, _ in album_tasks
        ]
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(convert_album, album_path, dest_album, progress, task_id): task_id
                for (album_path, dest_album), task_id in zip(album_tasks, task_ids)
            }
            task_names = {
                task_id: os.path.basename(album_path)
                for (album_path, _), task_id in zip(album_tasks, task_ids)
            }
            for future in as_completed(futures):
                task_id = futures[future]
                try:
                    future.result()
                except Exception as error:
                    album_name = task_names[task_id]
                    logger.exception("[Альбом] %s | Помилка конвертації: %s", album_name, error)
                    update_album(progress, task_id, "error", album_name, completed=0)
                    progress.console.print(f"[red]Помилка в '{album_name}': {error}[/red]")
                    progress.remove_task(task_id)


def main(source_root, dest_root, workers=None):
    run_conversion(source_root, dest_root, workers)


def cli():
    parser = argparse.ArgumentParser(description="Конвертація CUE-альбомів у FLAC")
    parser.add_argument("source", nargs="?", help="Папка з альбомами та CUE-файлами")
    parser.add_argument("dest", nargs="?", help="Папка для готових FLAC-файлів")
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="Максимальна кількість одночасних потоків (за замовчуванням: для кожного альбому)",
    )
    parser.add_argument("--logs", default=None, help="Папка для логів")
    args = parser.parse_args()
    config = load_config()
    source = args.source or config["source"]
    dest = args.dest or config["dest"]
    workers = args.workers if args.workers is not None else config["workers"]
    logs_dir = args.logs or config["logs_dir"]

    if not source:
        parser.error("вкажіть source або збережіть його в config.json")
    if not dest:
        parser.error("вкажіть dest або збережіть його в config.json")
    if not os.path.isdir(source):
        parser.error(f"source папка не існує: {source}")
    if workers < 1:
        parser.error("--workers має бути додатним числом")

    save_config(source, dest, workers, logs_dir)
    setup_logging(logs_dir)
    main(source, dest, workers)


if __name__ == "__main__":
    cli()
