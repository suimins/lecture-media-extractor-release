from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
def downloads_folder() -> Path:
    """Use the Windows known folder, including a user-redirected Downloads folder."""
    import os
    if os.name == "nt":
        import ctypes
        import uuid
        folder_id = (ctypes.c_byte * 16).from_buffer_copy(uuid.UUID(
            "374DE290-123F-4565-9164-39C4925E467B").bytes_le)
        value = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(folder_id), 0, None, ctypes.byref(value)) == 0:
            try:
                return Path(value.value)
            finally:
                ctypes.windll.ole32.CoTaskMemFree(value)
    return Path.home() / "Downloads"


DEFAULT_OUTPUT = downloads_folder() / "lecture"


class JobStore:
    def __init__(self, path: Path | None = None):
        self.path = path or DATA / "jobs.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, status TEXT NOT NULL,
                output_dir TEXT NOT NULL, error TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
            columns = {row[1] for row in db.execute("PRAGMA table_info(jobs)")}
            for name, definition in (("progress", "INTEGER NOT NULL DEFAULT 0"), ("detail", "TEXT NOT NULL DEFAULT ''")):
                if name not in columns:
                    db.execute(f"ALTER TABLE jobs ADD COLUMN {name} {definition}")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def update(self, job_id: str, title: str, status: str, directory: Path, error: str = "", progress: int = 0, detail: str = ""):
        with self.connect() as db:
            db.execute("""INSERT INTO jobs(id,title,status,output_dir,error,progress,detail) VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET title=excluded.title,status=excluded.status,
                output_dir=excluded.output_dir,error=excluded.error,progress=excluded.progress,
                detail=excluded.detail,updated_at=CURRENT_TIMESTAMP""",
                       (job_id, title, status, str(directory), error, progress, detail))

    def recover(self):
        with self.connect() as db:
            db.execute("UPDATE jobs SET status='interrupted' WHERE status='running'")

    def recent(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM jobs ORDER BY updated_at DESC LIMIT 100")]
