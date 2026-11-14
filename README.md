# Yank

A persistent, mouse-friendly terminal download manager powered by **yt-dlp**, **aria2c**, and **FFmpeg**. Linux, Python 3.11+.

## Run

```sh
yank
```

Paste a video URL in the box at the bottom and press **Enter**. Multiple whitespace-separated links can be pasted together. Downloads begin immediately, up to the configured concurrency limit. Click an entry or use the arrow keys to select it. Each entry is itself a progress bar: its background fills behind its title and transfer information.

The centered block-letter header sits above the download queue. Small terminals automatically get a compact header. Queue entries are one line each, with the title followed by a divider and transfer details. The duplicate toolbar is gone: the single-line footer is the action bar. Add, Paste, and dialog Close buttons are all one terminal row high, as is the URL field.

Yank uses your terminal’s ANSI palette and default background, including its transparency and blur settings. Yank inherits your terminal font; it has no independent pixel/point size. Change the font in your terminal profile if you want smaller or larger text.

## Controls

| Key | Action |
| --- | --- |
| Up / Down | Select a queue entry; Up from the URL field enters the queue |
| p / Space | Pause / resume |
| r | Retry immediately / resume a cancelled job |
| c | Cancel the download, retaining its entry and partial files |
| d / Delete | Stop and remove **only the queue entry** |
| Shift+D | Stop downloads and clear the entire queue; **keep all files** |
| Enter / l | Details, error explanation, live downloader log |
| o | Open a completed file in the default application |
| n (also i / /) | Focus the URL field |
| Esc | Focus the queue / close a dialog |
| ? | Help |
| q | Quit safely |

**q**, **n**, **?**, and **Shift+D** work from the queue or an empty URL field. The footer shows lowercase shortcut letters with two spaces between groups; Shift+D is deliberately not added as another footer button. While editing a URL, ordinary letters and `?` stay literal so shortcuts do not corrupt links. Ctrl+Q/Ctrl+N and F1 remain optional hidden fallbacks. The footer stays mouse-clickable even while editing; its buttons invoke commands directly rather than inserting shortcut letters. Enter opens details when the list is focused; a single mouse click only selects a row.

**Paste** sits immediately below **+ Add**. One click pastes clipboard text and immediately submits it, starting downloads without a second Add click (multiple lines become space-separated URLs). **+ Add** and Enter still submit manually entered URLs. Paste does not modify the clipboard. Clipboard access uses `wl-paste` from **wl-clipboard** on Wayland, or `xclip`/`xsel` on X11. If access is unavailable or the clipboard contains only an image, Yank reports that rather than pasting garbage; Ctrl+Shift+V still works through the terminal.

**Pause** stops the process tree and retains partial data. Resume starts fresh extraction and continues compatible partials, rather than relying on expired CDN links. **Cancel** also stops downloading but remains cancelled across restarts. **Remove** never deletes finished or partial files. **Shift+D** immediately removes every queue entry (including active, paused and finished ones), stopping active downloads without deleting media, partials, logs or archives. Repeated `d` keypresses safely advance to the next live entry even before the screen redraws. You can delete unwanted files yourself in your file manager.

Quitting stops downloads; active jobs are saved as queued and resume next launch. Paused and cancelled jobs stay that way. To download without the TUI, use `yank serve` instead.

## Configuration

Created on first launch:

```text
~/.config/yank/config.toml
```

Or `$XDG_CONFIG_HOME/yank/config.toml` when set. Edit it with:

```sh
yank config --edit
```

Important settings:

```toml
download_dir = "~/Downloads/yank"
concurrent_downloads = 3
aria2_connections = 4
retry_initial = 5
retry_max = 300
stall_timeout = 180
socket_timeout = 30
inherit_ytdlp_config = true
format = "bv*+ba/b"
merge_output_format = "mkv"
```

Changes take effect after restarting Yank. Existing jobs retain the destination chosen when they were added; a new destination applies to new jobs. The default format selects the best available video and audio, merging when needed. `merge_output_format` controls merges; already-combined files retain their original container.

### Per-domain download folders

The generated config already contains a `[domain_dirs]` table with commented examples. **Edit/uncomment that existing table** rather than adding a second declaration. For an older config without the table, add it at the **end** of `config.toml`, after all general settings:

