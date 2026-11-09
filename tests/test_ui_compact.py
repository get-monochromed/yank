from textual.widgets import Button, Footer, Input, ListView

from yank.config import Config
from yank.engine import Engine


async def test_single_line_queue_and_small_controls(tmp_path):
    from yank.ui import YankApp, DownloadRow, ProgressLine
    config = Config(state_dir=tmp_path/'state', path=tmp_path/'config.toml')
    engine = Engine(config)
    for suffix in ('one', 'two'):
        job = engine.add('https://example.com/'+suffix)
        engine.control(job.id, 'pause')
        job.title = 'A [literal] video title'
        job.downloaded = 512
        job.total = 1024
        engine.touch(job, True)
    app = YankApp(config, engine=engine)
    async with app.run_test(size=(160,40)) as pilot:
        await pilot.pause()
        assert not app.query('#controls'), 'remove the duplicate large toolbar'
        rows = list(app.query(DownloadRow))
        assert all(row.region.height == 1 for row in rows)
        assert rows[1].region.y == rows[0].region.y+1
        text = app.query_one(ProgressLine).render()
        assert '\n' not in text.plain
        for value in ('A [literal] video title', 'PAUSED', '512', '1.0 KiB', 'aria2c', 'attempt', '50.0%'):
            assert value in text.plain
        field = app.query_one(Input)
        add = app.query_one('#add', Button)
        paste = app.query_one('#paste', Button)
        assert add.region.height == paste.region.height == 1
        assert field.region.height <= 1.5*add.region.height
        assert paste.region.y == add.region.y+1
        assert app.query_one(Footer).region.height == 1
        banner = app.query_one('#banner')
        assert abs(banner.region.x+banner.region.width/2-80) <= 1
        await pilot.click(f'#job-{rows[0].job.id}')
        await pilot.press('question_mark')
        await pilot.pause()
        assert app.screen.query_one('#close-dialog', Button).region.height == add.region.height
        await pilot.click('#close-dialog')
        await pilot.press('l')
        await pilot.pause()
        assert app.screen.query_one('#close-dialog', Button).region.height == add.region.height
        await pilot.click('#close-dialog')


async def test_primary_shortcuts_and_url_characters(tmp_path):
    from yank.ui import YankApp
    app = YankApp(Config(state_dir=tmp_path/'state', path=tmp_path/'config.toml'))
    async with app.run_test(size=(100,30)) as pilot:
        await pilot.pause()
        visible = {binding.action: binding for _, binding, enabled, tooltip in app.screen.active_bindings.values() if binding.show}
        assert visible['quit'].key == 'q'
        assert visible['input'].key == 'n'
        assert visible['help'].key == 'question_mark'
        await pilot.press('question_mark')  # Also works from the empty URL input.
        assert len(app.screen_stack) == 2
        await pilot.press('escape', 'n')
        field = app.query_one(Input)
        assert app.focused is field
        assert field.value == ''
        field.value = 'https://example.com/'
        await pilot.press('q', 'n', 'question_mark')
        assert field.value.endswith('/qn?'), 'shortcuts must not eat URL characters'
        field.value = ''
        await pilot.press('q')
    assert app.engine._closed


async def test_footer_controls_stay_clickable_while_editing(tmp_path):
    from textual.widgets._footer import FooterKey
    from yank.ui import YankApp
    config = Config(state_dir=tmp_path/'state', path=tmp_path/'config.toml', ytdlp='/usr/bin/false')
    engine = Engine(config)
    job = engine.add('https://example.com/paused')
    engine.control(job.id, 'pause')
    app = YankApp(config, engine=engine)
    async with app.run_test(size=(120,30)) as pilot:
        field = app.query_one(Input)
        field.value = 'https://example.com/editing'
        await pilot.pause()
        keys = {key.action: key for key in app.query(FooterKey)}
        assert {'quit', 'input', 'toggle', 'retry', 'cancel', 'remove', 'details', 'help'} <= keys.keys()
        await pilot.click(keys['cancel'])
        assert job.status == 'cancelled'
        assert field.value == 'https://example.com/editing'
        await pilot.click(keys['help'])
        assert len(app.screen_stack) == 2
        await pilot.click('#close-dialog')


async def test_footer_has_two_spaces_between_key_label_groups(tmp_path):
    from textual.widgets._footer import FooterKey
    from rich.cells import cell_len
    from yank.ui import YankApp
    app = YankApp(Config(state_dir=tmp_path/'state', path=tmp_path/'config.toml'))
    async with app.run_test(size=(120,24)) as pilot:
        await pilot.pause()
        keys = list(app.query(FooterKey))
        expected = ['q Quit', 'n New URL', 'p Pause/resume', 'r Retry',
                    'c Cancel', 'd Remove', 'l Details', '? Help']
        assert [key.render().plain for key in keys] == [label+'  ' for label in expected]
        for previous, following, label in zip(keys, keys[1:], expected):
            assert following.region.x-(previous.region.x+cell_len(label)) == 2
        assert app.query_one(Footer).region.height == 1
