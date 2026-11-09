"""Textual terminal UI. Engine state is also exposed through the JSON CLI."""
from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path

from rich.cells import cell_len
from rich.text import Text
from textual import events, on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, Footer, Input, ListItem, ListView, Static
from textual.widgets._footer import FooterKey

from .config import Config
from .clipboard import ClipboardError, read_clipboard
from .engine import Engine
from .model import ACTIVE, Job

BANNER = ''' █████ █████                   █████
░░███ ░░███                   ░░███
 ░░███ ███   ██████   ████████  ░███ █████
  ░░█████   ░░░░░███ ░░███░░███░███░░███
   ░░███     ███████  ░███ ░███░██████░
    ░███    ███░░███  ░███ ░███░███░░███
    █████  ░░████████ ████ █████████ █████
   ░░░░░    ░░░░░░░░ ░░░░ ░░░░░░░░ ░░░░░'''
COLORS = {'done': '#275644', 'paused': '#424148', 'cancelled': '#4a3440',
          'retrying': '#674d2a', 'queued': '#283646', 'processing': '#42466b'}


def clean(text: str) -> str:
    return re.sub(r'[\x00-\x1f\x7f-\x9f]', ' ', text).strip()


def size(value: float) -> str:
    for unit in ('B', 'KiB', 'MiB', 'GiB', 'TiB'):
        if value < 1024:
            return f'{value:.1f} {unit}'
        value /= 1024
    return f'{value:.1f} PiB'


def duration(seconds: float | None) -> str:
    if seconds is None:
        return '--:--'
    seconds = max(0, int(seconds))
    return f'{seconds // 3600}:{seconds // 60 % 60:02}:{seconds % 60:02}' if seconds >= 3600 else f'{seconds // 60}:{seconds % 60:02}'


