import asyncio
import json
from pathlib import Path

import pytest

from yank.config import Config


def cfg(tmp_path):
    return Config(state_dir=tmp_path / 'state', download_dir=tmp_path / 'downloads',
                  retry_initial=.05, retry_max=.1, inherit_ytdlp_config=False)


def test_queue_persists_deduplicates_and_removes_without_deleting_files(tmp_path):
    from yank.engine import Engine
    config = cfg(tmp_path)
    engine = Engine(config)
    job = engine.add('https://example.com/video?a=1&b=2')
    assert engine.add(job.url).id == job.id
    assert job.status == 'queued'
    assert job.destination == str(config.download_dir)
    with pytest.raises(ValueError, match='HTTP'):
        engine.add('--exec rm')
    engine.store.close()
    restored = Engine(config)
    assert restored.jobs[job.id].url == job.url
    config.download_dir.mkdir()
    file = config.download_dir / 'finished.mp4'
    file.write_bytes(b'keep')
    restored.control(job.id, 'remove')
    assert not restored.jobs
    assert file.read_bytes() == b'keep'
    restored.store.close()


async def wait_status(engine, job_id, statuses, timeout=5):
    async with asyncio.timeout(timeout):
        while engine.jobs[job_id].status not in statuses:
            await asyncio.sleep(.02)
    return engine.jobs[job_id]


@pytest.fixture
def worker(tmp_path):
    import sys
    path = tmp_path / 'worker'
    path.write_text('#!' + sys.executable + '\n' + '''
import json, pathlib, sys, time
args = sys.argv
url = args[-1]
archive = pathlib.Path(args[args.index('--download-archive') + 1])
marker = archive.with_suffix('.attempt')
attempt = int(marker.read_text()) + 1 if marker.exists() else 1
marker.write_text(str(attempt))
if 'retry' in url and attempt == 1:
    print('ERROR: HTTP Error 403: Page rejected', flush=True)
    sys.exit(1)
print('YANK_META:' + json.dumps({'id':'fixture', 'title':'Controlled worker'}), flush=True)
for n in range(1, 11):
    print('YANK_PROGRESS:' + json.dumps({'downloaded':n*100,'total':1000,'status':'downloading','format_id':'default','speed':100}), flush=True)
    time.sleep(.05 if 'slow' not in url else .3)
out = pathlib.Path(args[args.index('--paths') + 1]) / 'fixture.mp4'
out.write_bytes(b'fixture')
print('YANK_DONE:' + json.dumps(str(out)), flush=True)
''')
    path.chmod(0o755)
    return str(path)


async def test_process_retry_pause_resume_cancel_and_restart(tmp_path, worker):
    from yank.engine import Engine
    config = cfg(tmp_path)
    config.ytdlp = worker
    config.concurrent_downloads = 1
    engine = Engine(config)
    await engine.start(ipc=False)
    try:
        retry = engine.add('https://example.com/retry')
        done = await wait_status(engine, retry.id, {'done'})
        assert done.attempt == 2
        assert done.backend == 'native'
        assert Path(done.filename).exists()
        slow = engine.add('https://example.com/slow')
        await wait_status(engine, slow.id, {'downloading'})
        engine.control(slow.id, 'pause')
        await asyncio.sleep(.15)
        assert slow.status == 'paused'
        assert slow.id not in engine.processes
        engine.control(slow.id, 'resume')
        await wait_status(engine, slow.id, {'downloading'})
        engine.control(slow.id, 'cancel')
        await asyncio.sleep(.15)
        assert slow.status == 'cancelled'
        assert slow.id not in engine.processes
        engine.control(slow.id, 'retry')
        await wait_status(engine, slow.id, {'downloading'})
    finally:
        await engine.close()
    restored = Engine(config)
    await restored.start(ipc=False)
    try:
        assert restored.jobs[slow.id].status in {'queued', 'extracting', 'downloading'}
        assert restored.jobs[retry.id].status == 'done'
    finally:
        await restored.close()


async def test_single_instance_and_ipc(tmp_path, worker):
    from yank.engine import Engine, request
    config = cfg(tmp_path)
    config.ytdlp = worker
    engine = Engine(config)
    await engine.start()
    other = Engine(config)
    try:
        with pytest.raises(RuntimeError, match='already running'):
            await other.start()
        await other.close()
        await other.close()  # Failed startup/duplicate cleanup must be harmless.
        response = await request(config, {'action':'add', 'url':'https://example.com/slow'})
        job_id = response['job']['id']
        await request(config, {'action':'pause', 'id':job_id})
        state = await request(config, {'action':'state'})
        assert state['jobs'][0]['status'] == 'paused'
        assert 'error' in await request(config, {'action':'remove', 'id':'missing'})
        await request(config, {'action':'remove', 'id':job_id})
        assert (await request(config, {'action':'state'}))['jobs'] == []
    finally:
        await engine.close()


async def test_start_sees_entries_added_since_construction(tmp_path):
    from yank.engine import Engine
    config = cfg(tmp_path)
    engine = Engine(config)
    other = Engine(config)
    job = other.add('https://example.com/new')
    other.control(job.id, 'pause')
    other.store.close()
    await engine.start(ipc=False)
    try:
        assert job.id in engine.jobs
        assert engine.jobs[job.id].status == 'paused'
    finally:
        await engine.close()
