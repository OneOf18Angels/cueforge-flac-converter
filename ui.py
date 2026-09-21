import os
import queue
import re
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from convert import app_dir, load_config, run_conversion, save_config, setup_logging

LOG_PATTERN = re.compile(
    r"^(?P<time>[^ ]+ [^ ]+) \[(?P<level>[^]]+)\] \[Потік (?P<thread>\d+)\] (?P<message>.*)$"
)
ALBUM_PATTERN = re.compile(r"^\[Альбом\] (?P<album>.*?) \| (?P<message>.*)$")


class LogViewer(tk.Toplevel):
    def __init__(self, parent, logs_dir):
        super().__init__(parent)
        self.title("Перегляд логів")
        self.geometry("1100x650")
        self.logs_dir = logs_dir if os.path.isabs(logs_dir) else os.path.join(app_dir(), logs_dir)
        self.records = []
        self.sort_column = "time"
        self.sort_reverse = False
        self.build_ui()
        self.refresh_files()

    def build_ui(self):
        toolbar = ttk.Frame(self)
        toolbar.pack(fill="x", padx=10, pady=10)
        ttk.Label(toolbar, text="Файл:").pack(side="left")
        self.file_var = tk.StringVar()
        self.file_box = ttk.Combobox(toolbar, textvariable=self.file_var, state="readonly", width=70)
        self.file_box.pack(side="left", padx=6)
        self.file_box.bind("<<ComboboxSelected>>", lambda _event: self.load_selected())
        ttk.Button(toolbar, text="Оновити", command=self.refresh_files).pack(side="left")
        ttk.Label(toolbar, text="Потік:").pack(side="left", padx=(18, 4))
        self.thread_var = tk.StringVar(value="Всі")
        self.thread_box = ttk.Combobox(toolbar, textvariable=self.thread_var, state="readonly", width=12)
        self.thread_box.pack(side="left")
        self.thread_box.bind("<<ComboboxSelected>>", lambda _event: self.render_records())

        columns = ("time", "level", "thread", "message")
        self.table = ttk.Treeview(self, columns=columns, show="headings")
        headings = {"time": "Час", "level": "Рівень", "thread": "Потік", "message": "Повідомлення"}
        widths = {"time": 180, "level": 80, "thread": 90, "message": 700}
        for column in columns:
            self.table.heading(column, text=headings[column], command=lambda key=column: self.sort_by(key))
            self.table.column(column, width=widths[column], anchor="w")
        scroll = ttk.Scrollbar(self, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)
        self.table.pack(side="left", fill="both", expand=True, padx=(10, 0), pady=(0, 10))
        scroll.pack(side="right", fill="y", padx=(0, 10), pady=(0, 10))

    def refresh_files(self):
        os.makedirs(self.logs_dir, exist_ok=True)
        files = sorted(
            (name for name in os.listdir(self.logs_dir) if name.lower().endswith(".log")),
            reverse=True,
        )
        self.file_box["values"] = files
        if files and self.file_var.get() not in files:
            self.file_var.set(files[0])
        if self.file_var.get():
            self.load_selected()

    def load_selected(self):
        path = os.path.join(self.logs_dir, self.file_var.get())
        try:
            with open(path, encoding="utf-8") as log_file:
                self.records = []
                active_albums = {}
                for line in log_file:
                    record = self.parse_line(line.rstrip())
                    if record:
                        record = self.restore_legacy_album(record, active_albums)
                        self.records.append(record)
        except OSError as error:
            messagebox.showerror("Помилка", str(error), parent=self)
            return
        threads = sorted({record[2] for record in self.records}, key=int)
        self.thread_box["values"] = ["Всі", *threads]
        self.thread_var.set("Всі")
        self.render_records()

    @staticmethod
    def parse_line(line):
        match = LOG_PATTERN.match(line)
        if not match:
            return None
        message = match["message"]
        album_match = ALBUM_PATTERN.match(message)
        if album_match:
            album = album_match["album"]
            message = album_match["message"]
        else:
            album = "Без групи"
        return (match["time"], match["level"], match["thread"], album, message)

    @staticmethod
    def restore_legacy_album(record, active_albums):
        time, level, thread, album, message = record
        if album != "Без групи":
            return record

        start_match = re.search(r"(?:Початок альбому:|Конвертую альбом:) (.+)$", message)
        skip_match = re.search(r"Пропускаю '(.+?)'", message)
        if start_match:
            album = os.path.basename(start_match.group(1))
            active_albums[thread] = album
        elif skip_match:
            album = skip_match.group(1)
            active_albums[thread] = album
        elif thread in active_albums:
            album = active_albums[thread]

        if message.startswith("Готово:") or skip_match:
            active_albums.pop(thread, None)
        return (time, level, thread, album, message)

    def render_records(self):
        self.table.delete(*self.table.get_children())
        records = self.records
        selected_thread = self.thread_var.get()
        if selected_thread != "Всі":
            records = [record for record in records if record[2] == selected_thread]
        groups = {}
        for record in records:
            groups.setdefault(record[3], []).append(record)
        index = {"time": 0, "level": 1, "thread": 2, "message": 4}[self.sort_column]
        groups = sorted(
            groups.items(),
            key=lambda item: min((record[index], record[0]) for record in item[1]),
            reverse=self.sort_reverse,
        )
        for album, album_records in groups:
            parent = self.table.insert(
                "", "end", values=("", "АЛЬБОМ", "", f"{album} ({len(album_records)} записів)"), open=True
            )
            child_records = sorted(
                album_records,
                key=lambda record: (record[index], record[0]),
                reverse=self.sort_reverse,
            )
            for record in child_records:
                self.table.insert(parent, "end", values=(record[0], record[1], record[2], record[4]))

    def sort_by(self, column):
        if self.sort_column == column:
            self.sort_reverse = not self.sort_reverse
        else:
            self.sort_column = column
            self.sort_reverse = False
        self.render_records()


