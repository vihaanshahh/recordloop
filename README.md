# RecordLoop

Record browser interactions, generate test code, and capture video — for any frontend framework.

Works with **React, Vue, Next.js, Vite, Angular, Svelte, Gatsby, Remix, Astro**, and any app running on localhost.

## Setup (30 seconds)

```bash
pip install playwright pytest watchdog
playwright install chromium
```

Then in your project directory:

```bash
python -m recordloop init
```

This will:
- Auto-detect your frontend framework from `package.json`
- Set the correct dev server port (3000, 5173, 4200, etc.)
- Generate a `.env` file with your config

### Environment Variables

All config reads from `RECORDLOOP_*` env vars or a `.env` file:

| Variable | Default | Description |
|---|---|---|
| `RECORDLOOP_FRAMEWORK` | (auto-detected) | react, vue, next, vite, angular, svelte, etc. |
| `RECORDLOOP_BASE_URL` | `http://localhost:3000` | Your dev server URL |
| `RECORDLOOP_PORT` | `3000` | Dev server port (used if BASE_URL not set) |
| `RECORDLOOP_VIDEO_DIR` | `test-videos` | Where videos are saved |
| `RECORDLOOP_TEST_OUTPUT_DIR` | `generated-tests` | Where test code is saved |
| `RECORDLOOP_HEADLESS` | `true` | Run browser headlessly |
| `RECORDLOOP_SLOW_MO` | `0` | Slow down actions (ms) |
| `RECORDLOOP_VIEWPORT_WIDTH` | `1280` | Browser viewport width |
| `RECORDLOOP_VIEWPORT_HEIGHT` | `720` | Browser viewport height |

See `.env.example` for a template.

## Quick Start

### With env-aware config (recommended)

```python
from recordloop import PlaywrightRecorder, RecordLoopConfig

config = RecordLoopConfig()  # reads .env + auto-detects framework

with PlaywrightRecorder(config.to_recorder_config()) as recorder:
    page = recorder.start_recording(config.base_url)

    page.fill("#search", "Playwright")
    page.click("#search-button")

    recorder.stop_recording()
    code = recorder.generate_test_code(test_name="test_search")
    print(code)
```

### With explicit config

```python
from recordloop import PlaywrightRecorder, RecorderConfig

config = RecorderConfig(base_url="http://localhost:5173", headless=False)

with PlaywrightRecorder(config) as recorder:
    page = recorder.start_recording("http://localhost:5173")
    page.click("#my-button")
    recorder.stop_recording()
    print(recorder.generate_test_code())
```

### Using Decorators

```python
from recordloop import recordable, video_capture

@recordable()
def test_login(page):
    page.goto("http://localhost:3000/login")
    page.fill("#username", "user@example.com")
    page.click("#login")

@video_capture(output_dir="videos")
def test_checkout(page):
    page.goto("http://localhost:3000/checkout")
    # ... test steps ...
```

### Watch Mode

```python
from recordloop import TestRunner

runner = TestRunner()
runner.watch_mode(
    watch_paths=["src/", "tests/"],
    test_files=["tests/test_integration.py"]
)
```

## Visualize Recordings

Generate an HTML report with action timelines, test code previews, and video playback:

```bash
python -m recordloop report
```

Opens `recordloop-report.html` — a single-file dashboard showing all your recordings.

## CLI Commands

```bash
python -m recordloop init              # Detect framework, generate .env
python -m recordloop report            # Generate HTML report
python -m recordloop config            # Print current config
```

## Framework Auto-Detection

RecordLoop reads your `package.json` and picks the right defaults:

| Framework | Default Port | Detected By |
|---|---|---|
| React (CRA) | 3000 | `react` in deps |
| React + Vite | 5173 | `react` + `vite` in deps |
| Next.js | 3000 | `next` in deps |
| Vue + Vite | 5173 | `vue` + `vite` in deps |
| Nuxt | 3000 | `nuxt` in deps |
| Angular | 4200 | `@angular/core` in deps |
| Svelte | 5173 | `svelte` in deps |
| Gatsby | 8000 | `gatsby` in deps |
| Remix | 3000 | `@remix-run/react` in deps |
| Astro | 4321 | `astro` in deps |

## API Reference

### RecordLoopConfig

Env-aware config that auto-detects your framework:

```python
config = RecordLoopConfig()             # auto-detect everything
config = RecordLoopConfig(headless=False)  # override specific fields
config.summary()                        # print human-readable config
config.to_recorder_config()             # convert to RecorderConfig
```

### PlaywrightRecorder

Core class for recording actions and generating test code.

```python
with PlaywrightRecorder(config) as recorder:
    recorder.start_recording(url)
    recorder.record_action(type, selector, value)
    recorder.stop_recording()
    recorder.generate_test_code()
    recorder.save_test_code()
```

### Decorators

- `@recordable()` — Auto-record actions and generate test code
- `@video_capture()` — Capture video of test execution
- `@watch_changes(paths)` — Auto-run test when files change

### TestRunner

```python
runner = TestRunner()
runner.run_tests(test_files, video_enabled=True)
runner.watch_mode(watch_paths, test_files)
```

### Report

```python
from recordloop import generate_report
generate_report()  # creates recordloop-report.html
```

## Action Types

`NAVIGATE`, `CLICK`, `DOUBLE_CLICK`, `TYPE`, `PRESS`, `SELECT`, `CHECK`, `UNCHECK`, `HOVER`, `SCREENSHOT`, `WAIT_FOR_SELECTOR`, `WAIT_FOR_NAVIGATION`, `WAIT_FOR_TIMEOUT`
