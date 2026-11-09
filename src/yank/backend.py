"""yt-dlp command construction and native/aria2 progress decoding."""
from __future__ import annotations

import json
import math
from pathlib import Path
import re
import shutil

from .config import Config
from .model import Job

PROGRESS = ('download:YANK_PROGRESS:{"downloaded":%(progress.downloaded_bytes|null)j,'
            '"total":%(progress.total_bytes|null)j,"estimate":%(progress.total_bytes_estimate|null)j,'
            '"speed":%(progress.speed|null)j,"eta":%(progress.eta|null)j,'
            '"status":%(progress.status)j,"format_id":%(info.format_id)j}')
META = ('before_dl:YANK_META:{"title":%(title)j,"id":%(id)j,"format_id":%(format_id)j,'
        '"formats":%(requested_formats.:.{format_id,filesize,filesize_approx})j}')
ARIA = re.compile(r'\[#\w+\s+([\d.]+[KMGTPE]?i?B?)/([\d.]+[KMGTPE]?i?B?)\([^)]*\).*?DL:([\d.]+[KMGTPE]?i?B?)')


def build_command(config: Config, job: Job) -> list[str]:
    work = Path(job.destination) / '.yank' / job.id / job.backend
    archive = config.state_dir / 'archives' / f'{job.id}.txt'
    args = [config.ytdlp]
    if not config.inherit_ytdlp_config:
        args += ['--ignore-config']
    args += config.extra_args
    for option, value in [('cookies-from-browser', config.cookies_from_browser),
                          ('cookies', config.cookies_file), ('proxy', config.proxy),
                          ('impersonate', config.impersonate)]:
        if value:
            args += [f'--{option}', value]
    args += ['--no-simulate', '--no-ignore-errors', '--no-flat-playlist',
             '--no-playlist', '--no-quiet', '--progress', '--newline', '--color', 'never',
             '--progress-delta', '0.3', '--continue', '--no-overwrites', '--no-break-on-existing',
             '--download-archive', str(archive), '--no-exec',
             '--retries', '3', '--fragment-retries', '3', '--extractor-retries', '2',
             '--retry-sleep', 'http:exp=1:8', '--retry-sleep', 'fragment:exp=1:8',
             '--socket-timeout', str(config.socket_timeout), '--abort-on-unavailable-fragments',
             '--concurrent-fragments', '2', '--format', config.format,
             '--merge-output-format', config.merge_output_format,
             '--paths', str(Path(job.destination)), '--paths', f'temp:{work}',
             '--output', '%(title).180B [%(id)s].%(ext)s',
             '--print', META, '--print', 'after_move:YANK_DONE:%(.{filepath,id,extractor_key})j',
             '--progress-template', PROGRESS,
             '--progress-template', 'postprocess:YANK_PROCESS:%(progress.status)j',
             '--downloader', 'native', '--downloader', 'dash,m3u8:native']
    if job.backend == 'aria2c' and shutil.which('aria2c'):
        n = config.aria2_connections
        aria = (f'aria2c:-x{n} -s{n} -j{n} -k1M --file-allocation=none '
                '--summary-interval=1 --console-log-level=notice --enable-color=false '
                '--human-readable=false --show-console-readout=true --quiet=false '
                f'--max-tries=3 --retry-wait=3 --connect-timeout=15 --timeout={int(config.socket_timeout) or 1}')
        args += ['--downloader', 'http:aria2c', '--downloader-args', aria]
    # Node is a supported fallback when Deno is not installed. Never weaken TLS.
    if shutil.which('node'):
        args += ['--js-runtimes', 'node']
    # Fresh extraction on every invocation refreshes expired CDN/signature URLs.
    if job.attempt > 1:
        args += ['--sleep-requests', '1']
        if job.attempt % 2 == 0:
            args += ['--force-ipv4']
    return [*args, '--', job.url]


def number(value) -> float:
    try:
        result = float(value)
        return max(0, result) if math.isfinite(result) else 0
    except (TypeError, ValueError):
        return 0


def byte_value(value: str) -> float:
    match = re.fullmatch(r'([\d.]+)([KMGTPE]?)(i?B?)', value)
    if not match:
        return 0
    n, unit, suffix = match.groups()
    return float(n) * ((1024 if 'i' in suffix else 1000) ** (' KMGTPE'.index(unit) if unit else 0))


def aggregate(job: Job):
    job.downloaded = int(sum(item.get('downloaded', 0) for item in job.components.values()))
    totals = [item.get('total', 0) for item in job.components.values()]
    job.total = int(sum(totals)) if totals and all(totals) else 0