```toml
[domain_dirs]
"youtube.com" = "~/Downloads/youtube"
"youtu.be" = "~/Downloads/youtube"
"website.com" = "~/Videos/website"
```

- Domain names must be quoted and must not include `https://`, a port, a path, or `*`.
- A rule matches that domain and its subdomains: `youtube.com` also covers `www.youtube.com`, `m.youtube.com`, and `music.youtube.com`.
- The most specific matching domain wins, so you can give `music.youtube.com` a separate folder.
- Matching is case-insensitive and uses domain boundaries; `notyoutube.com` does not match `youtube.com`.
- Short-link domains such as `youtu.be` need their own entry. Routing uses the original submitted URL's hostname, not a redirect target, playlist member, or CDN hostname.
- Unmatched sites use the existing `download_dir` fallback. Paths support `~` and environment variables, just like `download_dir`.
- A destination is saved when an entry is first added. Changing these rules does not move existing files or change queued entries; remove/re-add an entry if you want it to use a new rule.

Restart Yank after editing the configuration.

Yank inherits normal yt-dlp configuration by default, including existing browser cookies, JS challenge helpers, and extractor settings. It **does not edit** `~/.config/yt-dlp/config`. It overrides the output paths, filename, transport, progress, archive, retry and execution-hook flags needed to manage jobs. Avoid global simulation/filtering/skip-download options: these can prevent downloads. Use `inherit_ytdlp_config = false` for a clean configuration.

Explicit per-app overrides are available:

```toml
# Empty values inherit yt-dlp settings rather than clearing them.
cookies_from_browser = ""
cookies_file = ""
proxy = ""
impersonate = ""
ytdlp = "yt-dlp"
extra_args = []
```

For example, `cookies_from_browser = "firefox"` uses your own Firefox session; a custom profile can use yt-dlp's `firefox:/path/to/profile` syntax. An exported Netscape cookie file can be configured instead. Do not publish cookies, signed URLs, or proxy credentials. Cookie files must remain readable by your user. Impersonation is opt-in and requires `curl_cffi` in **yt-dlp's** environment; it is not enabled or installed automatically.

## Recovery behavior and limits

* Failed jobs retry **indefinitely** until paused, cancelled, or removed.
* Backoff grows from 5 seconds to a maximum of 300 seconds by default; waiting jobs release their concurrency slot.
* Each attempt runs a fresh yt-dlp extraction to refresh expired signatures and CDN URLs.
* Failed HTTP transfers alternate between aria2c and native yt-dlp. HLS/DASH use native fragment handling; live streams may use FFmpeg as selected by yt-dlp.
* Later attempts add a request delay, and alternate attempts try IPv4.
* HTTP/fragment/extractor retries are bounded within each process so the manager can periodically start fresh extraction. Missing fragments are treated as errors, not silently skipped.
* A no-output watchdog restarts stalled processes. Set `stall_timeout = 0` to disable it, for example for very long silent postprocessing.
* Native and aria2 partial files are kept separately. Sparse aria2 partials are **not** incorrectly resumed by the native HTTP downloader. Switching transport can therefore start that component again.
* Each job has a persistent yt-dlp archive and an output-path manifest so retries of playlist-only URLs skip already-completed entries **only while their final files still exist**. Missing or unverified archive entries are backed up and removed from the active archive before re-extraction, allowing missing media to download again. A playlist-only URL is **one queue entry**, with title/progress tracking its current video. A video URL containing a playlist parameter downloads only that video.
* Known video/audio sizes are combined for the progress fill. Unknown lengths are shown honestly as unknown rather than inventing a percentage. Finalization stays below 100% until yt-dlp successfully finishes.

**There is no universal unblock or download guarantee.** yt-dlp only supports what its current extractors and the site's access rules allow. Removed videos, DRM, login requirements, CAPTCHA, geo-restrictions, site changes, and persistent server blocks may need intervention or may be impossible. Yank displays the real error and an actionable hint; it never disables TLS verification, bypasses DRM, or claims that retries can fix everything. Cancel permanently unavailable entries to stop retries.

Keep yt-dlp current through the way you installed it. For a pipx installation:

```sh
pipx upgrade yt-dlp
# If a recent site change requires a nightly release:
pipx upgrade yt-dlp --pip-args='--pre'
```

