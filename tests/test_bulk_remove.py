import asyncio
import pytest

from textual import events
from textual.widgets import ListView

from yank.config import Config
from yank.engine import Engine
from yank.ui import DownloadRow, YankApp


def paused_app(tmp_path, count=60):
    config = Config(state_dir=tmp_path/'state', path=tmp_path/'config.toml',
                    download_dir=tmp_path/'downloads', ytdlp='/usr/bin/false')
    engine = Engine(config)
    for index in range(count):
        job = engine.add(f'https://example.com/video/{index}')
        engine.control(job.id, 'pause')
    return YankApp(config, engine=engine)


@pytest.mark.parametrize('index', [0, 30, 59])
async def test_remove_burst_uses_live_selection_not_deleted_rows(tmp_path, index):
    app = paused_app(tmp_path)
    async with app.run_test(size=(120,32)) as pilot:
        queue = app.query_one(ListView)
        queue.focus()
        queue.index = index
        for _ in range(80):
            app.action_remove()  # No render tick between key-repeat actions.
        await pilot.pause(.3)
        assert app.engine.jobs == {}
        assert not app.query(DownloadRow)


async def test_repeated_delete_events_while_list_refreshes(tmp_path):
    app = paused_app(tmp_path, 120)
    async with app.run_test(size=(120,32)) as pilot:
        queue = app.query_one(ListView)
        queue.focus()
        for batch in range(15):
            for _ in range(12):
                app._driver.send_message(events.Key('d', 'd'))
            await asyncio.sleep(.03)
        async with asyncio.timeout(10):
            while app.engine.jobs:
                if app._exception:
                    raise app._exception
                await asyncio.sleep(.05)
        await pilot.pause(.3)
        assert not app.query(DownloadRow)


async def test_shift_d_clears_queue_without_a_footer_item_or_file_deletion(tmp_path):
    from textual.widgets._footer import FooterKey
    app = paused_app(tmp_path, 120)
    downloads = tmp_path/'downloads'
    downloads.mkdir()
    output = downloads/'keep.mp4'
    output.write_bytes(b'keep this file')
    jobs = list(app.engine.jobs.values())
    jobs[0].status = 'done'
    jobs[0].filename = str(output)
    jobs[1].status = 'cancelled'
    app.engine.touch(jobs[0], True)
    app.engine.touch(jobs[1], True)
    async with app.run_test(size=(120,32)) as pilot:
        app.action_queue()
        await pilot.press('D')
        await pilot.pause(.3)
        assert not app.engine.jobs
        assert not app.query(DownloadRow)
        assert output.read_bytes() == b'keep this file'
        keys = list(app.query(FooterKey))
        assert [key.key_display for key in keys] == ['q','n','p','r','c','d','l','?']
        assert all(key.action != 'clear_queue' for key in keys)
    restored = Engine(app.engine.config)
    try:
        assert not restored.jobs
    finally:
        restored.store.close()


async def test_shift_d_in_a_nonempty_url_is_text_not_clear(tmp_path):
    from textual.widgets import Input
    app = paused_app(tmp_path, 3)
    async with app.run_test(size=(120,32)) as pilot:
        field = app.query_one(Input)
        field.value = 'https://example.com/'
        await pilot.press('D')
        assert field.value.endswith('/D')
        assert len(app.engine.jobs) == 3
        field.value = ''
        await pilot.press('D')
        await pilot.pause(.3)
        assert not app.engine.jobs
