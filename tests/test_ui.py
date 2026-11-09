import asyncio
from pathlib import Path

from textual.widgets import Input, ListView
from yank.config import Config
from yank.engine import Engine


async def test_mouse_keyboard_input_and_row_progress(tmp_path):
    from yank.ui import YankApp, DownloadRow, ProgressLine
    config = Config(state_dir=tmp_path/'state', path=tmp_path/'config.toml', download_dir=tmp_path/'downloads', ytdlp='/usr/bin/false')
    engine = Engine(config)
    first = engine.add('https://example.com/first')
    second = engine.add('https://example.com/second')
    engine.control(first.id, 'pause')
    engine.control(second.id, 'pause')
    first.title = 'A [literal] title with wide characters: café'
    first.total = 1000
    first.downloaded = 500
    engine.touch(first, True)
    app = YankApp(config, engine=engine)
    async with app.run_test(size=(120, 42)) as pilot:
        await pilot.pause()
        assert isinstance(app.focused, Input)
        assert app.query_one(Input).region.bottom <= 41
        line = app.query_one(ProgressLine).render()
        assert '[literal]' in line.plain
        assert any(span.style and 'on #' in str(span.style) for span in line.spans)
        await pilot.click(f'#job-{first.id}')
        assert isinstance(app.focused, ListView)
        await pilot.press('c')
        await pilot.pause()
        assert first.status == 'cancelled'
        await pilot.press('down', 'd')
        await pilot.pause()
        assert second.id not in engine.jobs
        await pilot.press('i')
        assert isinstance(app.focused, Input)
        await pilot.press('c')
        assert app.query_one(Input).value == 'c'
        app.query_one(Input).value = 'https://example.com/new'
        await pilot.press('enter')
        await pilot.pause()
        assert any(job.url.endswith('/new') for job in engine.jobs.values())
        assert app.query_one(Input).value == ''
        async with asyncio.timeout(2):
            while len(app.query(DownloadRow)) != 2:
                await pilot.pause(.05)
        assert len(app.query(DownloadRow)) == 2
        await pilot.press('up', 'p')
        await pilot.pause()
        assert app.query_one(ListView).highlighted_child.job.status == 'paused'
        await pilot.press('question_mark')
        await pilot.pause()
        assert len(app.screen_stack) == 2
        await pilot.press('escape')
        assert len(app.screen_stack) == 1
        await pilot.resize_terminal(60, 22)
        await pilot.pause()
        assert app.query_one(Input).region.bottom <= 22


async def test_multiline_paste_and_small_terminal_launch(tmp_path):
    from textual import events
    from yank.ui import YankApp
    config = Config(state_dir=tmp_path/'state', path=tmp_path/'config.toml')
    app = YankApp(config)
    async with app.run_test(size=(60,18)) as pilot:
        field = app.query_one(Input)
        field.post_message(events.Paste('https://example.com/one\nhttps://example.org/two'))
        await pilot.pause()
        assert field.value.split() == ['https://example.com/one', 'https://example.org/two']
        assert field.region.bottom <= 17