YouTube may additionally require yt-dlp's EJS helper package/remote components and a supported JS runtime (Deno recommended; Node is also enabled when found). See the [yt-dlp EJS documentation](https://github.com/yt-dlp/yt-dlp/wiki/EJS).

## CLI / automation

All queue actions have an equivalent headless path. Changes are sent to the running app through a same-user Unix socket; when it is closed, queue changes are saved for the next launch. No daemon starts implicitly from `add`.

```sh
yank add 'https://example.com/video' 'https://example.org/another'
yank state                  # JSON state, including stable entry IDs
yank list                   # readable summary
yank pause ENTRY_ID
yank resume ENTRY_ID
yank retry ENTRY_ID
yank cancel ENTRY_ID
yank remove ENTRY_ID        # never deletes media
yank clear                  # stop/remove ALL queue entries; keep files
yank logs ENTRY_ID --lines 80
yank serve                  # engine without a TUI; Ctrl+C stops safely
yank serve --until-done     # exits when nothing remains queued/running/retrying
yank stop                   # stop a running engine/TUI
yank doctor                 # tool paths and versions
yank --config /path/to/config.toml tui
```

Only one engine can own a state directory. Running `yank` twice gives a useful error; use the CLI to control the existing instance. Multiple configuration files share the same queue unless you also select a different `XDG_STATE_HOME`.

Queue, logs, and archives live under `~/.local/state/yank` (or `$XDG_STATE_HOME/yank`). Logs are capped per active attempt and rotated, but old jobs' logs are retained for diagnosis. Queue removal preserves those logs and archives too. Per-job working files live in `<download_dir>/.yank/<entry-id>/<transport>/`; final videos land directly in `download_dir`. Nothing is written into your media directory until a download starts.

## Installation

Install native `aria2`, `ffmpeg`/`ffprobe`, and `yt-dlp`. Yank uses these external executables; it does not silently install or update downloaders. Without aria2 it falls back to native yt-dlp. Native binaries are strongly recommended: container wrappers can outlive their launcher and defeat process-group pause/cancel.

### Install

Prerequisites: Python 3.11+, Git, pipx, yt-dlp, and FFmpeg/ffprobe. aria2 is recommended; wl-clipboard (Wayland) or xclip/xsel (X11) enables the Paste button. Keep these external tools on your PATH. The repository is private, so cloning requires GitHub access and a configured SSH key.

```sh
mkdir -p "$HOME/.local/src"
git clone git@github.com:get-monochromed/yank.git "$HOME/.local/src/yank"
pipx install --editable "$HOME/.local/src/yank"
pipx ensurepath
yank --help
```

If `yank` is not found after `ensurepath`, open a new terminal. The command is exposed in pipx's binary directory (normally `~/.local/bin`); its isolated environment is normally `~/.local/share/pipx/venvs/yank-tui`. No service or daemon is installed. Config and queue state are created on first use at the XDG locations documented above; downloads go to the configured destinations.

### Upgrade

Quit Yank first, then update the checkout and refresh the editable installation:

```sh
git -C "$HOME/.local/src/yank" pull --ff-only
pipx install --force --editable "$HOME/.local/src/yank"
```

Reopen Yank to use the updated code. Existing config, queue entries and downloaded files are retained. If your checkout is elsewhere, substitute its path in these commands.

### Uninstall

```sh
pipx uninstall yank-tui
```

This removes the command and its pipx environment. It deliberately preserves the source checkout, `~/.config/yank`, `~/.local/state/yank`, partials and downloaded videos. Remove those separately only if you no longer need them.

## Tests

From an installed checkout, inject the test tools into the same isolated environment and run the suite:

```sh
pipx inject yank-tui pytest pytest-asyncio
cd "$HOME/.local/src/yank"
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ~/.local/share/pipx/venvs/yank-tui/bin/python -m pytest -p pytest_asyncio.plugin -q
```

The suite includes transactional persistence, controls, JSON CLI/IPC, cancellation and retries with a controlled executable, Textual mouse/keyboard tests, and actual yt-dlp/aria2/FFmpeg downloads from a local HTTP server. Local media are generated by FFmpeg. Integration tests verify playable output, byte equality for a direct MP4, HLS, rejection/retry recovery, live progress, and real aria2 pause/resume without orphan downloaders. Integration tests skip if their external dependencies are absent.
