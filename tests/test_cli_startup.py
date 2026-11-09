import pytest

from yank.cli import operation
from yank.config import Config
from yank.engine import InstanceLock


async def test_read_only_state_does_not_compete_with_engine_startup(tmp_path):
    config = Config(state_dir=tmp_path/'state', download_dir=tmp_path/'downloads')
    lock = InstanceLock(config)  # Engine owns its lock, but has not bound IPC yet.
    try:
        state = await operation(config, {'action': 'state'})
        assert state['jobs'] == []
        assert state['running'] is False
        with pytest.raises(RuntimeError, match='already running'):
            InstanceLock(config)
    finally:
        lock.close()