class ProgressLine(Static):
    def __init__(self, job: Job):
        super().__init__()
        self.job = job

    def render(self) -> Text:
        job = self.job
        width = max(1, self.size.width)
        selected = bool(self.parent and self.parent.has_class('-highlight'))
        base = '#18232c' if selected else '#121a22'
        fill = COLORS.get(job.status, '#23545c')
        percent = f'{job.progress:6.1%}' if job.total or job.status == 'done' else '   --%'
        prefix = '› ' if selected else '  '
        meta = f'{percent.strip()} {job.status.upper()} · {size(job.downloaded)}/{size(job.total) if job.total else "?"}'
        if job.status == 'retrying':
            meta += f' · retry {max(0, int(job.retry_at - time.time()))}s'
        elif job.status == 'downloading':
            meta += f' · {size(job.speed)}/s · ETA {duration(job.eta)}'
        elif job.status == 'processing':
            meta += ' · finalizing'
        meta += f' · {job.backend} · attempt {job.attempt}'
        title_width = max(min(24, width // 2), width - cell_len(meta) - 3)
        result = Text(prefix + clean(job.title), style='#e5edf3')
        result.truncate(title_width, overflow='ellipsis', pad=True)
        result.append(' │ ', style='#587080')
        result.append(meta, style='#9dafbf')
        result.truncate(width, overflow='ellipsis', pad=True)
        result.stylize(f'on {base}')
        # Rich offsets count characters, while a terminal measures cell widths.
        target = int(width * job.progress)
        cells = boundary = 0
        for character in result.plain:
            if cells + cell_len(character) > target:
                break
            cells += cell_len(character)
            boundary += 1
        result.stylize(f'on {fill}', 0, boundary)
        return result


class URLInput(Input):
    def check_consume_key(self, key: str, character: str | None) -> bool:
        # Keep mouse-clickable footer actions visible even while editing.
        # Actual key events still go to Input, so URL characters remain text.
        if key in ('q', 'n', 'question_mark', 'p', 'r', 'c', 'd', 'l'):
            return False
        return super().check_consume_key(key, character)

    async def _on_key(self, event: events.Key):
        action = {'q': 'quit', 'n': 'input', '?': 'help', 'D': 'clear_queue'}.get(event.character)
        if not self.value and action:
            event.prevent_default()
            event.stop()
            await self.app.run_action(action)

    def _on_paste(self, event: events.Paste):
        # Textual Input normally discards everything after the first newline.
        normalized = events.Paste(' '.join(event.text.split()))
        super()._on_paste(normalized)
        event.prevent_default()
        event.stop()


class DownloadList(ListView):
    BINDINGS = [Binding('enter', 'app.details', 'Details', show=False)]


class CommandFooterKey(FooterKey):
    def render(self) -> Text:
        label = super().render()
        label.append('  ')
        return label

    async def on_mouse_down(self, event: events.MouseDown):
        # Textual's default footer simulates a keystroke, which a focused Input
        # consumes as text. Mouse actions must invoke the command directly.
        event.prevent_default()
        event.stop()
        if self._disabled:
            self.app.bell()
        else:
            await self.app.run_action(self.action)


class CommandFooter(Footer):
    def compose(self) -> ComposeResult:
        for widget in super().compose():
            if isinstance(widget, FooterKey):
                yield CommandFooterKey(widget.key, widget.key_display, widget.description,
                                       widget.action, disabled=widget._disabled)
            else:
                yield widget


class DownloadRow(ListItem):
    def __init__(self, job: Job):
        super().__init__(id=f'job-{job.id}')
        self.job = job

    def compose(self) -> ComposeResult:
        yield ProgressLine(self.job)

    def on_click(self):
        if isinstance(self.parent, ListView):
            self.parent.focus()


HELP = '''QUEUE CONTROLS
↑ / ↓       Select a download (↑ from the URL box enters the queue)
p / Space   Pause or resume the selected entry
r           Retry now / resume a cancelled entry
c           Cancel; keep its entry and partial files
d / Delete  Remove the entry; NEVER delete downloaded or partial files
Shift+D     Clear the whole queue and stop downloads; NEVER delete files
Enter / l   Details, last error, and live downloader log
o           Open a completed file in your default player
n / i / /   Focus the URL box
Esc         Return to queue / close this dialog
?           Help
q           Quit safely; active entries resume next launch

Paste one or several HTTP(S) links into the bottom box and press Enter.
Buttons, rows, dialogs and scrolling all support the mouse.
q, n, ? and Shift+D also work while the URL box is empty. While editing a URL,
ordinary letters type text; Ctrl+Q and Ctrl+N remain optional fallbacks.
Click Paste to paste clipboard URLs and immediately start downloading.
+ Add (or Enter) still submits manually entered URLs.

RELIABILITY
Failures retry indefinitely with capped exponential backoff. Each retry
re-extracts the page to refresh expired URLs. Transport alternates between
aria2 and native HTTP; HLS/DASH use yt-dlp's native downloader. A watchdog
restarts silent/stalled processes. Pausing stops the whole process group;
resuming re-extracts and continues compatible partial files.

No tool guarantees access to every site. Login, CAPTCHA, DRM, removed
videos and persistent blocks may need intervention. See the error hint.
Configure cookies/proxy in config.toml; no TLS checks are disabled.
Playlist-only URLs run as one entry (progress is for the current video).

Quitting closes the download engine too. Use `yank serve` for a headless
runner and `yank state` / `yank pause ID` etc. to control a running app.
'''


class InfoDialog(ModalScreen):
    BINDINGS = [('escape', 'dismiss', 'Close'), ('q', 'dismiss', 'Close')]

    def __init__(self, engine: Engine, job_id: str | None = None):
        super().__init__()
        self.engine = engine
        self.job_id = job_id

    def compose(self) -> ComposeResult:
        with Vertical(id='dialog'):
            yield Static('DOWNLOAD DETAILS' if self.job_id else 'YANK / FIELD GUIDE', id='dialog-title')
            with VerticalScroll(id='dialog-scroll'):
                yield Static('', id='dialog-body', markup=False)
            yield Button('Close', id='close-dialog', compact=True, tooltip='Close (Esc)')

    def on_mount(self):
        self.update_body()
        if self.job_id:
            self.set_interval(1, self.update_body)

    def update_body(self):
        if not self.job_id:
            body = HELP + f'\nCONFIG\n{self.engine.config.path}\n\nDOWNLOADS\n{self.engine.config.download_dir}'
        elif job := self.engine.jobs.get(self.job_id):
            path = self.engine.config.state_dir / 'logs' / f'{job.id}.log'
            log = '\n'.join(path.read_text(errors='replace').splitlines()[-60:]) if path.exists() else 'No output yet.'
            body = (f'{job.title}\n\n{job.url}\n\nID: {job.id}\nState: {job.status} · attempt {job.attempt} · {job.backend}'
                    f'\nDestination: {job.destination}\nFile: {job.filename or "not finished"}'
                    f'\n\nLAST ERROR\n{job.last_error or "None"}\n\n{job.hint}\n\nLOG (latest 60 lines)\n{log}')
        else:
            body = 'This entry has been removed.'
        self.query_one('#dialog-body', Static).update(re.sub(r'[\x00-\x08\x0b-\x1f\x7f-\x9f]', '', body))

    @on(Button.Pressed, '#close-dialog')
    def close_dialog(self):
        self.dismiss()


class MainScreen(Screen):
    def on_resize(self, event: events.Resize):
        compact = event.size.height < 20 or event.size.width < 48
        self.set_class(compact, 'compact')
        if self.query('#banner'):
            self.query_one('#banner', Static).update('Y A N K  /  DOWNLOAD MANAGER' if compact else BANNER)


class YankApp(App):
    TITLE = 'Yank'
    ENABLE_COMMAND_PALETTE = False
    CSS = '''
    Screen { background: #0b1117; color: #dce7ee; layout: vertical; }
    #brand { height: auto; padding: 0 1; border-bottom: solid #263849; align: center top; }
    #banner { width: auto; height: 8; color: #77d4c8; text-wrap: nowrap; }
    #tagline { width: auto; height: 1; color: #849aac; }
    #summary { height: 1; padding: 0 1; color: #9bb2c4; }
    #queue { height: 1fr; margin: 0 1; background: transparent; border: none; }
    #queue:focus { border: none; }
    DownloadRow { height: 1; margin: 0; padding: 0; background: transparent; }
    DownloadRow.-highlight, DownloadRow:hover { background: transparent; }
    ProgressLine { height: 1; width: 1fr; }
    #empty { height: 1fr; content-align: center middle; color: #7d93a6; }
    #input-row { height: 2; margin: 0 1; }
    #input-main { width: 1fr; height: 2; }
    #url { height: 1; padding: 0 1; border: none; background: #172630; }
    #url:focus { background: #1c303b; }
    #inspector { height: 1; color: #9a937f; text-wrap: nowrap; text-overflow: ellipsis; }
    #input-actions { width: 9; height: 2; margin-left: 1; }
    #input-actions Button, #close-dialog { min-width: 0; width: 9; height: 1; padding: 0; border: none; background: #244b4a; text-style: none; }
    #input-actions Button:hover, #close-dialog:hover { background: #32605b; }
    Footer { height: 1; background: #101b25; color: #9bb2c4; }
    Screen.compact #banner { height: 1; }
    Screen.compact #tagline { display: none; }
    Screen.compact #brand { height: 2; }
    InfoDialog { align: center middle; background: #000000 70%; }
    #dialog { width: 88%; max-width: 108; height: 88%; border: round #77d4c8; background: #101a24; padding: 1 2; }
    #dialog-title { height: 1; color: #77d4c8; text-style: bold; }
    #dialog-scroll { height: 1fr; }
    #dialog-body { height: auto; }
    #close-dialog { margin-top: 1; }
    '''
    BINDINGS = [
        Binding('q', 'quit', 'Quit', key_display='q'),
        Binding('n', 'input', 'New URL', key_display='n'),
        Binding('ctrl+q', 'quit', show=False, priority=True),
        Binding('ctrl+n', 'input', show=False, priority=True),
        Binding('i,slash', 'input', show=False), Binding('escape', 'queue', show=False),
        Binding('up', 'queue', show=False), Binding('down', 'queue', show=False),
        Binding('p', 'toggle', 'Pause/resume', key_display='p'),
        Binding('space', 'toggle', show=False), Binding('r', 'retry', 'Retry', key_display='r'),
        Binding('c', 'cancel', 'Cancel', key_display='c'), Binding('d', 'remove', 'Remove', key_display='d'),
        Binding('D', 'clear_queue', show=False),
        Binding('delete', 'remove', show=False), Binding('l', 'details', 'Details', key_display='l'),
        Binding('enter', 'details', show=False), Binding('o', 'open_file', show=False),
        Binding('question_mark', 'help', 'Help', key_display='?'), Binding('f1', 'help', show=False)]

    def __init__(self, config: Config, urls=(), engine: Engine | None = None):
        super().__init__()
        self.engine = engine or Engine(config)
        self.initial_urls = urls
        self._last_revision = -1
        self._ids: list[str] = []

    def compose(self) -> ComposeResult:
        with Vertical(id='brand'):
            yield Static(BANNER, id='banner', markup=False)
            yield Static('yt-dlp × aria2', id='tagline')
        yield Static('', id='summary', markup=False)
        yield Static('Your queue is empty.\nPaste a link below and press Enter.', id='empty')
        yield DownloadList(id='queue')
        with Horizontal(id='input-row'):
            with Vertical(id='input-main'):
                yield URLInput(placeholder='Paste video URL(s), then Enter…', id='url', select_on_focus=False, compact=True)
                yield Static('', id='inspector', markup=False)
            with Vertical(id='input-actions'):
                yield Button('+ Add', id='add', compact=True)
                yield Button('Paste', id='paste', compact=True, tooltip='Paste clipboard URLs and start downloading')
        yield CommandFooter()

    def get_default_screen(self) -> Screen:
        return MainScreen(id='_default')

    async def on_mount(self):
        try:
            await self.engine.start()
            for url in self.initial_urls:
                self.engine.add(url)
        except (RuntimeError, ValueError, OSError) as error:
            self.exit(message=str(error), return_code=1)
            return
        self.set_interval(.2, self.refresh_queue)
        await self.refresh_queue()
        self.query_one(Input).focus()
        self.on_resize()

    async def on_unmount(self):
        await self.engine.close()

    def on_resize(self, event: events.Resize | None = None):
        compact = self.size.height < 20 or self.size.width < 48
        self.screen_stack[0].set_class(compact, 'compact')
        if self.is_mounted and self.query('#banner'):
            self.query_one('#banner', Static).update('Y A N K  /  DOWNLOAD MANAGER' if compact else BANNER)

    def selected(self) -> Job | None:
        if not self.engine.jobs:
            return None
        queue = self.query_one('#queue', ListView)
        row = queue.highlighted_child
        if isinstance(row, DownloadRow) and row.job.id in self.engine.jobs:
            return self.engine.jobs[row.job.id]
        # Key repeat can beat the periodic render. The highlighted widget may
        # still refer to a job that was just removed; choose the next live row
        # (or the previous one at the end) rather than sending a stale ID.
        rows = list(queue.children)
        index = queue.index or 0
        for candidate in rows[index:] + list(reversed(rows[:index])):
            if isinstance(candidate, DownloadRow) and candidate.job.id in self.engine.jobs:
                return self.engine.jobs[candidate.job.id]
        jobs = list(self.engine.jobs.values())
        return jobs[min(index, len(jobs) - 1)]

    async def refresh_queue(self):
        if self.engine.stopped.is_set():
            self.exit()
            return
        if not self.query('#queue'):
            return
        self.on_resize()
        queue = self.query_one('#queue', ListView)
        ids = list(self.engine.jobs)
        if ids != self._ids:
            selected = self.selected()
            old_index = queue.index or 0
            await queue.clear()
            await queue.extend(DownloadRow(job) for job in self.engine.jobs.values())
            if ids:
                queue.index = ids.index(selected.id) if selected and selected.id in ids else min(old_index, len(ids) - 1)
            self._ids = ids
        self.query_one('#empty').display = not ids
        queue.display = bool(ids)
        for row in queue.children:
            row.query_one(ProgressLine).refresh()
        jobs = list(self.engine.jobs.values())
        active = sum(job.status in ACTIVE for job in jobs)
        done = sum(job.status == 'done' for job in jobs)
        speed = sum(job.speed for job in jobs if job.status == 'downloading')
        self.query_one('#summary', Static).update(f'{len(jobs)} ENTRIES   /   {active} ACTIVE   /   {done} FINISHED   /   {size(speed)}/s')
        if selected := self.selected():
            text = selected.last_error if selected.last_error else selected.url
        else:
            text = 'Ready when you are. Downloaded files are never deleted by queue controls.'
        self.query_one('#inspector', Static).update(text)

    @on(Input.Submitted, '#url')
    def submit_url(self):
        field = self.query_one(Input)
        urls = field.value.split()
        if not urls:
            return
        failed = []
        for url in urls:
            try:
                self.engine.add(url)
            except ValueError as error:
                failed.append(url)
                self.notify(str(error), severity='error')
        field.value = ' '.join(failed)
        field.focus()

    @on(Button.Pressed)
    async def button(self, event: Button.Pressed):
        if event.button.id == 'add':
            self.submit_url()
        elif event.button.id == 'paste':
            self.run_worker(self.paste_clipboard(), group='clipboard', exclusive=True)

    async def paste_clipboard(self):
        button = self.query_one('#paste', Button)
        button.disabled = True
        try:
            text = await read_clipboard()
            if not text.strip():
                self.notify('The clipboard is empty.')
                return
            field = self.query_one(Input)
            field.focus()
            field.replace(' '.join(text.split()), *field.selection)
            self.submit_url()
        except ClipboardError as error:
            self.notify(str(error), severity='warning')
        finally:
            button.disabled = False

    def action_input(self):
        self.query_one(Input).focus()

    def action_queue(self):
        queue = self.query_one(ListView)
        if self._ids:
            from_input = isinstance(self.focused, Input)
            queue.focus()
            if from_input or queue.index is None:
                queue.index = len(self._ids) - 1

    def control(self, action: str):
        if job := self.selected():
            self.engine.control(job.id, action)
        elif action != 'remove':
            self.notify('Select a download first.')

    def action_toggle(self):
        if job := self.selected():
            self.control('resume' if job.status in ('paused', 'cancelled') else 'pause')

    def action_retry(self):
        self.control('retry')

    def action_cancel(self):
        self.control('cancel')

    def action_remove(self):
        self.control('remove')

    def action_clear_queue(self):
        count = self.engine.clear()
        if count:
            self.notify(f'Removed {count} queue entries. Files kept.')

    def action_details(self):
        if job := self.selected():
            self.push_screen(InfoDialog(self.engine, job.id))

    def action_help(self):
        self.push_screen(InfoDialog(self.engine))

    def action_open_file(self):
        if (job := self.selected()) and job.status == 'done' and Path(job.filename).is_file():
            subprocess.Popen(['xdg-open', job.filename], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            self.notify('No completed file selected.')

    async def action_quit(self):
        await self.engine.close()
        self.exit()
