import json
import os
import subprocess
import sys


def test_cli_persistent_queue_and_control(tmp_path):
    env = {**os.environ, 'XDG_CONFIG_HOME':str(tmp_path/'config'), 'XDG_STATE_HOME':str(tmp_path/'state')}
    def run(*args):
        return subprocess.run([sys.executable, '-m', 'yank', *args], env=env, capture_output=True, text=True)
    added = run('add', 'https://example.com/video')
    assert added.returncode == 0, added.stderr
    job_id = json.loads(added.stdout)['jobs'][0]['id']
    assert run('pause', job_id).returncode == 0
    state = run('state')
    assert json.loads(state.stdout)['jobs'][0]['status'] == 'paused'
    assert run('resume', job_id).returncode == 0
    assert run('cancel', job_id).returncode == 0
    assert run('remove', job_id).returncode == 0
    assert json.loads(run('state').stdout)['jobs'] == []
    assert run('add', 'not-a-url').returncode != 0
    assert run('remove', '../../bad').returncode != 0
    assert run('--help').returncode == 0
    run('add', 'https://example.com/unsafe-title')
    from yank.model import Store
    store = Store(tmp_path/'state/yank')
    job = next(iter(store.load().values()))
    job.title = 'Unsafe\x1b]52;c;ZmFrZQ==\x07 title'
    store.save(job)
    store.close()
    assert '\x1b' not in run('list').stdout
