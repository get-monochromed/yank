"""Real yt-dlp/aria2/ffmpeg integration; no fake downloader output."""
import asyncio
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import subprocess
import threading

import pytest

from yank.config import Config
from yank.engine import Engine


@pytest.fixture
def media_server(tmp_path):
    if not all(shutil.which(tool) for tool in ('yt-dlp','aria2c','ffmpeg','ffprobe')):
        pytest.skip('integration requires yt-dlp, aria2c, ffmpeg and ffprobe')
    media = tmp_path / 'media'
    media.mkdir()
    subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i',
                    'testsrc2=size=320x180:rate=24', '-t', '3', '-c:v', 'mpeg4', '-q:v', '5',
                    str(media/'clip.mp4')], check=True, capture_output=True)
    subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-i', str(media/'clip.mp4'),
                    '-c', 'copy', '-hls_time', '1', '-hls_list_size', '0', str(media/'stream.m3u8')], check=True, capture_output=True)
    (media/'index.html').write_text('<html><head><title>Local integration clip</title></head><body><video src="clip.mp4"></video></body></html>')
    class Handler(SimpleHTTPRequestHandler):
        rejected = False
        def do_GET(self):
            if self.path == '/flaky.mp4' and not type(self).rejected:
                type(self).rejected = True
                self.send_error(403, 'Controlled rejection for recovery test')
                return
            if self.path == '/flaky.mp4':
                self.path = '/clip.mp4'
            super().do_GET()
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(Handler, directory=str(media)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_port}', media
    server.shutdown()
    server.server_close()
    thread.join()


@pytest.mark.parametrize('source', ['index.html', 'flaky.mp4', 'stream.m3u8'])
async def test_real_downloads_and_automatic_403_recovery(tmp_path, media_server, source):
    base, media = media_server
    config = Config(state_dir=tmp_path/'state', download_dir=tmp_path/'downloads',
                    inherit_ytdlp_config=False, retry_initial=.05, retry_max=.1, stall_timeout=15,
                    extra_args=['--limit-rate', '64K'] if source == 'index.html' else [])
    engine = Engine(config)
    await engine.start(ipc=False)
    try:
        job = engine.add(base + '/' + source)
        saw_progress = False
        async with asyncio.timeout(35):
            while job.status != 'done':
                saw_progress |= 0 < job.progress < 1 and job.speed > 0
                if job.attempt > 3:
                    log = (config.state_dir/'logs'/f'{job.id}.log').read_text()
                    pytest.fail(f'{job.last_error}\n{log}')
                await asyncio.sleep(.05)
        assert job.progress == 1
        output = Path(job.filename)
        assert output.exists()
        probe = subprocess.run(['ffprobe','-v','error','-show_entries','format=duration','-of','json',str(output)], text=True, capture_output=True, check=True)
        assert float(json.loads(probe.stdout)['format']['duration']) >= 2.9
        log = (config.state_dir/'logs'/f'{job.id}.log').read_text()
        assert 'YANK_META:' in log
        assert 'YANK_DONE:' in log
        if source == 'flaky.mp4':
            assert job.attempt == 2
            assert '403' in log
            assert job.backend == 'native'
        elif source == 'index.html':
            assert saw_progress
            assert job.backend == 'aria2c'
            assert job.total == (media/'clip.mp4').stat().st_size
            assert job.downloaded == job.total
            assert output.read_bytes() == (media/'clip.mp4').read_bytes()
            assert '[#' in log  # Real aria2 console readout, not just a final hook.
    finally:
        await engine.close()


async def test_real_aria_pause_resume_from_tui(tmp_path, media_server):
    import os
    from yank.ui import YankApp
    base, media = media_server
    config = Config(state_dir=tmp_path/'state', download_dir=tmp_path/'downloads',
                    inherit_ytdlp_config=False, extra_args=['--limit-rate', '32K'], stall_timeout=20)
    engine = Engine(config)
    job = engine.add(base+'/index.html')
    paused = engine.add(base+'/clip.mp4')
    engine.control(paused.id, 'pause')
    app = YankApp(config, engine=engine)
    async with app.run_test(size=(120, 42)) as pilot:
        async with asyncio.timeout(25):
            while not (job.downloaded > 0 and job.progress < .9):
                await pilot.pause(.1)
        await pilot.click(f'#job-{job.id}')
        screenshot_dir = os.environ.get('YANK_SCREENSHOT_DIR')
        if screenshot_dir:
            app.save_screenshot('yank-live.svg', path=screenshot_dir)
        await pilot.press('p')
        assert job.status == 'paused'
        async with asyncio.timeout(10):
            while job.id in engine.processes:
                await pilot.pause(.1)
        processes = subprocess.run(['ps','-C','aria2c','-o','pid=,args='], capture_output=True, text=True).stdout
        assert job.id not in processes, f'aria2c still running after pause: {processes}'
        before = job.downloaded
        await pilot.pause(.3)
        assert job.downloaded == before
        await pilot.press('p')
        async with asyncio.timeout(30):
            while job.status != 'done':
                await pilot.pause(.1)
        assert Path(job.filename).read_bytes() == (media/'clip.mp4').read_bytes()
        assert paused.status == 'paused'
        await pilot.press('ctrl+q')
    assert engine._closed


