"""
Bridge between the JS SDK and the Python recorder.

Runs a lightweight HTTP server that:
1. Receives session data POSTed from the JS SDK running in the browser
2. Converts JS actions → RecordedAction objects
3. Replays them with Playwright (video + test code generation)

Usage:
    python -m recordloop serve              # start on port 8787
    python -m recordloop serve --port 9000  # custom port

Or use programmatically:
    from recordloop.bridge import convert_session, replay_session
    actions = convert_session(js_session_dict)
"""

import json
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Optional

from .recorder import RecorderConfig, RecordedAction, ActionType, PlaywrightRecorder


# Map JS action types → Python ActionType
ACTION_MAP = {
    "navigate": ActionType.NAVIGATE,
    "click": ActionType.CLICK,
    "double_click": ActionType.DOUBLE_CLICK,
    "type": ActionType.TYPE,
    "select": ActionType.SELECT,
    "check": ActionType.CHECK,
    "uncheck": ActionType.UNCHECK,
    "scroll": ActionType.SCROLL,
    "hover": ActionType.HOVER,
}


def convert_session(session: dict) -> list[RecordedAction]:
    """
    Convert a JS SDK session into a list of RecordedActions.

    Args:
        session: The session dict from the JS SDK (RecordLoop.getSession())

    Returns:
        List of RecordedAction objects ready for Playwright replay
    """
    actions = []
    for i, js_action in enumerate(session.get("actions", [])):
        action_type_str = js_action.get("type", "")
        action_type = ACTION_MAP.get(action_type_str)
        if action_type is None:
            continue

        selector = js_action.get("selector")
        value = js_action.get("value")
        timestamp = js_action.get("timestamp", 0)

        # Build options from extra fields
        options = {}
        if "position" in js_action:
            options["position"] = js_action["position"]

        actions.append(RecordedAction(
            id=f"js_{i:04d}",
            timestamp=timestamp,
            action_type=action_type,
            selector=selector,
            value=value,
            options=options,
            page_url=session.get("url"),
        ))

    return actions


def replay_session(
    session: dict,
    config: Optional[RecorderConfig] = None,
    video: bool = True,
    generate_test: bool = True,
    output_dir: str = "generated-tests",
) -> dict:
    """
    Replay a JS SDK session with Playwright.

    Converts the session, opens a browser, replays every action,
    captures video, and generates test code.

    Args:
        session: JS SDK session dict
        config: Optional recorder config
        video: Whether to capture video
        generate_test: Whether to generate test code
        output_dir: Where to save generated files

    Returns:
        Dict with paths to generated files:
        { "video": "path/to/video.mp4", "test": "path/to/test.py", "recording": "path/to/recording.json" }
    """
    actions = convert_session(session)
    if not actions:
        return {"error": "No replayable actions in session"}

    config = config or RecorderConfig()
    result = {}

    with PlaywrightRecorder(config) as recorder:
        # Find the first navigate action to get the start URL
        start_url = None
        for a in actions:
            if a.action_type == ActionType.NAVIGATE and a.value:
                start_url = a.value
                break

        page = recorder.start_recording(navigate_to=start_url)

        # Inject the actions so generate_test_code works
        recorder._actions = actions
        recorder._start_time = time.time()

        # Replay each action on the real page
        for action in actions:
            try:
                _replay_action(page, action)
            except Exception as e:
                print(f"[replay] Skipped {action.action_type.value}: {e}")

        recorder.stop_recording()

        # Save outputs
        video_path = recorder.get_video_path()
        if video_path:
            result["video"] = str(video_path)

        if generate_test:
            test_name = f"test_session_{session.get('id', 'unknown')}"
            test_path = recorder.save_test_code(
                filepath=f"{output_dir}/{test_name}.py",
                test_name=test_name,
            )
            result["test"] = test_path

        rec_path = recorder.save_recording(
            filepath=f"{output_dir}/session_{session.get('id', 'unknown')}.json"
        )
        result["recording"] = rec_path

    return result


