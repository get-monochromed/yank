import json
import shutil

from yank.config import Config
from yank.model import Job


def test_backend_command_and_progress_do_not_mix_sparse_partial_files(tmp_path):
    from yank.backend import build_command, parse_line, retry_delay, error_hint
    config = Config(state_dir=tmp_path / 'state', download_dir=tmp_path / 'out', inherit_ytdlp_config=False)
    job = Job(url='https://example.com/v?x=1&z=2', destination=str(config.download_dir))
    first = build_command(config, job)
    assert first[-2:] == ['--', job.url]
    assert '--ignore-config' in first
    assert 'http:aria2c' in first
    job.backend = 'native'
    second = build_command(config, job)
    assert 'temp:' + str(config.download_dir / '.yank' / job.id / 'aria2c') in first
    assert 'temp:' + str(config.download_dir / '.yank' / job.id / 'native') in second
    parse_line(job, 'YANK_META:' + json.dumps({'title':'A [video]', 'id':'v1', 'formats':[{'format_id':'v', 'filesize':800}, {'format_id':'a', 'filesize':200}]}))
    parse_line(job, 'YANK_PROGRESS:' + json.dumps({'downloaded':400,'total':800,'format_id':'v','status':'downloading','speed':20,'eta':20}))
    assert job.title == 'A [video]'
    assert job.progress == .4
    assert job.total == 1000
    parse_line(job, 'YANK_PROGRESS:' + json.dumps({'downloaded':800,'total':800,'format_id':'v','status':'finished'}))
    parse_line(job, 'YANK_PROGRESS:' + json.dumps({'downloaded':100,'total':200,'format_id':'a','status':'downloading'}))
    assert job.progress == .9
    parse_line(job, 'YANK_DONE:' + json.dumps(str(tmp_path / 'A [video].mkv')))
    assert job.filename.endswith('.mkv')
    assert retry_delay(config, 100000) == config.retry_max
    assert 'cookies' in error_hint('HTTP Error 403: sign in to confirm')
    assert 'DRM' in error_hint('This video is DRM protected')


def test_aria_progress_and_malformed_lines(tmp_path):
    from yank.backend import parse_line
    job = Job(url='https://example.com/v', destination=str(tmp_path))
    parse_line(job, 'YANK_META:{"title":"Clip","id":"v"}')
    parse_line(job, '[#a0b1c2 1048576/4194304(25%) CN:4 DL:524288 ETA:6s]')
    assert job.downloaded == 1048576
    assert job.total == 4194304
    assert job.speed == 524288
    assert job.progress == .25
    parse_line(job, 'YANK_PROGRESS:{"downloaded":"NA","total":null,"speed":"NA"}')
    parse_line(job, 'YANK_META:broken')
    assert job.total == 4194304