def test_real_headless_cli_and_live_socket(tmp_path, media_server):
    import os
    import sys
    import time
    base, media = media_server
    env = {**os.environ, 'XDG_CONFIG_HOME':str(tmp_path/'config'), 'XDG_STATE_HOME':str(tmp_path/'state')}
    config_file = tmp_path/'config/yank/config.toml'
    config_file.parent.mkdir(parents=True)
    config_file.write_text(f'download_dir = "{tmp_path / "downloads"}"\ninherit_ytdlp_config = false\n')
    def cli(*args):
        return subprocess.run([sys.executable, '-m', 'yank', *args], env=env, text=True, capture_output=True, timeout=10)
    server = subprocess.Popen([sys.executable, '-m', 'yank', 'serve'], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic()+5
        while not json.loads(cli('state').stdout)['running']:
            assert server.poll() is None, server.stderr.read()
            assert time.monotonic() < deadline
            time.sleep(.1)
        added = cli('add', base+'/clip.mp4')
        assert added.returncode == 0, added.stderr
        job_id = json.loads(added.stdout)['jobs'][0]['id']
        deadline = time.monotonic()+25
        while True:
            state = json.loads(cli('state').stdout)
            job = state['jobs'][0]
            if job['status'] == 'done':
                break
            assert time.monotonic() < deadline, job
            time.sleep(.1)
        assert Path(job['filename']).read_bytes() == (media/'clip.mp4').read_bytes()
        assert cli('stop').returncode == 0
        server.wait(timeout=8)
        assert server.returncode == 0, server.stderr.read()
        assert not json.loads(cli('state').stdout)['running']
        assert cli('remove', job_id).returncode == 0
        assert Path(job['filename']).exists()
    finally:
        if server.poll() is None:
            server.terminate()
            server.wait(timeout=10)


async def test_missing_archived_output_is_downloaded_again(tmp_path, media_server):
    base, media = media_server
    config = Config(state_dir=tmp_path/'state', download_dir=tmp_path/'downloads',
                    inherit_ytdlp_config=False, retry_initial=.05, retry_max=.1)
    engine = Engine(config)
    job = engine.add(base+'/clip.mp4')
    archives = config.state_dir/'archives'
    archives.mkdir()
    archive = archives/f'{job.id}.txt'
    archive.write_text('generic clip\n')
    await engine.start(ipc=False)
    try:
        async with asyncio.timeout(25):
            while job.status != 'done':
                assert job.attempt <= 3, job.last_error
                await asyncio.sleep(.05)
        assert job.filename, 'archive-only completion must not count as a downloaded file'
        assert Path(job.filename).read_bytes() == (media/'clip.mp4').read_bytes()
    finally:
        await engine.close()


@pytest.mark.parametrize('remove_output', [False, True])
async def test_archived_output_manifest_is_checked_on_resume(tmp_path, media_server, remove_output):
    base, media = media_server
    config = Config(state_dir=tmp_path/'state', download_dir=tmp_path/'downloads',
                    inherit_ytdlp_config=False, retry_initial=.05, retry_max=.1)
    engine = Engine(config)
    await engine.start(ipc=False)
    job = engine.add(base+'/clip.mp4')
    try:
        async with asyncio.timeout(25):
            while job.status != 'done':
                await asyncio.sleep(.05)
        output = Path(job.filename)
        assert job.outputs == {'generic clip': str(output)}
        job.status = 'queued'  # Simulate a pending job across restart.
        engine.touch(job, True)
    finally:
        await engine.close()
    if remove_output:
        output.unlink()
    resumed = Engine(config)
    await resumed.start(ipc=False)
    try:
        job = resumed.jobs[job.id]
        async with asyncio.timeout(25):
            while job.status != 'done':
                assert job.attempt <= 3, job.last_error
                await asyncio.sleep(.05)
        assert Path(job.filename).read_bytes() == (media/'clip.mp4').read_bytes()
        log = (config.state_dir/'logs'/f'{job.id}.log').read_text().split('--- attempt 2')[-1]
        if remove_output:
            assert 'YANK_DONE:' in log
            assert list((config.state_dir/'archives').glob('*.unverified-*.txt'))
        else:
            assert 'has already been recorded in the archive' in log
            assert 'YANK_DONE:' not in log
    finally:
        await resumed.close()


async def test_real_download_uses_domain_destination(tmp_path, media_server):
    base, media = media_server
    routed = tmp_path/'routed-videos'
    fallback = tmp_path/'default-videos'
    path = tmp_path/'config.toml'
    path.write_text(f'download_dir = "{fallback}"\ninherit_ytdlp_config = false\n[domain_dirs]\n"127.0.0.1" = "{routed}"\n')
    config = Config.load(path)
    config.state_dir = tmp_path/'state'
    engine = Engine(config)
    await engine.start(ipc=False)
    try:
        job = engine.add(base+'/clip.mp4')
        assert job.destination == str(routed)
        async with asyncio.timeout(25):
            while job.status != 'done':
                assert job.attempt <= 3, job.last_error
                await asyncio.sleep(.05)
        output = Path(job.filename)
        assert output.parent == routed
        assert output.read_bytes() == (media/'clip.mp4').read_bytes()
        assert not fallback.exists()
    finally:
        await engine.close()
