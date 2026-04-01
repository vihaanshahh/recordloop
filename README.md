# Playwright Recorder

A lightweight Playwright abstraction layer for Python with test recording, video capture, and auto-reload on code changes.

## Features

- **Test Recording**: Capture browser actions and generate reusable test code
- **Video Recording**: Automatically capture videos of test runs
- **Watch Mode**: Auto-run tests when source files change
- **Simple API**: Intuitive context managers and decorators
- **Code Generation**: Convert recorded actions to Playwright test code

## Installation

```bash
pip install playwright pytest watchdog
playwright install chromium
```

## Quick Start

### Basic Recording

```python
from playwright_recorder import PlaywrightRecorder, RecorderConfig

with PlaywrightRecorder() as recorder:
    page = recorder.start_recording("https://example.com")
    
    # Interact with page
    page.fill("#search", "Playwright")
    page.click("#search-button")
    
    # Stop and generate test code
    recorder.stop_recording()
    code = recorder.generate_test_code(test_name="test_search")
    print(code)
```

### Using Decorators

```python
from playwright_recorder import recordable, video_capture

@recordable()
def test_login(page):
    page.goto("https://example.com/login")
    page.fill("#username", "user@example.com")
    page.click("#login")

@video_capture(output_dir="videos")
def test_checkout(page):
    page.goto("https://example.com/checkout")
    # ... test steps ...
```

### Watch Mode

```python
from playwright_recorder import TestRunner

runner = TestRunner()
runner.watch_mode(
    watch_paths=["src/", "tests/"],
    test_files=["tests/test_integration.py"]
)
```

## API Reference

### PlaywrightRecorder

Core class for recording actions and generating test code.

```python
with PlaywrightRecorder(config) as recorder:
    recorder.start_recording(url)  # Start recording, optionally navigate
    recorder.record_action(type, selector, value)  # Record manual action
    recorder.stop_recording()  # Stop recording
    recorder.generate_test_code()  # Generate Python test code
    recorder.save_test_code()  # Save generated code to file
```

### Decorators

- `@recordable()` - Auto-record actions and generate test code
- `@video_capture()` - Capture video of test execution
- `@watch_changes(paths)` - Auto-run test when files change

### TestRunner

Run tests with video capture and watch mode.

```python
runner = TestRunner()
runner.run_tests(test_files, video_enabled=True)
runner.watch_mode(watch_paths, test_files)
```

## File Structure

```
playwright-recorder/
├── recorder.py       # Core recorder class
├── test_runner.py    # Test runner with video & watch
├── decorators.py     # @recordable, @video_capture, etc.
├── __init__.py       # Public API
├── example.py        # Usage examples
└── README.md         # This file
```

## Action Types

Supported action types for manual recording:

- `NAVIGATE` - Page navigation
- `CLICK` / `DOUBLE_CLICK` - Click actions
- `TYPE` - Text input
- `PRESS` - Keyboard press
- `SELECT` - Dropdown selection
- `CHECK` / `UNCHECK` - Checkbox/radio
- `HOVER` - Mouse hover
- `SCREENSHOT` - Capture screenshot
- `WAIT_FOR_SELECTOR` - Wait for element
- `WAIT_FOR_NAVIGATION` - Wait for page load
- `WAIT_FOR_TIMEOUT` - Wait duration
