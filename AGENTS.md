# Repository Guidelines

## Project Structure & Module Organization

Yank is a Python 3.11+ terminal download manager. Application code lives in `src/yank/`: `cli.py` handles commands, `ui.py` implements the Textual interface, `engine.py` manages jobs and processes, and `backend.py`, `config.py`, and `model.py` support downloading, configuration, and persistence. Tests are in `tests/`, organized as `test_<feature>.py`. User-facing setup and behavior are documented in `README.md`.

## Build, Test, and Development Commands

- `python -m pip install -e '.[test]'` installs the package in editable mode with test dependencies.
- `python -m yank` or `yank` starts the terminal interface; `yank --help` lists CLI commands.
- `yank doctor` checks external downloader and runtime availability.
- `python -m pytest` runs the complete test suite; `python -m pytest tests/test_engine.py` runs one module.

For real downloads, install yt-dlp and FFmpeg/ffprobe; aria2 is recommended. Clipboard support requires an appropriate Wayland or X11 utility.

## Coding Style & Naming Conventions

Follow the existing Python style: four-space indentation, snake_case for modules, functions, and variables, and PascalCase for classes. Keep imports and type annotations consistent with surrounding code. No formatter or linter is configured in `pyproject.toml`; avoid unrelated formatting changes.

## Testing Guidelines

Tests use pytest and pytest-asyncio, with async mode configured automatically. Name files `test_*.py` and test functions `test_*`. Use temporary paths and controlled worker fixtures when testing state or downloader behavior; avoid relying on real downloads or personal configuration. Add regression tests for behavior changes and run the relevant module plus the full suite when practical. No coverage threshold is configured.

## Commit & Pull Request Guidelines

The available history contains one subject, `[verified] Initial Yank terminal download manager`; it does not establish a broader commit convention. Use concise, descriptive commit subjects and explain the change and verification in pull requests. Link relevant issues and include terminal screenshots when changing visible UI behavior.

## Security & Configuration

Configuration and state use XDG directories by default. Never commit cookies, credentials, signed URLs, local databases, or download artifacts. Preserve the documented behavior that removing queue entries does not delete downloaded or partial files.
