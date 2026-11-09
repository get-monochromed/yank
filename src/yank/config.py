"""XDG configuration. No changes to the user's global yt-dlp configuration."""
from __future__ import annotations

import math
import os
import re
from urllib.parse import urlsplit
from dataclasses import dataclass, field, fields
from pathlib import Path
import tomllib

DEFAULT_CONFIG = '''# Yank — edit, then restart Yank. Paths support ~ and environment variables.
download_dir = "~/Downloads/yank"
concurrent_downloads = 3
aria2_connections = 4
# Retry forever, with capped exponential backoff (seconds).
retry_initial = 5
retry_max = 300
# Restart a process that emits no output for this many seconds. 0 disables it.
stall_timeout = 180
socket_timeout = 30
# Preserve your existing yt-dlp cookies, JS helpers, extractor options, etc.
# Yank overrides output, progress, retry and downloader flags for queue safety.
inherit_ytdlp_config = true
ytdlp = "yt-dlp"
# Leave blank to inherit yt-dlp settings; use a browser/profile you own.
cookies_from_browser = ""
cookies_file = ""
proxy = ""
# Optional TLS impersonation requires curl_cffi in yt-dlp's environment.
# Do not enable globally unless a specific site needs it.
impersonate = ""
format = "bv*+ba/b"
merge_output_format = "mkv"
# Explicit additional yt-dlp arguments (no shell). Do not override output/progress.
extra_args = []

# Optional source-domain destinations. Quote domain names; subdomains match too.
# Put this table AFTER the general settings above.
[domain_dirs]
# "youtube.com" = "~/Downloads/youtube"
# "youtu.be" = "~/Downloads/youtube"
# "website.com" = "~/Videos/website"
'''


def xdg(name: str, fallback: str) -> Path:
    return Path(os.environ.get(name, str(Path.home() / fallback))).expanduser() / 'yank'


def expand(value: str) -> Path:
    return Path(os.path.expandvars(value)).expanduser().absolute()


@dataclass
class Config:
    path: Path = field(default_factory=lambda: xdg('XDG_CONFIG_HOME', '.config') / 'config.toml')
    state_dir: Path = field(default_factory=lambda: xdg('XDG_STATE_HOME', '.local/state'))
    download_dir: Path = field(default_factory=lambda: Path.home() / 'Downloads/yank')
    domain_dirs: dict[str, Path] = field(default_factory=dict)
    concurrent_downloads: int = 3
    aria2_connections: int = 4
    retry_initial: float = 5
    retry_max: float = 300
    stall_timeout: float = 180
    socket_timeout: float = 30
    inherit_ytdlp_config: bool = True
    ytdlp: str = 'yt-dlp'
    cookies_from_browser: str = ''
    cookies_file: str = ''
    proxy: str = ''
    impersonate: str = ''
    format: str = 'bv*+ba/b'
    merge_output_format: str = 'mkv'
    extra_args: list[str] = field(default_factory=list)

    def destination_for(self, url: str) -> Path:
        host = (urlsplit(url).hostname or '').lower().rstrip('.')
        matches = [domain for domain in self.domain_dirs
                   if host == domain or host.endswith('.' + domain)]
        return self.domain_dirs[max(matches, key=len)] if matches else self.download_dir

    @classmethod
    def load(cls, path: Path | None = None) -> Config:
        config = cls()
        if path is not None:
            config.path = path.expanduser().absolute()
        config.path.parent.mkdir(parents=True, exist_ok=True)
        if not config.path.exists():
            with config.path.open('x', encoding='utf-8') as file:
                file.write(DEFAULT_CONFIG)
            config.path.chmod(0o600)
        with config.path.open('rb') as file:
            data = tomllib.load(file)
        allowed = {f.name for f in fields(cls)} - {'path', 'state_dir'}
        for key, value in data.items():
            if key not in allowed:
                raise ValueError(f'Unknown config option: {key}')
            setattr(config, key, expand(value) if key == 'download_dir' and isinstance(value, str) else value)
        for key in ('concurrent_downloads', 'aria2_connections'):
            value = getattr(config, key)
            if type(value) is not int or not 1 <= value <= (32 if key == 'concurrent_downloads' else 16):
                raise ValueError(f'{key} must be an integer from 1 to {32 if key == "concurrent_downloads" else 16}')
        for key in ('retry_initial', 'retry_max', 'stall_timeout', 'socket_timeout'):
            value = getattr(config, key)
            if type(value) not in (int, float) or not math.isfinite(value) or value < (0 if key == 'stall_timeout' else 0.01):
                raise ValueError(f'{key} must be a finite positive number (stall_timeout also accepts 0)')
        if config.retry_max < config.retry_initial:
            raise ValueError('retry_max must be >= retry_initial')
        if type(config.inherit_ytdlp_config) is not bool:
            raise ValueError('inherit_ytdlp_config must be true or false')
        for key in ('ytdlp', 'cookies_from_browser', 'cookies_file', 'proxy', 'impersonate', 'format', 'merge_output_format'):
            if not isinstance(getattr(config, key), str):
                raise ValueError(f'{key} must be a string')
        if not isinstance(config.download_dir, Path):
            raise ValueError('download_dir must be a path string')
        if not isinstance(config.domain_dirs, dict):
            raise ValueError('domain_dirs must be a TOML table of quoted domains and folder paths')
        routes = {}
        domain_pattern = r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.?'
        for domain, destination in config.domain_dirs.items():
            if not isinstance(domain, str) or len(domain) > 253 or not re.fullmatch(domain_pattern, domain, re.IGNORECASE):
                raise ValueError(f'domain_dirs: use a quoted domain such as "youtube.com", not a URL, port or wildcard: {domain!r}')
            if not isinstance(destination, str) or not destination.strip() or '\x00' in destination:
                raise ValueError(f'domain_dirs[{domain!r}] must be a non-empty folder path string; quote dotted domain keys')
            normalized = domain.lower().rstrip('.')
            if normalized in routes:
                raise ValueError(f'domain_dirs contains duplicate rules for {normalized!r}')
            routes[normalized] = expand(destination)
        config.domain_dirs = routes
        if not isinstance(config.extra_args, list) or not all(isinstance(x, str) for x in config.extra_args):
            raise ValueError('extra_args must be an array of strings')
        return config
