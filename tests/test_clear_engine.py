import asyncio
import json
import os
import subprocess
import sys

from yank.config import Config
from yank.engine import Engine, request


async def test_clear_over_ipc_stops_children_and_preserves_files(tmp_path):
    worker = tmp_path/'slow-worker'
    worker.write_text('#!'+sys.executable+'\nimport time\nprint("ready", flush=True)\ntime.sleep(60)\n')
    worker.chmod(0o755)
    config = Config(state_dir=tmp_path/'state', download_dir=tmp_path/'downloads', ytdlp=str(worker))
    config.download_dir.mkdir()
    finished = config.download_dir/'finished.mp4'
    partial = config.download_dir/'unfinished.mp4.part'
    finished.write_bytes(b'finished')
    partial.write_bytes(b'partial')
    engine = Engine(config)
    await engine.start()
    try:
        engine.add('https://example.com/one')
        engine.add('https://example.com/two')
        async with asyncio.timeout(5):
            while len(engine.processes) < 2:
                await asyncio.sleep(.02)
        processes = list(engine.processes.values())
        tasks = list(engine.tasks.values())
        response = await request(config, {'action':'clear'})
        assert response == {'removed':2}
        assert (await request(config, {'action':'state'}))['jobs'] == []
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 8)
        assert all(proc.returncode is not None for proc in processes)
        assert engine.processes == {}
        assert engine.store.load() == {}
        assert finished.read_bytes() == b'finished'
        assert partial.read_bytes() == b'partial'
        assert await request(config, {'action':'clear'}) == {'removed':0}
    finally:
        await engine.close()


def test_cli_clear_works_offline_and_preserves_files(tmp_path):
    env = {**os.environ, 'XDG_CONFIG_HOME':str(tmp_path/'config'), 'XDG_STATE_HOME':str(tmp_path/'state')}
    def run(*args):
        return subprocess.run([sys.executable,'-m','yank',*args], env=env, capture_output=True, text=True, timeout=10)
    assert run('add','https://example.com/one','https://example.com/two').returncode == 0
    response = run('clear')
    assert response.returncode == 0, response.stderr
    assert json.loads(response.stdout) == {'removed':2}
    assert json.loads(run('state').stdout)['jobs'] == []
    assert json.loads(run('clear').stdout) == {'removed':0}
