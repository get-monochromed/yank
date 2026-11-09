"""Persistent async download manager and same-user Unix-socket control API."""
from __future__ import annotations

import asyncio
from contextlib import suppress
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import shutil
import time
from urllib.parse import urlsplit

from .backend import build_command, error_hint, parse_line, retry_delay
from .config import Config
from .model import ACTIVE, Job, Store
from .text import safe_text


class InstanceLock:
    def __init__(self, config: Config):
        config.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.file = (config.state_dir / 'manager.lock').open('a')
        try:
            fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.file.close()
            raise RuntimeError('Yank is already running. Use yank add/state/pause/etc. to control it.') from None

    def close(self):
        self.file.close()


def socket_address(config: Config) -> str:
    # Linux abstract sockets avoid Unix path length limits in long XDG paths.
    # IPC requests are additionally restricted by peer uid below.
    identity = hashlib.sha256(str(config.state_dir.resolve()).encode()).hexdigest()[:24]
    return f'\0yank-{os.getuid()}-{identity}'


async def request(config: Config, payload: dict) -> dict:
    reader, writer = await asyncio.wait_for(asyncio.open_unix_connection(socket_address(config), limit=2**22), 3)
    try:
        writer.write(json.dumps(payload).encode() + b'\n')
        await writer.drain()
        return json.loads(await asyncio.wait_for(reader.readline(), 5))
    finally:
        writer.close()
        await writer.wait_closed()


