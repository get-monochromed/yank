"""Regression tests for actual process cleanup, not mocked process APIs."""
import asyncio
from contextlib import suppress
import os
from pathlib import Path
import signal
import sys

from yank.config import Config
from yank.engine import Engine


async def test_pause_during_watchdog_cleanup_does_not_orphan_process(tmp_path):
    worker = tmp_path / 'stubborn-worker'
    worker.write_text('#!' + sys.executable + '\n' + '''
import signal, time
signal.signal(signal.SIGINT, signal.SIG_IGN)
print('ready', flush=True)
time.sleep(120)
''')
    worker.chmod(0o755)
    config = Config(state_dir=tmp_path/'state', download_dir=tmp_path/'downloads',
                    ytdlp=str(worker), stall_timeout=.1, retry_initial=10, retry_max=10)
    engine = Engine(config)
    cleaning = asyncio.Event()
    original = engine._terminate
    async def observed_termination(proc):
        cleaning.set()
        await original(proc)
    engine._terminate = observed_termination
    await engine.start(ipc=False)
    proc = None
    try:
        job = engine.add('https://example.com/stalled')
        await asyncio.wait_for(cleaning.wait(), 5)
        assert job.status == 'retrying'
        proc = engine.processes[job.id]
        task = engine.tasks[job.id]
        engine.control(job.id, 'pause')
        await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 8)
        assert proc.returncode is not None, 'downloader survived cancelled cleanup'
        assert job.id not in engine.processes
        assert job.status == 'paused'
    finally:
        if proc is not None:
            with suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            await proc.wait()
        await engine.close()


def is_running(pid):
    try:
        return Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].strip()[0] != 'Z'
    except FileNotFoundError:
        return False


async def test_termination_escalates_after_group_leader_exits(tmp_path):
    child_code = '''
import pathlib, signal, sys, time
signal.signal(signal.SIGINT, signal.SIG_IGN)
signal.signal(signal.SIGTERM, signal.SIG_IGN)
pathlib.Path(sys.argv[1]).touch()
time.sleep(120)
'''
    parent_code = '''
import pathlib, subprocess, sys, time
child = subprocess.Popen([sys.executable, '-c', sys.argv[1], sys.argv[2]], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
while not pathlib.Path(sys.argv[2]).exists():
    time.sleep(.01)
print(child.pid, flush=True)
time.sleep(120)
'''
    engine = Engine(Config(state_dir=tmp_path/'state'))
    proc = await asyncio.create_subprocess_exec(sys.executable, '-c', parent_code, child_code,
        str(tmp_path/'child-ready'), stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL, start_new_session=True)
    child_pid = None
    try:
        child_pid = int(await asyncio.wait_for(proc.stdout.readline(), 5))
        assert is_running(child_pid)
        await asyncio.wait_for(engine._terminate(proc), 8)
        assert not is_running(child_pid), 'descendant survived after group leader exited'
    finally:
        with suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
        await proc.wait()
        engine.store.close()
