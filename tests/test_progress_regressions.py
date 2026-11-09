import json
from yank.backend import parse_line
from yank.model import Job


def test_aria_audio_with_unknown_size_does_not_overwrite_completed_video(tmp_path):
    job = Job(url='https://example.com/video', destination=str(tmp_path))
    parse_line(job, 'YANK_META:' + json.dumps({'title':'Two tracks', 'id':'v1', 'formats':[
        {'format_id':'v','filesize':800}, {'format_id':'a'}]}))
    parse_line(job, 'YANK_PROGRESS:' + json.dumps({'format_id':'v','downloaded':800,'total':800,'status':'finished'}))
    assert job.format_id == 'a'
    assert job.status == 'downloading'
    parse_line(job, '[#ab12cd 100B/200B(50%) CN:1 DL:50B ETA:2s]')
    assert job.downloaded == 900
    assert job.total == 1000
    assert job.components['v']['downloaded'] == 800
    parse_line(job, 'YANK_PROGRESS:' + json.dumps({'format_id':'a','downloaded':200,'total':200,'status':'finished'}))
    assert job.downloaded == job.total == 1000
    assert job.status == 'processing'
