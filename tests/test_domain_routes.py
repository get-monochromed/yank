from pathlib import Path

import pytest

from yank.config import Config
from yank.engine import Engine


def test_domain_routes_expand_paths_match_subdomains_and_keep_destinations(tmp_path, monkeypatch):
    monkeypatch.setenv('MEDIA_ROOT', str(tmp_path/'media'))
    path = tmp_path/'config.toml'
    path.write_text('''download_dir = "~/Downloads/default"
[domain_dirs]
"youtube.com" = "$MEDIA_ROOT/youtube"
"youtu.be" = "$MEDIA_ROOT/youtube"
"music.youtube.com" = "~/Music/youtube"
"website.com" = "$MEDIA_ROOT/other"
''')
    config = Config.load(path)
    config.state_dir = tmp_path/'state'
    engine = Engine(config)
    try:
        cases = {
            'https://youtube.com/watch?v=one':tmp_path/'media/youtube',
            'https://WWW.YouTube.com/watch?v=two':tmp_path/'media/youtube',
            'https://m.youtube.com/watch?v=three':tmp_path/'media/youtube',
            'https://youtu.be/short':tmp_path/'media/youtube',
            'https://music.youtube.com/watch?v=four':Path.home()/'Music/youtube',
            'https://www.website.com/movie':tmp_path/'media/other',
            'https://notyoutube.com/movie':Path.home()/'Downloads/default',
            'https://youtube.com.evil.example/movie':Path.home()/'Downloads/default',
            'https://example.org/movie?redirect=https://youtube.com':Path.home()/'Downloads/default',
        }
        for url, expected in cases.items():
            assert engine.add(url).destination == str(expected)
        job = engine.add('https://youtube.com/watch?v=one')
        config.domain_dirs['youtube.com'] = tmp_path/'changed'
        assert engine.add(job.url).destination == str(tmp_path/'media/youtube')
        assert engine.add('https://youtube.com/watch?v=new').destination == str(tmp_path/'changed')
    finally:
        engine.store.close()
    restored = Engine(config)
    try:
        assert restored.jobs[job.id].destination == str(tmp_path/'media/youtube')
    finally:
        restored.store.close()


@pytest.mark.parametrize('text', [
    'domain_dirs = []',
    '[domain_dirs]\n"https://youtube.com" = "~/Videos"',
    '[domain_dirs]\n"*.youtube.com" = "~/Videos"',
    '[domain_dirs]\n"youtube.com/path" = "~/Videos"',
    '[domain_dirs]\n"youtube.com:443" = "~/Videos"',
    '[domain_dirs]\n"youtube.com" = 42',
    '[domain_dirs]\n"youtube.com" = ""',
    '[domain_dirs]\nyoutube.com = "~/Videos"',
])
def test_invalid_domain_rules_have_a_clear_config_error(tmp_path, text):
    path = tmp_path/'config.toml'
    path.write_text(text+'\n')
    with pytest.raises(ValueError, match='domain_dirs'):
        Config.load(path)
