#!/usr/bin/env python3
"""
RecordLoop end-to-end demo.

Starts a local web server, records a Playwright session against the
demo app, saves video + test code, and prints a summary.

Usage:
    python demo/run_demo.py
    python demo/run_demo.py --headed      # watch the browser
    python demo/run_demo.py --port 7777   # custom port
"""

import argparse
import sys
import time
import threading
import http.server
from pathlib import Path

# Ensure the repo root is importable when running as a script
sys.path.insert(0, str(Path(__file__).parent.parent))

from recordloop.recorder import PlaywrightRecorder, RecorderConfig, ActionType


def _serve_html(port: int, html_path: Path):
    """Serve the demo HTML on a background thread."""
    class _Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(html_path.parent), **kwargs)
        def log_message(self, *_):
            pass  # silence request logs

    server = http.server.HTTPServer(("127.0.0.1", port), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def run_demo(headed: bool = False, port: int = 7777):
    base_url = f"http://localhost:{port}/app.html"
    html_path = Path(__file__).parent / "app.html"
    out_dir = Path(__file__).parent / "output"
    out_dir.mkdir(exist_ok=True)

    print()
    print("=" * 56)
    print("  RecordLoop Demo")
    print("=" * 56)
    print()

    # Start local server
    server = _serve_html(port, html_path)
    print(f"  Serving demo app at {base_url}")
    time.sleep(0.3)  # let the server bind

    config = RecorderConfig(
        base_url=base_url,
        video_dir=str(out_dir),
        test_output_dir=str(out_dir),
        headless=not headed,
        slow_mo=80 if headed else 0,
    )

    print("  Launching Playwright (Chromium) …")
    print()

    with PlaywrightRecorder(config) as recorder:
        page = recorder.start_recording(base_url)
        page.wait_for_load_state("domcontentloaded")

        # ── Counter ────────────────────────────────────────────
        print("  [1/3] Testing counter …")
        page.wait_for_selector("#increment-btn")

        recorder.record_click("#increment-btn")
        page.click("#increment-btn")
        time.sleep(0.15)

        recorder.record_click("#increment-btn")
        page.click("#increment-btn")
        time.sleep(0.15)

        recorder.record_click("#increment-btn")
        page.click("#increment-btn")
        time.sleep(0.15)

        recorder.record_click("#decrement-btn")
        page.click("#decrement-btn")
        time.sleep(0.15)

        counter_val = page.text_content("#counter-value")
        print(f"     Counter value: {counter_val}  ✓")

        # ── Todo list ──────────────────────────────────────────
        print("  [2/3] Adding tasks …")

        for task in ["Write more tests", "Ship the demo"]:
            recorder.record_type("#todo-input", task)
            page.fill("#todo-input", task)
            time.sleep(0.1)
            recorder.record_click("#add-todo-btn")
            page.click("#add-todo-btn")
            time.sleep(0.2)

        print(f"     Added 2 tasks  ✓")

        # ── Contact form ───────────────────────────────────────
        print("  [3/3] Filling contact form …")

        recorder.record_type("#name-input", "Ada Lovelace")
        page.fill("#name-input", "Ada Lovelace")
        time.sleep(0.1)

        recorder.record_type("#email-input", "ada@recordloop.dev")
        page.fill("#email-input", "ada@recordloop.dev")
        time.sleep(0.1)

        recorder.record_action(ActionType.SELECT, "#topic-select", "feature")
        page.select_option("#topic-select", "feature")
        time.sleep(0.1)

        recorder.record_type("#message-input", "RecordLoop is exactly what we needed for PR reviews.")
        page.fill("#message-input", "RecordLoop is exactly what we needed for PR reviews.")
        time.sleep(0.15)

        recorder.record_click("#submit-form-btn")
        page.click("#submit-form-btn")
        time.sleep(0.3)

        success = page.is_visible("#success-message")
        print(f"     Form submitted, success banner visible: {success}  ✓")

        # ── Wrap up ────────────────────────────────────────────
        actions = recorder.stop_recording()
        video_path = recorder.get_video_path()

        test_path = recorder.save_test_code(
            filepath=str(out_dir / "test_demo.py"),
            test_name="test_recordloop_demo",
        )

    server.shutdown()

    print()
    print("=" * 56)
    print("  Done!")
    print("=" * 56)
    print()
    print(f"  Actions recorded : {len(actions)}")
    if video_path:
        print(f"  Video            : {video_path}")
    else:
        print("  Video            : (install ffmpeg for MP4 conversion)")
    print(f"  Generated test   : {test_path}")
    print()
    print("  Generated Playwright test code:")
    print("  " + "-" * 50)
    code = Path(test_path).read_text()
    for line in code.splitlines()[:40]:
        print("  " + line)
    if code.count("\n") > 40:
        print("  … (truncated — see full file above)")
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RecordLoop end-to-end demo")
    parser.add_argument("--headed", action="store_true", help="Show the browser window")
    parser.add_argument("--port", type=int, default=7777, help="Local server port")
    args = parser.parse_args()
    run_demo(headed=args.headed, port=args.port)
