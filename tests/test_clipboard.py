import asyncio
import sys

import pytest
from textual.widgets import Input

from yank.config import Config


@pytest.fixture
def clipboard_tool(tmp_path, monkeypatch):
    folder = tmp_path/'bin'
    folder.mkdir()
    monkeypatch.setenv('PATH', str(folder))
    monkeypatch.setenv('WAYLAND_DISPLAY', 'test-wayland')
    monkeypatch.setenv('XDG_SESSION_TYPE', 'wayland')
    def install(code):
        tool = folder/'wl-paste'
        tool.write_text('#!'+sys.executable+'\n'+code+'\n')
        tool.chmod(0o755)
    return install


async def test_read_clipboard_via_real_subprocess(clipboard_tool):
    from yank.clipboard import read_clipboard
    clipboard_tool("print('https://example.com/one\\nhttps://example.org/two', end='')")
    assert await read_clipboard() == 'https://example.com/one\nhttps://example.org/two'


@pytest.mark.parametrize('code', ["import sys; print('no clipboard text', file=sys.stderr); sys.exit(1)", 'import time; time.sleep(10)'])
async def test_clipboard_errors_and_timeout_are_reported(clipboard_tool, code):
    from yank.clipboard import ClipboardError, read_clipboard
    clipboard_tool(code)
    with pytest.raises(ClipboardError):
        await read_clipboard(timeout=.05)


async def test_mouse_paste_submits_in_one_click(tmp_path, clipboard_tool):
    from yank.ui import YankApp
    clipboard_tool("print('https://example.com/one\\nhttps://example.org/two', end='')")
    app = YankApp(Config(state_dir=tmp_path/'state', path=tmp_path/'config.toml',
                         download_dir=tmp_path/'downloads', ytdlp='/usr/bin/false'))
    async with app.run_test(size=(120,32)) as pilot:
        await pilot.click('#paste')
        field = app.query_one(Input)
        async with asyncio.timeout(2):
            while len(app.engine.jobs) < 2:
                await pilot.pause(.05)
        assert {job.url for job in app.engine.jobs.values()} == {'https://example.com/one', 'https://example.org/two'}
        assert app.focused is field
        assert field.value == ''
        for job in app.engine.jobs.values():
            app.engine.control(job.id, 'pause')
