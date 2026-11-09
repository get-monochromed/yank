"""Read plain clipboard text on demand without changing the clipboard."""
from __future__ import annotations

import asyncio
import os
import shutil

from .text import safe_text


class ClipboardError(RuntimeError):
    pass


async def read_clipboard(timeout: float = 3) -> str:
    commands = []
    if os.environ.get('WAYLAND_DISPLAY') or os.environ.get('XDG_SESSION_TYPE') == 'wayland':
        commands.append(['wl-paste', '--no-newline', '--type', 'text'])
    if os.environ.get('DISPLAY'):
        commands += [['xclip', '-selection', 'clipboard', '-o'], ['xsel', '--clipboard', '--output']]
    errors = []
    for command in commands:
        executable = shutil.which(command[0])
        if not executable:
            continue
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(executable, *command[1:],
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.DEVNULL)
            out, err = await asyncio.wait_for(proc.communicate(), timeout)
            if proc.returncode == 0:
                return out.decode('utf-8', errors='replace')
            errors.append(safe_text(err.decode('utf-8', errors='replace')).strip()[:200])
        except (OSError, asyncio.TimeoutError) as error:
            errors.append(f'{command[0]}: {type(error).__name__}')
        finally:
            if proc is not None and proc.returncode is None:
                proc.kill()
                await proc.communicate()
    if errors:
        raise ClipboardError('Cannot read clipboard text: ' + '; '.join(errors))
    raise ClipboardError('Clipboard access needs wl-clipboard (Wayland) or xclip/xsel (X11). You can still use Ctrl+Shift+V.')