class TkProgress:
    def __init__(self):
        self.events = queue.Queue()
        self.next_id = 0
        self.console = self

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def add_task(self, description, total=1):
        task_id = self.next_id
        self.next_id += 1
        self.events.put(("add", task_id, description, 0, total))
        return task_id

    def update(self, task_id, description=None, completed=None, total=None):
        self.events.put(("update", task_id, description, completed, total))

    def remove_task(self, task_id):
        self.events.put(("remove", task_id))

    def print(self, message):
        self.events.put(("log", message))


class ConverterApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("FLAC Converter")
        self.geometry("980x600")
        self.resizable(True, True)
        self.progress = TkProgress()
        config = load_config()
        self.config = config
        self.rows = {}
        self.running = False
        self.total_albums = 0
        self.finished_albums = 0
        self.build_ui()
        self.after(100, self.process_events)

    def build_ui(self):
        paths = ttk.LabelFrame(self, text="Папки")
        paths.pack(fill="x", padx=12, pady=12)

        self.source = self.path_row(paths, "Source:", 0, self.choose_source, self.config["source"])
        self.dest = self.path_row(paths, "Destination:", 1, self.choose_dest, self.config["dest"])
        self.logs = self.path_row(paths, "Логи:", 2, self.choose_logs, self.config["logs_dir"])

        controls = ttk.Frame(self)
        controls.pack(fill="x", padx=12, pady=(0, 8))
        ttk.Label(controls, text="Потоки:").pack(side="left")
        self.workers = tk.StringVar(value=str(self.config["workers"]))
        ttk.Spinbox(controls, from_=1, to=128, textvariable=self.workers, width=6).pack(side="left", padx=8)
        self.start_button = ttk.Button(controls, text="Запустити", command=self.start)
        self.start_button.pack(side="left")
        ttk.Button(controls, text="Перегляд логів", command=self.open_log_viewer).pack(side="left", padx=8)
        self.status = ttk.Label(controls, text="Готово")
        self.status.pack(side="right")

        overview = ttk.Frame(self)
        overview.pack(fill="x", padx=12, pady=(0, 10))
        self.overall_text = ttk.Label(overview, text="Загальний прогрес: 0/0")
        self.overall_text.pack(anchor="w")
        self.overall = ttk.Progressbar(overview, mode="determinate", maximum=1, value=0)
        self.overall.pack(fill="x", pady=(4, 0))

        columns = ("album", "status", "thread", "progress")
        self.table = ttk.Treeview(self, columns=columns, show="headings")
        headings = {"album": "Альбом", "status": "Стан", "thread": "Потік", "progress": "Прогрес"}
        widths = {"album": 560, "status": 100, "thread": 100, "progress": 100}
        for column in columns:
            self.table.heading(column, text=headings[column])
            self.table.column(column, width=widths[column], anchor="w")
        self.table.pack(fill="both", expand=True, padx=12, pady=(0, 12))

    def path_row(self, parent, label, row, command, initial_value=""):
        ttk.Label(parent, text=label).grid(row=row, column=0, padx=8, pady=6)
        value = tk.StringVar()
        value.set(initial_value)
        ttk.Entry(parent, textvariable=value).grid(row=row, column=1, sticky="ew", padx=8, pady=6)
        ttk.Button(parent, text="Огляд...", command=command).grid(row=row, column=2, padx=8, pady=6)
        parent.columnconfigure(1, weight=1)
        return value

    def choose_source(self):
        path = self.choose_directory(self.source, "Source папка")
        if path:
            self.source.set(path)

    def choose_dest(self):
        path = self.choose_directory(self.dest, "Destination папка")
        if path:
            self.dest.set(path)

    def choose_logs(self):
        path = self.choose_directory(self.logs, "Папка логів")
        if path:
            self.logs.set(path)

    @staticmethod
    def choose_directory(variable, title):
        current_path = variable.get().strip()
        if current_path and not os.path.isabs(current_path):
            current_path = os.path.join(app_dir(), current_path)
        initial_dir = current_path if os.path.isdir(current_path) else "::{20D04FE0-3AEA-1069-A2D8-08002B30309D}"
        return filedialog.askdirectory(title=title, initialdir=initial_dir)

    def open_log_viewer(self):
        LogViewer(self, self.logs.get().strip() or "logs")

    def start(self):
        source = self.source.get().strip()
        dest = self.dest.get().strip()
        logs_dir = self.logs.get().strip() or "logs"
        if not os.path.isdir(source):
            messagebox.showerror("Помилка", "Source папка не існує")
            return
        if os.path.exists(dest) and not os.path.isdir(dest):
            messagebox.showerror("Помилка", "Destination має бути папкою")
            return
        if os.path.abspath(source) == os.path.abspath(dest):
            messagebox.showerror("Помилка", "Source і Destination не можуть бути однією папкою")
            return
        if os.path.isabs(logs_dir) and os.path.exists(logs_dir) and not os.path.isdir(logs_dir):
            messagebox.showerror("Помилка", "Папка логів вказує на файл")
            return
        logs_path = logs_dir if os.path.isabs(logs_dir) else os.path.join(app_dir(), logs_dir)
        if os.path.exists(logs_path) and not os.path.isdir(logs_path):
            messagebox.showerror("Помилка", "Папка логів вказує на файл")
            return
        if self.running:
            return
        try:
            workers = int(self.workers.get().strip())
        except (AttributeError, TypeError, ValueError):
            self.workers.set("4")
            messagebox.showerror("Помилка", "Кількість потоків має бути цілим числом")
            return
        if workers < 1:
            self.workers.set("4")
            messagebox.showerror("Помилка", "Кількість потоків має бути додатною")
            return
        try:
            os.makedirs(dest, exist_ok=True)
        except OSError as error:
            messagebox.showerror("Помилка destination", str(error))
            return
        self.running = True
        self.start_button.configure(state="disabled")
        self.status.configure(text="Обробка...")
        self.table.delete(*self.table.get_children())
        self.rows.clear()
        self.total_albums = 0
        self.finished_albums = 0
        self.overall.configure(maximum=1, value=0)
        self.overall_text.configure(text="Загальний прогрес: 0/0")
        self.progress = TkProgress()
        save_config(source, dest, workers, logs_dir)
        try:
            setup_logging(logs_dir)
        except OSError as error:
            self.running = False
            self.start_button.configure(state="normal")
            messagebox.showerror("Помилка логування", str(error))
            return
        threading.Thread(
            target=self.run,
            args=(source, dest, workers, self.progress),
            daemon=True,
        ).start()

    def run(self, source, dest, workers, progress):
        try:
            run_conversion(source, dest, workers, progress)
            progress.events.put(("finished", None))
        except Exception as error:
            progress.events.put(("error", str(error)))

    def process_events(self):
        try:
            while True:
                event = self.progress.events.get_nowait()
                kind = event[0]
                if kind == "add":
                    _, task_id, description, completed, total = event
                    item = self.table.insert(
                        "", "end", values=(description, "waiting", "", self.format_progress(completed, total))
                    )
                    self.rows[task_id] = item
                    self.total_albums += 1
                    self.overall.configure(maximum=self.total_albums)
                    self.update_overall()
                elif kind == "update":
                    _, task_id, description, completed, total = event
                    if task_id in self.rows:
                        item = self.rows[task_id]
                        status = description.split("]", 1)[0].lstrip("[")
                        thread = description.rsplit("(потік ", 1)[-1].rstrip(")") if "(потік " in description else ""
                        self.table.item(
                            item,
                            values=(description, status, thread, self.format_progress(completed, total)),
                        )
                elif kind == "remove":
                    item = self.rows.pop(event[1], None)
                    if item:
                        self.table.delete(item)
                    self.finished_albums += 1
                    self.update_overall()
                elif kind == "finished":
                    self.running = False
                    self.start_button.configure(state="normal")
                    self.status.configure(text="Готово")
                elif kind == "log":
                    self.status.configure(text="Помилка конвертації")
                elif kind == "error":
                    self.running = False
                    self.start_button.configure(state="normal")
                    self.status.configure(text="Помилка")
                    messagebox.showerror("Помилка", event[1])
        except queue.Empty:
            pass
        self.after(100, self.process_events)

    def update_overall(self):
        self.overall.configure(value=self.finished_albums)
        self.overall_text.configure(
            text=f"Загальний прогрес: {self.finished_albums}/{self.total_albums}"
        )

    @staticmethod
    def format_progress(completed, total):
        completed = completed or 0
        total = total or 0
        percent = round(completed / total * 100) if total else 0
        return f"{completed}/{total} ({percent}%)"


if __name__ == "__main__":
    ConverterApp().mainloop()