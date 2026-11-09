"""Queue records and transactional persistence."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import sqlite3
import time
import uuid
from pathlib import Path

ACTIVE = {'extracting', 'downloading', 'processing'}
PENDING = ACTIVE | {'queued', 'retrying'}


@dataclass
class Job:
    url: str
    destination: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    title: str = ''
    status: str = 'queued'
    downloaded: int = 0
    total: int = 0
    speed: float = 0
    eta: float | None = None
    attempt: int = 0
    backend: str = 'aria2c'
    retry_at: float = 0
    last_error: str = ''
    hint: str = ''
    filename: str = ''
    outputs: dict[str, str] = field(default_factory=dict)
    format_id: str = ''
    video_id: str = ''
    components: dict = field(default_factory=dict)
    expected_formats: list = field(default_factory=list)
    created: float = field(default_factory=time.time)

    @property
    def progress(self) -> float:
        if self.status == 'done':
            return 1.0
        return min(.995, max(0, self.downloaded / self.total)) if self.total else 0.0

    def to_dict(self) -> dict:
        return {**asdict(self), 'progress': self.progress}


class Store:
    def __init__(self, state_dir: Path):
        state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = state_dir / 'queue.sqlite3'
        self.db = sqlite3.connect(path)
        path.chmod(0o600)
        # SQLite's busy timeout does not reliably wait for the lock upgrade
        # used by journal_mode on a fresh database. Concurrent CLI readers and
        # engine startup must retry that short-lived initialization race.
        deadline = time.monotonic() + 5
        while True:
            try:
                self.db.execute('PRAGMA journal_mode=WAL')
                self.db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, data TEXT NOT NULL)')
                break
            except sqlite3.Error as error:
                code = (getattr(error, 'sqlite_errorcode', 0) or 0) & 0xFF
                if code not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED) or time.monotonic() >= deadline:
                    self.db.close()
                    raise
                time.sleep(.025)

    def load(self) -> dict[str, Job]:
        records = [Job(**json.loads(data)) for (data,) in self.db.execute('SELECT data FROM jobs')]
        return {job.id: job for job in sorted(records, key=lambda job: job.created)}

    def save(self, job: Job):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO jobs VALUES (?, ?)', (job.id, json.dumps(asdict(job))))

    def delete(self, job_id: str):
        with self.db:
            self.db.execute('DELETE FROM jobs WHERE id = ?', (job_id,))

    def clear(self):
        with self.db:
            self.db.execute('DELETE FROM jobs')

    def close(self):
        self.db.close()
