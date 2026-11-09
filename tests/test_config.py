from pathlib import Path


def test_config_bootstraps_under_xdg_and_validates(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    from yank.config import Config
    config = Config.load()
    assert config.path == tmp_path / 'config/yank/config.toml'
    assert config.path.exists()
    assert config.download_dir == Path.home() / 'Downloads/yank'
    config.path.write_text('download_dir = "~/Videos/yank"\nconcurrent_downloads = 2\n')
    config = Config.load()
    assert config.download_dir == Path.home() / 'Videos/yank'
    assert config.concurrent_downloads == 2
    config.path.write_text('concurrent_downloads = 0\n')
    import pytest
    with pytest.raises(ValueError, match='concurrent_downloads'):
        Config.load()