class Engine:
    def __init__(self, config: Config):
        self.config = config
        self.store = Store(config.state_dir)
        self.jobs = self.store.load()
        self.tasks: dict[str, asyncio.Task] = {}
        self.processes: dict[str, asyncio.subprocess.Process] = {}
        self._saved: dict[str, float] = {}
        self._scheduler = None
        self._server = None
        self._lock = None
        self._closing = False
        self.stopped = asyncio.Event()
        self.revision = 0

    def touch(self, job: Job, force=False):
        self.revision += 1
        now = time.monotonic()
        if force or now - self._saved.get(job.id, 0) > 1:
            self.store.save(job)
            self._saved[job.id] = now

    def add(self, url: str) -> Job:
        url = url.strip()
        parsed = urlsplit(url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or any(c.isspace() or ord(c) < 32 for c in url):
            raise ValueError('Enter a complete HTTP or HTTPS video URL')
        if len(url) > 16384:
            raise ValueError('URL is too long')
        for job in self.jobs.values():
            if job.url == url:
                return job
        job = Job(url=url, destination=str(self.config.destination_for(url)), title=url)
        self.jobs[job.id] = job
        self.touch(job, True)
        return job

    def control(self, job_id: str, action: str):
        if job_id not in self.jobs:
            raise ValueError(f'No queue entry: {job_id}')
        job = self.jobs[job_id]
        if action in ('pause', 'cancel', 'remove'):
            if job.status == 'done' and action != 'remove':
                return
            job.status = 'paused' if action == 'pause' else 'cancelled'
            job.speed = 0
            job.eta = None
            if task := self.tasks.get(job_id):
                if not task.cancelling():
                    task.cancel()
            if action == 'remove':
                self.store.delete(job_id)
                del self.jobs[job_id]
                self.revision += 1
                return
        elif action in ('resume', 'retry'):
            if job.status == 'done':
                return
            if job.status in ACTIVE:
                return
            job.status = 'queued'
            job.retry_at = 0
            job.last_error = ''
            job.hint = ''
        else:
            raise ValueError(f'Unknown action: {action}')
        self.touch(job, True)

    def clear(self) -> int:
        count = len(self.jobs)
        self.store.clear()
        for job in self.jobs.values():
            job.status = 'cancelled'
            job.speed = 0
            job.eta = None
            if task := self.tasks.get(job.id):
                if not task.cancelling():
                    task.cancel()
        self.jobs.clear()
        self._saved.clear()
        self.revision += 1
        return count

    def state(self) -> dict:
        return {'jobs': [job.to_dict() for job in self.jobs.values()],
                'download_dir': str(self.config.download_dir), 'config': str(self.config.path),
                'running': self._scheduler is not None and not self._closing}

    async def start(self, ipc=True):
        self._lock = InstanceLock(self.config)
        try:
            fresh = self.store.load()
            for job_id, job in fresh.items():
                if job_id in self.jobs:
                    self.jobs[job_id].__dict__.update(job.__dict__)
                    fresh[job_id] = self.jobs[job_id]
            self.jobs = fresh
            for job in self.jobs.values():
                if job.status in ACTIVE:
                    job.status = 'queued'
                    job.speed = 0
                    self.touch(job, True)
            if ipc:
                self._server = await asyncio.start_unix_server(self._handle_client, socket_address(self.config), limit=65536)
            self._scheduler = asyncio.create_task(self._schedule())
        except BaseException:
            self._lock.close()
            self._lock = None
            raise

    async def _handle_client(self, reader, writer):
        import socket
        import struct
        try:
            sock = writer.get_extra_info('socket')
            _, uid, _ = struct.unpack('3i', sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if uid != os.getuid():
                return
            payload = json.loads(await asyncio.wait_for(reader.readline(), 5))
            action = payload.get('action')
            if action == 'state':
                response = self.state()
            elif action == 'add':
                response = {'job': self.add(payload['url']).to_dict()}
            elif action == 'clear':
                response = {'removed': self.clear()}
            elif action == 'stop':
                self.stopped.set()
                response = {'ok': True}
            else:
                self.control(payload['id'], action)
                response = {'ok': True}
        except (ValueError, KeyError, TypeError, asyncio.TimeoutError) as error:
            response = {'error': str(error)}
        except (ConnectionError, OSError):
            return
        finally:
            if not writer.is_closing():
                if 'response' in locals():
                    writer.write(json.dumps(response).encode() + b'\n')
                    with suppress(ConnectionError):
                        await writer.drain()
                writer.close()
                with suppress(ConnectionError):
                    await writer.wait_closed()

    async def _schedule(self):
        while not self._closing:
            for job_id, task in list(self.tasks.items()):
                if task.done():
                    # Retrieve exceptions so no tasks fail silently.
                    if not task.cancelled() and task.exception():
                        job = self.jobs.get(job_id)
                        if job and job.status in ACTIVE:
                            self._failed(job, str(task.exception()))
                    del self.tasks[job_id]
            available = self.config.concurrent_downloads - len(self.tasks)
            now = time.time()
            for job in list(self.jobs.values()):
                if available <= 0:
                    break
                if job.id not in self.tasks and (job.status == 'queued' or (job.status == 'retrying' and job.retry_at <= now)):
                    job.status = 'extracting'
                    self.touch(job, True)
                    self.tasks[job.id] = asyncio.create_task(self._run(job))
                    available -= 1
            await asyncio.sleep(.1)

    def _failed(self, job: Job, message: str):
        job.status = 'retrying'
        job.last_error = message[-2000:]
        job.hint = error_hint(message)
        job.speed = 0
        job.eta = None
        job.retry_at = time.time() + retry_delay(self.config, job.attempt)
        # Use the other transport after each failure. HLS/DASH stay native.
        job.backend = 'native' if job.backend == 'aria2c' else 'aria2c'
        self.touch(job, True)

    async def _terminate(self, proc):
        # A reaped group leader says nothing about its aria2/ffmpeg children.
        # Keep escalating until the group disappears, not just until wait()
        # returns for the leader. A killed orphan may briefly remain a zombie.
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(proc.pid, sig)
            except ProcessLookupError:
                await proc.wait()
                return
            deadline = loop.time() + 2
            while loop.time() < deadline:
                try:
                    os.killpg(proc.pid, 0)
                except ProcessLookupError:
                    await proc.wait()
                    return
                await asyncio.sleep(.025)
        await proc.wait()

    def _validate_archive(self, job: Job):
        # An archive ID is not proof that its output still exists. Keep only
        # entries backed by a persisted, verified final path. Older jobs without
        # this manifest safely re-extract; --no-overwrites preserves real files.
        verified = {key: path for key, path in job.outputs.items() if Path(path).is_file()}
        archive = self.config.state_dir / 'archives' / f'{job.id}.txt'
        if archive.exists():
            entries = archive.read_text(encoding='utf-8').splitlines()
            kept = [entry for entry in entries if entry in verified]
            if kept != entries:
                backup = archive.with_suffix(f'.unverified-{time.time_ns()}.txt')
                shutil.copy2(archive, backup)
                temporary = archive.with_suffix('.next')
                temporary.write_text(''.join(entry + '\n' for entry in kept), encoding='utf-8')
                temporary.chmod(0o600)
                temporary.replace(archive)
        job.outputs = verified
        if job.filename and not Path(job.filename).is_file():
            job.filename = ''
        self.touch(job, True)

    async def _run(self, job: Job):
        proc = None
        log = None
        error = ''
        try:
            job.attempt += 1
            job.speed = 0
            job.eta = None
            # Never retain misleading 99% progress while re-extracting.
            job.components = {}
            job.downloaded = job.total = 0
            self.touch(job, True)
            destination = Path(job.destination)
            destination.mkdir(parents=True, exist_ok=True)
            (destination / '.yank' / job.id / job.backend).mkdir(parents=True, exist_ok=True, mode=0o700)
            (self.config.state_dir / 'archives').mkdir(exist_ok=True, mode=0o700)
            self._validate_archive(job)
            logs = self.config.state_dir / 'logs'
            logs.mkdir(exist_ok=True, mode=0o700)
            logfile = logs / f'{job.id}.log'
            if logfile.exists() and logfile.stat().st_size > 1024 * 1024:
                logfile.replace(logfile.with_suffix('.previous.log'))
            log = logfile.open('a', encoding='utf-8')
            logfile.chmod(0o600)
            log.write(f'\n--- attempt {job.attempt} / {job.backend} ---\n')
            log.flush()
            proc = await asyncio.create_subprocess_exec(*build_command(self.config, job),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                stdin=asyncio.subprocess.DEVNULL, start_new_session=True)
            self.processes[job.id] = proc
            buffer = b''
            while True:
                timeout = self.config.stall_timeout or None
                chunk = await asyncio.wait_for(proc.stdout.read(8192), timeout)
                if not chunk:
                    break
                buffer += chunk
                lines = re.split(b'[\r\n]', buffer)
                buffer = lines.pop()[-65536:]
                for raw in lines:
                    line = raw.decode('utf-8', errors='replace').strip()
                    if not line:
                        continue
                    # Strip terminal escapes from all externally sourced output.
                    line = safe_text(line)
                    if log.tell() < 2 * 1024 * 1024:
                        log.write(line + '\n')
                        log.flush()
                    if parse_line(job, line):
                        self.touch(job)
                    elif 'ERROR' in line or 'error:' in line.lower():
                        error = line
            if buffer:
                line = buffer.decode('utf-8', errors='replace').strip()
                parse_line(job, line)
                if 'error' in line.lower():
                    error = line
            code = await proc.wait()
            if job.id not in self.jobs or job.status not in ACTIVE:
                return
            if code:
                self._failed(job, error or f'yt-dlp exited with code {code}; see log')
            else:
                if not job.outputs or not all(Path(path).is_file() for path in job.outputs.values()):
                    self._failed(job, 'yt-dlp exited without verified final output files; see log')
                    return
                if not job.filename or not Path(job.filename).is_file():
                    job.filename = list(job.outputs.values())[-1]
                job.status = 'done'
                job.speed = 0
                job.eta = 0
                job.last_error = job.hint = ''
                self.touch(job, True)
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            if job.id in self.jobs and job.status in ACTIVE:
                self._failed(job, f'No output for {self.config.stall_timeout:g}s; restarting stalled download')
        except Exception as exc:
            if job.id in self.jobs and job.status in ACTIVE:
                self._failed(job, f'{type(exc).__name__}: {exc}')
        finally:
            async def cleanup():
                try:
                    if proc is not None:
                        await self._terminate(proc)
                finally:
                    self.processes.pop(job.id, None)
                    if log:
                        log.close()

            # Pause/cancel may arrive AFTER a timeout starts its finally block.
            # Shielding alone is not enough: keep awaiting the cleanup task so
            # the scheduler cannot reuse this slot while the child is alive.
            cleanup_task = asyncio.create_task(cleanup())
            interrupted = False
            while True:
                try:
                    await asyncio.shield(cleanup_task)
                    break
                except asyncio.CancelledError:
                    if cleanup_task.cancelled():
                        raise
                    interrupted = True
            if interrupted:
                raise asyncio.CancelledError

    async def close(self):
        if getattr(self, '_closed', False):
            return
        self._closed = True
        if self._lock is None:
            self.store.close()
            return
        self._closing = True
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        if self._scheduler:
            self._scheduler.cancel()
            with suppress(asyncio.CancelledError):
                await self._scheduler
        for task in self.tasks.values():
            if not task.cancelling():
                task.cancel()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)
        for job in self.jobs.values():
            if job.status in ACTIVE:
                job.status = 'queued'
                job.speed = 0
            self.store.save(job)
        self.store.close()
        if self._lock:
            self._lock.close()
        self.stopped.set()
