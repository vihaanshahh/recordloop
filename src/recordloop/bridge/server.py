"""
recordloop.bridge.server
~~~~~~~~~~~~~~~~~~~~~~~~
HTTP bridge server that receives POST requests from the JS SDK running in the
browser, persists sessions to disk, and serves the JS bundle.

Zero Playwright dependency.  Stdlib only: http.server, threading, json, pathlib.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Inline JS stub served when the built bundle is not present
# ---------------------------------------------------------------------------

_JS_STUB = """\
/* RecordLoop SDK stub — built bundle not found.
 * Run `npm run build` inside the js/ directory to generate the real bundle.
 */
(function () {
  'use strict';
  var RecordLoop = {
    init: function (opts) {
      console.warn('[RecordLoop] Using stub SDK. Build the JS bundle for full functionality.');
      return {
        stop: function () {},
        getSession: function () { return null; }
      };
    }
  };
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = RecordLoop;
  } else {
    window.RecordLoop = RecordLoop;
  }
})();
"""

# ---------------------------------------------------------------------------
# CORS headers applied to every response
# ---------------------------------------------------------------------------

_CORS_HEADERS: list[tuple[str, str]] = [
    ("Access-Control-Allow-Origin", "*"),
    ("Access-Control-Allow-Methods", "GET, POST, OPTIONS"),
    ("Access-Control-Allow-Headers", "Content-Type"),
]

VERSION = "2.0.0"


# ---------------------------------------------------------------------------
# Request handler
# ---------------------------------------------------------------------------

class _BridgeHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the RecordLoop bridge server."""

    # Injected by BridgeServer before the server starts
    sessions_dir: Path
    js_bundle_path: Path

    # ------------------------------------------------------------------
    # Silence the default access log — we do our own minimal logging
    # ------------------------------------------------------------------

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: D401
        pass

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._send_cors_preflight()

    def do_GET(self) -> None:  # noqa: N802
        path = self._clean_path()
        if path == "/health":
            self._handle_health()
        elif path == "/sdk.js":
            self._handle_sdk_js()
        elif path == "/sessions":
            self._handle_list_sessions()
        else:
            self._send_json({"error": f"Not found: {path}"}, status=404)

    def do_POST(self) -> None:  # noqa: N802
        path = self._clean_path()
        if path == "/session":
            self._handle_post_session()
        elif path == "/diff":
            self._handle_post_diff()
        else:
            self._send_json({"error": f"Not found: {path}"}, status=404)

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    def _handle_health(self) -> None:
        self._send_json({"ok": True, "version": VERSION})

    def _handle_sdk_js(self) -> None:
        bundle = self.__class__.js_bundle_path
        if bundle.is_file():
            try:
                data = bundle.read_bytes()
                self._send_response_bytes(
                    data,
                    content_type="application/javascript; charset=utf-8",
                    status=200,
                )
                return
            except OSError as exc:
                self._print_err(f"Failed to read JS bundle: {exc}")
                # Fall through to stub
        # Serve the inline stub
        self._send_response_bytes(
            _JS_STUB.encode("utf-8"),
            content_type="application/javascript; charset=utf-8",
            status=200,
        )

    def _handle_post_session(self) -> None:
        body = self._read_body()
        if body is None:
            return  # error already sent

        try:
            data = json.loads(body)
        except json.JSONDecodeError as exc:
            self._send_json({"error": f"Invalid JSON: {exc}"}, status=400)
            return

        # Import here to keep startup fast and to avoid circular issues
        try:
            from recordloop.core.session import Session  # noqa: PLC0415
        except ImportError as exc:
            self._send_json({"error": f"Internal import error: {exc}"}, status=500)
            self._print_err(f"Import error in /session: {exc}")
            return

        try:
            session = Session.from_dict(data)
        except (KeyError, ValueError, TypeError) as exc:
            self._send_json({"error": f"Invalid session payload: {exc}"}, status=400)
            return

        sessions_dir: Path = self.__class__.sessions_dir
        try:
            sessions_dir.mkdir(parents=True, exist_ok=True)
            dest = sessions_dir / f"{session.id}.json"
            dest.write_text(session.to_json(indent=2), encoding="utf-8")
        except OSError as exc:
            self._send_json({"error": f"Failed to save session: {exc}"}, status=500)
            self._print_err(f"IO error saving session {session.id}: {exc}")
            return

        self._send_json({
            "session_id": session.id,
            "actions": len(session.actions),
            "saved_to": str(dest),
        }, status=200)

    def _handle_post_diff(self) -> None:
        body = self._read_body()
        if body is None:
            return

        try:
            data = json.loads(body)
        except json.JSONDecodeError as exc:
            self._send_json({"error": f"Invalid JSON: {exc}"}, status=400)
            return

        session_a_ref = data.get("session_a")
        session_b_ref = data.get("session_b")

        if not session_a_ref or not session_b_ref:
            self._send_json(
                {"error": "Body must contain 'session_a' and 'session_b' fields"},
                status=400,
            )
            return

        try:
            from recordloop.core.session import Session  # noqa: PLC0415
            from recordloop.core.diff import diff_sessions  # noqa: PLC0415
        except ImportError as exc:
            self._send_json({"error": f"Internal import error: {exc}"}, status=500)
            self._print_err(f"Import error in /diff: {exc}")
            return

        session_a = self._load_session_ref(session_a_ref, Session)
        if session_a is None:
            self._send_json(
                {"error": f"Session not found or unreadable: {session_a_ref!r}"},
                status=404,
            )
            return

        session_b = self._load_session_ref(session_b_ref, Session)
        if session_b is None:
            self._send_json(
                {"error": f"Session not found or unreadable: {session_b_ref!r}"},
                status=404,
            )
            return

        try:
            result = diff_sessions(session_a, session_b)
        except Exception as exc:  # noqa: BLE001
            self._send_json({"error": f"Diff failed: {exc}"}, status=500)
            self._print_err(f"diff_sessions error: {exc}")
            return

        self._send_json(result.to_dict())

    def _handle_list_sessions(self) -> None:
        sessions_dir: Path = self.__class__.sessions_dir
        entries: list[dict[str, Any]] = []

        if sessions_dir.is_dir():
            for path in sorted(sessions_dir.glob("*.json")):
                try:
                    raw = json.loads(path.read_text(encoding="utf-8"))
                    session_id = raw.get("id", path.stem)
                    recorded_at = raw.get("recorded_at", "")
                    action_count = len(raw.get("actions", []))
                    entries.append({
                        "id": session_id,
                        "recorded_at": recorded_at,
                        "action_count": action_count,
                    })
                except (OSError, json.JSONDecodeError, ValueError) as exc:
                    # Log the bad file but keep listing the rest
                    self._print_err(f"Skipping unreadable session file {path.name}: {exc}")

        self._send_json({"sessions": entries})

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _clean_path(self) -> str:
        """Return the URL path without query string."""
        raw = self.path or "/"
        return raw.split("?", 1)[0].rstrip("/") or "/"

    def _read_body(self) -> bytes | None:
        """Read the request body; send 400 and return None on error."""
        length_header = self.headers.get("Content-Length")
        if length_header is None:
            # Try chunked / unknown-length reads up to 10 MiB
            try:
                body = self.rfile.read(10 * 1024 * 1024)
            except OSError as exc:
                self._send_json({"error": f"Failed to read request body: {exc}"}, status=400)
                return None
        else:
            try:
                length = int(length_header)
            except ValueError:
                self._send_json(
                    {"error": f"Invalid Content-Length header: {length_header!r}"},
                    status=400,
                )
                return None
            if length > 10 * 1024 * 1024:
                self._send_json(
                    {"error": "Request body too large (max 10 MiB)"},
                    status=413,
                )
                return None
            try:
                body = self.rfile.read(length)
            except OSError as exc:
                self._send_json({"error": f"Failed to read request body: {exc}"}, status=400)
                return None
        return body

    def _load_session_ref(self, ref: str, session_cls: type) -> Any:
        """Resolve a session reference (id or path) and return a Session, or None."""
        sessions_dir: Path = self.__class__.sessions_dir

        # Try as a literal path first (absolute or relative)
        candidate = Path(ref)
        if candidate.is_absolute() and candidate.is_file():
            return self._parse_session_file(candidate, session_cls)
        if not candidate.is_absolute():
            # Could be relative to cwd
            resolved = Path.cwd() / candidate
            if resolved.is_file():
                return self._parse_session_file(resolved, session_cls)

        # Try as a session ID in sessions_dir
        id_path = sessions_dir / f"{ref}.json"
        if id_path.is_file():
            return self._parse_session_file(id_path, session_cls)

        return None

    @staticmethod
    def _parse_session_file(path: Path, session_cls: type) -> Any | None:
        """Read and deserialise a session file; return None on any error."""
        try:
            text = path.read_text(encoding="utf-8")
            return session_cls.from_json(text)
        except (OSError, KeyError, ValueError, TypeError):
            return None

    def _send_cors_preflight(self) -> None:
        self.send_response(204)
        for name, value in _CORS_HEADERS:
            self.send_header(name, value)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _send_json(self, payload: dict[str, Any], *, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_response_bytes(body, content_type="application/json; charset=utf-8", status=status)

    def _send_response_bytes(
        self,
        data: bytes,
        *,
        content_type: str,
        status: int,
    ) -> None:
        self.send_response(status)
        for name, value in _CORS_HEADERS:
            self.send_header(name, value)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    @staticmethod
    def _print_err(msg: str) -> None:
        print(f"[RecordLoop bridge] ERROR: {msg}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# BridgeServer
# ---------------------------------------------------------------------------

class BridgeServer:
    """RecordLoop bridge HTTP server.

    Receives POST requests from the JS SDK, persists sessions, and serves
    the built JS bundle (or an inline stub if the bundle is absent).

    Parameters
    ----------
    port:
        TCP port to listen on.  Default: 8787.
    sessions_dir:
        Directory where session JSON files are saved.
        Default: ``.recordloop/sessions`` (relative to the process cwd).
    """

    def __init__(
        self,
        port: int = 8787,
        sessions_dir: Path | str = ".recordloop/sessions",
    ) -> None:
        self.port = port
        self.sessions_dir = Path(sessions_dir).expanduser()

        # Resolve the JS bundle path relative to *this file*:
        # src/recordloop/bridge/server.py  →  ../../js/dist/recordloop.js
        _here = Path(__file__).parent
        self._js_bundle_path: Path = (_here / "../../.." / "js" / "dist" / "recordloop.js").resolve()

        self._httpd: HTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the server, print the startup message, and block until Ctrl-C."""
        self._create_server()
        assert self._httpd is not None
        self._print_startup()
        try:
            self._httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n[RecordLoop bridge] Stopped.", flush=True)
        finally:
            self._httpd.server_close()
            self._httpd = None

    def start_background(self) -> threading.Thread:
        """Start the server in a daemon background thread and return it."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise RuntimeError("BridgeServer is already running.")
            self._create_server()
            assert self._httpd is not None
            self._thread = threading.Thread(
                target=self._httpd.serve_forever,
                name="recordloop-bridge",
                daemon=True,
            )
            self._thread.start()
        self._print_startup()
        return self._thread

    def stop(self) -> None:
        """Gracefully shut down the server."""
        with self._lock:
            httpd = self._httpd
            self._httpd = None

        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()

        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    @property
    def url(self) -> str:
        return f"http://localhost:{self.port}"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _create_server(self) -> None:
        """Instantiate the HTTPServer, injecting class-level config into the handler."""
        # Inject shared state into the handler class.  We create a fresh
        # subclass each time so multiple BridgeServer instances do not share
        # state (useful in tests).
        handler_cls = type(
            "_ConfiguredBridgeHandler",
            (_BridgeHandler,),
            {
                "sessions_dir": self.sessions_dir,
                "js_bundle_path": self._js_bundle_path,
            },
        )
        self._httpd = HTTPServer(("", self.port), handler_cls)

    def _print_startup(self) -> None:
        sessions_display = str(self.sessions_dir).rstrip("/") + "/"
        print(
            f"RecordLoop bridge running at {self.url}\n"
            f"Sessions saved to: {sessions_display}\n"
            f"Press Ctrl+C to stop.",
            flush=True,
        )
