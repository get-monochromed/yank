"""TUI launcher, JSON automation interface and headless queue runner."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import sys
import tomllib

from . import __version__
from .config import Config
from .engine import Engine, InstanceLock, request
from .model import PENDING
from .text import safe_text


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description='Yank — mouse-friendly yt-dlp download manager. No arguments opens the TUI.')
    root.add_argument('--version', action='version', version=__version__)
    root.add_argument('--config', type=Path, help='alternate TOML configuration file')
    subs = root.add_subparsers(dest='command')
    tui = subs.add_parser('tui', help='open the terminal interface')
    tui.add_argument('urls', nargs='*')
    add = subs.add_parser('add', help='enqueue URLs; immediately downloads if Yank is open')
    add.add_argument('urls', nargs='+')
    subs.add_parser('state', help='print JSON queue state (also works offline)')
    subs.add_parser('list', help='print a readable queue summary')
    subs.add_parser('clear', help='stop downloads and remove all queue entries; never deletes media files')
    for action in ('pause', 'resume', 'retry', 'cancel', 'remove'):
        command = subs.add_parser(action, help=f'{action} a queue entry by ID; remove never deletes files')
        command.add_argument('id')
    serve = subs.add_parser('serve', help='run the download engine without the TUI')
    serve.add_argument('--until-done', action='store_true', help='exit when no queued, running or retrying entries remain')
    subs.add_parser('stop', help='stop a running TUI/engine safely; pending entries resume next launch')
    logs = subs.add_parser('logs', help='show the selected download log')
    logs.add_argument('id')
    logs.add_argument('--lines', type=int, default=80)
    config = subs.add_parser('config', help='print config path or edit it')
    config.add_argument('--edit', action='store_true')
    subs.add_parser('doctor', help='check downloader/runtime availability')
    return root


async def operation(config: Config, payload: dict):
    try:
        response = await request(config, payload)
    except (ConnectionRefusedError, FileNotFoundError):
        if payload['action'] == 'stop':
            raise ValueError('Yank is not running') from None
        if payload['action'] == 'state':
            # SQLite readers do not need exclusive engine ownership. Taking
            # that lock for status polling can make an engine starting at the
            # same instant incorrectly exit with "already running".
            engine = Engine(config)
            try:
                return engine.state()
            finally:
                engine.store.close()
        lock = InstanceLock(config)
        engine = Engine(config)
        try:
            if payload['action'] == 'add':
                response = {'job': engine.add(payload['url']).to_dict()}
            elif payload['action'] == 'clear':
                response = {'removed': engine.clear()}
            else:
                engine.control(payload['id'], payload['action'])
                response = {'ok': True}
        finally:
            engine.store.close()
            lock.close()
    if 'error' in response:
        raise ValueError(response['error'])
    return response


async def serve(config: Config, until_done: bool):
    engine = Engine(config)
    try:
        await engine.start()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, engine.stopped.set)
        while not engine.stopped.is_set():
            if until_done and not any(job.status in PENDING for job in engine.jobs.values()):
                break
            try:
                await asyncio.wait_for(engine.stopped.wait(), .2)
            except asyncio.TimeoutError:
                pass
    finally:
        await engine.close()


def doctor(config: Config) -> dict:
    result = {'config': str(config.path), 'download_dir':str(config.download_dir),
              'inherits_ytdlp_config':config.inherit_ytdlp_config, 'tools':{}}
    for tool in (config.ytdlp, 'aria2c', 'ffmpeg', 'ffprobe', 'deno', 'node'):
        path = shutil.which(tool)
        entry = {'path':path}
        if path:
            try:
                version = subprocess.run([path, '--ignore-config', '--version'] if tool == config.ytdlp else [path, '-version' if tool in ('ffmpeg', 'ffprobe') else '--version'],
                    capture_output=True, text=True, timeout=15)
                entry['version'] = (version.stdout or version.stderr).splitlines()[0]
            except (OSError, subprocess.TimeoutExpired, IndexError) as error:
                entry['error'] = str(error)
        result['tools'][tool] = entry
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        config = Config.load(args.config)
        command = args.command or 'tui'
        if command == 'tui':
            from .ui import YankApp
            YankApp(config, urls=getattr(args, 'urls', [])).run()
            return
        if command == 'serve':
            asyncio.run(serve(config, args.until_done))
            return
        if command == 'config':
            if args.edit:
                subprocess.run([*shlex.split(os.environ.get('EDITOR', 'nvim')), str(config.path)], check=True)
            else:
                print(config.path)
            return
        if command == 'doctor':
            result = doctor(config)
        elif command == 'logs':
            state = asyncio.run(operation(config, {'action':'state'}))
            if args.id not in {job['id'] for job in state['jobs']}:
                raise ValueError(f'No queue entry: {args.id}')
            path = config.state_dir / 'logs' / f'{args.id}.log'
            print(safe_text(''.join(path.read_text(errors='replace').splitlines(keepends=True)[-max(1, args.lines):]), multiline=True) if path.exists() else 'No log yet.')
            return
        elif command == 'add':
            result = {'jobs':[asyncio.run(operation(config, {'action':'add', 'url':url}))['job'] for url in args.urls]}
        elif command in ('state', 'list'):
            result = asyncio.run(operation(config, {'action':'state'}))
            if command == 'list':
                for job in result['jobs']:
                    print(f"{job['id']}  {job['status']:12} {job['progress']:6.1%}  {safe_text(job['title'])}")
                return
        else:
            result = asyncio.run(operation(config, {'action':command, 'id':getattr(args, 'id', '')}))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except KeyboardInterrupt:
        pass
    except (ValueError, OSError, RuntimeError, tomllib.TOMLDecodeError, subprocess.SubprocessError) as error:
        print(f'yank: {error}', file=sys.stderr)
        raise SystemExit(1) from None