def _replay_action(page, action: RecordedAction):
    """Replay a single action on a Playwright page."""
    match action.action_type:
        case ActionType.NAVIGATE:
            if action.value:
                page.goto(action.value, wait_until="domcontentloaded")
        case ActionType.CLICK:
            if action.selector:
                page.click(action.selector, timeout=5000)
        case ActionType.DOUBLE_CLICK:
            if action.selector:
                page.dblclick(action.selector, timeout=5000)
        case ActionType.TYPE:
            if action.selector and action.value is not None:
                page.fill(action.selector, action.value, timeout=5000)
        case ActionType.SELECT:
            if action.selector and action.value is not None:
                page.select_option(action.selector, action.value, timeout=5000)
        case ActionType.CHECK:
            if action.selector:
                page.check(action.selector, timeout=5000)
        case ActionType.UNCHECK:
            if action.selector:
                page.uncheck(action.selector, timeout=5000)
        case ActionType.SCROLL:
            pos = action.options.get("position", {})
            page.evaluate(f"window.scrollTo({pos.get('x', 0)}, {pos.get('y', 0)})")
        case _:
            pass


class _BridgeHandler(BaseHTTPRequestHandler):
    """HTTP handler for receiving JS SDK sessions."""

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        try:
            session = json.loads(body)
        except json.JSONDecodeError:
            self._respond(400, {"error": "Invalid JSON"})
            return

        action_count = len(session.get("actions", []))
        session_id = session.get("id", "unknown")
        print(f"[bridge] Received session {session_id} with {action_count} actions")

        # Save the raw session to generated-tests for backward compat
        out_dir = Path(self.server.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        raw_path = out_dir / f"session_{session_id}_raw.json"
        raw_path.write_text(json.dumps(session, indent=2))

        # Also save to .recordloop/sessions/ for CI/CD pipeline
        sessions_dir = Path(".recordloop/sessions")
        sessions_dir.mkdir(parents=True, exist_ok=True)
        session_path = sessions_dir / f"{session_id}.json"
        session_path.write_text(json.dumps(session, indent=2))

        # Convert to RecordedActions and save
        actions = convert_session(session)
        converted = [a.to_dict() for a in actions]
        conv_path = out_dir / f"session_{session_id}.json"
        conv_path.write_text(json.dumps(converted, indent=2, default=str))

        print(f"[bridge] Saved {len(actions)} actions to {conv_path}")

        # Generate test code without Playwright (just code generation)
        recorder = PlaywrightRecorder.__new__(PlaywrightRecorder)
        recorder.config = RecorderConfig()
        recorder._actions = actions
        code = recorder.generate_test_code(test_name=f"test_session_{session_id}")
        test_path = out_dir / f"test_session_{session_id}.py"
        test_path.write_text(code)
        print(f"[bridge] Generated test: {test_path}")

        self._respond(200, {
            "ok": True,
            "actions": len(actions),
            "recording": str(conv_path),
            "test": str(test_path),
        })

    def do_OPTIONS(self):
        """Handle CORS preflight."""
        self.send_response(204)
        self._cors_headers()
        self.end_headers()

    def _respond(self, status, data):
        self.send_response(status)
        self._cors_headers()
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def _cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def log_message(self, format, *args):
        # Quieter logging
        pass


def serve(port: int = 8787, output_dir: str = "generated-tests"):
    """
    Start the bridge server.

    The JS SDK POSTs sessions here. The server converts them
    to RecordedActions, saves the recording JSON, and generates test code.

    Args:
        port: Port to listen on
        output_dir: Where to save outputs
    """
    server = HTTPServer(("0.0.0.0", port), _BridgeHandler)
    server.output_dir = output_dir
    print(f"RecordLoop bridge listening on http://localhost:{port}")
    print(f"Output directory: {output_dir}")
    print()
    print("Add this to your frontend:")
    print(f'  const rl = new RecordLoop({{ endpoint: "http://localhost:{port}" }})')
    print("  rl.start()")
    print()
    print("Press Ctrl+C to stop.")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.server_close()