def parse_line(job: Job, line: str) -> bool:
    """Return whether a structured state/progress event was consumed."""
    try:
        if line.startswith('YANK_META:'):
            data = json.loads(line.removeprefix('YANK_META:'))
            if str(data.get('id', '')) != job.video_id:
                job.components = {}
            job.video_id = str(data.get('id', ''))
            job.title = str(data.get('title') or job.title)
            formats = data.get('formats')
            job.expected_formats = formats if isinstance(formats, list) else []
            for fmt in job.expected_formats:
                job.components.setdefault(str(fmt['format_id']), {'downloaded': 0, 'total': number(fmt.get('filesize') or fmt.get('filesize_approx'))})
            job.format_id = str(job.expected_formats[0]['format_id']) if job.expected_formats else str(data.get('format_id') or 'default')
            aggregate(job)
        elif line.startswith('YANK_PROGRESS:'):
            data = json.loads(line.removeprefix('YANK_PROGRESS:'))
            key = str(data.get('format_id') or job.format_id or 'default')
            if not job.expected_formats and key not in job.components and len(job.components) == 1:
                job.components[key] = job.components.pop(next(iter(job.components)))
            item = job.components.setdefault(key, {'downloaded': 0, 'total': 0})
            downloaded = number(data.get('downloaded'))
            total = number(data.get('total') or data.get('estimate'))
            if downloaded:
                item['downloaded'] = downloaded
            if total:
                item['total'] = total
            if data.get('status') == 'finished':
                item['total'] = item['downloaded'] or item['total']
                item['finished'] = True
                remaining = [str(f['format_id']) for f in job.expected_formats
                             if not job.components.get(str(f['format_id']), {}).get('finished', False)]
                job.format_id = remaining[0] if remaining else key
                job.status = 'processing' if not remaining else 'downloading'
            else:
                job.format_id = key
                job.status = 'downloading'
            job.speed = number(data.get('speed'))
            job.eta = number(data['eta']) if isinstance(data.get('eta'), (int, float)) else None
            aggregate(job)
        elif line.startswith('YANK_DONE:'):
            data = json.loads(line.removeprefix('YANK_DONE:'))
            if isinstance(data, dict):
                job.filename = str(data['filepath'])
                archive_id = f"{str(data['extractor_key']).lower()} {data['id']}"
            elif isinstance(data, str):
                # Retain support for the original path-only event format.
                job.filename = data
                archive_id = f'generic {job.video_id or data}'
            else:
                return False
            job.outputs[archive_id] = job.filename
            job.status = 'processing'  # Only exit code zero marks the whole job done.
        elif line.startswith('YANK_PROCESS:') or line.startswith(('[Merger]', '[Fixup', '[VideoRemuxer]')):
            job.status = 'processing'
            job.speed = 0
        elif match := ARIA.search(line):
            downloaded, total, speed = map(byte_value, match.groups())
            key = job.format_id or 'default'
            job.components[key] = {'downloaded': downloaded, 'total': total}
            aggregate(job)
            job.speed = speed
            job.eta = (total - downloaded) / speed if speed else None
            job.status = 'downloading'
        else:
            return False
    except (ValueError, TypeError, KeyError, AttributeError):
        return False
    return True


def retry_delay(config: Config, attempt: int) -> float:
    return min(config.retry_max, config.retry_initial * 2 ** min(max(0, attempt - 1), 30))


def error_hint(message: str) -> str:
    text = message.lower()
    if 'drm' in text:
        return 'DRM-protected media cannot be downloaded by yt-dlp. Cancel this entry.'
    if any(word in text for word in ('403', '401', 'login', 'sign in', 'captcha', 'cloudflare', 'bot', 'rejected')):
        return 'May require fresh browser cookies, a browser challenge, or authorized access; see config.'
    if any(word in text for word in ('signat', 'nsig', 'javascript', 'challenge solver')):
        return 'Update yt-dlp and its EJS helpers; ensure Deno or Node is available.'
    if '429' in text or 'too many requests' in text:
        return 'Rate limited. Backoff is automatic; lower concurrent_downloads/aria2_connections.'
    if 'unsupported url' in text or '404' in text or 'not found' in text:
        return 'Check the URL and yt-dlp support/version. Retries cannot restore deleted media.'
    if any(word in text for word in ('disk', 'space', 'permission denied')):
        return 'Check destination permissions and free disk space.'
    return 'Will re-extract the page and retry with capped backoff; inspect the log for details.'
