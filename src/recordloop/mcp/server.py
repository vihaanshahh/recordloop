"""
recordloop.mcp.server
~~~~~~~~~~~~~~~~~~~~~
MCP server that exposes recordloop's session and diff tools to AI coding
tools such as Claude Code and Cursor.

Install the extra before use::

    pip install recordloop[mcp]
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.server.models import InitializationOptions
    from mcp.server.lowlevel.server import NotificationOptions
    from mcp import types
except ImportError as e:
    raise ImportError(
        "recordloop[mcp] is required. Install with: pip install recordloop[mcp]"
    ) from e

from recordloop.core.session import Session
from recordloop.core.diff import diff_sessions as _diff_sessions
from recordloop.bridge.server import BridgeServer


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _load_session_by_id(session_id: str, sessions_dir: Path) -> Session:
    """Load a session JSON file from *sessions_dir*/{session_id}.json.

    Raises
    ------
    FileNotFoundError
        If the file does not exist.
    ValueError
        If the file cannot be parsed as a valid Session.
    """
    path = sessions_dir / f"{session_id}.json"
    if not path.is_file():
        raise FileNotFoundError(
            f"Session '{session_id}' not found in {sessions_dir}"
        )
    text = path.read_text(encoding="utf-8")
    return Session.from_json(text)


# ---------------------------------------------------------------------------
# RecordLoopMCP
# ---------------------------------------------------------------------------

class RecordLoopMCP:
    """MCP server wrapper for recordloop.

    Parameters
    ----------
    sessions_dir:
        Directory that contains ``<session_id>.json`` files.
        Defaults to ``.recordloop/sessions`` relative to the working directory.
    """

    def __init__(self, sessions_dir: str = ".recordloop/sessions") -> None:
        self.sessions_dir = Path(sessions_dir)
        self.server = Server("recordloop")
        self._bridge_server: BridgeServer | None = None
        self._bridge_lock = threading.Lock()
        self._register_tools()

    # ------------------------------------------------------------------
    # Tool registration
    # ------------------------------------------------------------------

    def _register_tools(self) -> None:
        server = self.server
        sessions_dir = self.sessions_dir

        # ----------------------------------------------------------------
        # Tool 1: list_sessions
        # ----------------------------------------------------------------

        @server.list_tools()
        async def _list_tools() -> list[types.Tool]:
            return [
                types.Tool(
                    name="list_sessions",
                    description=(
                        "List recorded browser sessions, sorted newest first. "
                        "Returns id, recorded_at, duration_ms, action_count, and base_url for each session."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "sessions_dir": {
                                "type": "string",
                                "description": "Directory containing session JSON files.",
                                "default": ".recordloop/sessions",
                            },
                            "limit": {
                                "type": "integer",
                                "description": "Maximum number of sessions to return.",
                                "default": 20,
                            },
                        },
                        "additionalProperties": False,
                    },
                ),
                types.Tool(
                    name="get_session",
                    description=(
                        "Get the full details of a recorded session including all actions. "
                        "Returns all session fields and the complete actions array."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "session_id": {
                                "type": "string",
                                "description": "The unique session ID to retrieve.",
                            },
                            "sessions_dir": {
                                "type": "string",
                                "description": "Directory containing session JSON files.",
                                "default": ".recordloop/sessions",
                            },
                        },
                        "required": ["session_id"],
                        "additionalProperties": False,
                    },
                ),
                types.Tool(
                    name="diff_sessions",
                    description=(
                        "Structurally diff two browser sessions at the semantic-intent level "
                        "using a Smith-Waterman alignment algorithm. Returns a full structured diff "
                        "with per-action entries classified as unchanged/modified/added/removed, "
                        "plus aggregate summary statistics and a similarity score."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "session_a_id": {
                                "type": "string",
                                "description": "The baseline session ID (A).",
                            },
                            "session_b_id": {
                                "type": "string",
                                "description": "The candidate session ID (B) to compare against A.",
                            },
                            "sessions_dir": {
                                "type": "string",
                                "description": "Directory containing session JSON files.",
                                "default": ".recordloop/sessions",
                            },
                        },
                        "required": ["session_a_id", "session_b_id"],
                        "additionalProperties": False,
                    },
                ),
                types.Tool(
                    name="replay_session",
                    description=(
                        "Replay a recorded session through a real Chromium browser using Playwright "
                        "and capture it as video. Requires recordloop[capture] to be installed."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "session_id": {
                                "type": "string",
                                "description": "The session ID to replay.",
                            },
                            "sessions_dir": {
                                "type": "string",
                                "description": "Directory containing session JSON files.",
                                "default": ".recordloop/sessions",
                            },
                            "headless": {
                                "type": "boolean",
                                "description": "Whether to run the browser in headless mode.",
                                "default": True,
                            },
                        },
                        "required": ["session_id"],
                        "additionalProperties": False,
                    },
                ),
                types.Tool(
                    name="start_bridge",
                    description=(
                        "Start the RecordLoop HTTP bridge server in a background thread. "
                        "The bridge receives session data from the JS SDK running in the browser "
                        "and persists it to disk. If the server is already running on the requested "
                        "port, returns its status without starting a new instance."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "port": {
                                "type": "integer",
                                "description": "TCP port to listen on.",
                                "default": 8787,
                            },
                            "sessions_dir": {
                                "type": "string",
                                "description": "Directory where session JSON files will be saved.",
                                "default": ".recordloop/sessions",
                            },
                        },
                        "additionalProperties": False,
                    },
                ),
            ]

        # ----------------------------------------------------------------
        # Tool dispatcher: call_tool
        # ----------------------------------------------------------------

        @server.call_tool()
        async def _call_tool(
            name: str, arguments: dict[str, Any] | None
        ) -> list[types.TextContent]:
            args: dict[str, Any] = arguments or {}

            if name == "list_sessions":
                result = _tool_list_sessions(args)
            elif name == "get_session":
                result = _tool_get_session(args)
            elif name == "diff_sessions":
                result = _tool_diff_sessions(args)
            elif name == "replay_session":
                result = _tool_replay_session(args)
            elif name == "start_bridge":
                result = _tool_start_bridge(args, self)
            else:
                result = {"error": f"Unknown tool: {name!r}"}

            return [
                types.TextContent(
                    type="text",
                    text=json.dumps(result, ensure_ascii=False, indent=2),
                )
            ]

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Run the MCP server on stdio (blocking)."""
        import asyncio
        asyncio.run(self._run())

    async def _run(self) -> None:
        init_options = InitializationOptions(
            server_name="recordloop",
            server_version="2.0.0",
            capabilities=self.server.get_capabilities(
                notification_options=NotificationOptions(),
                experimental_capabilities={},
            ),
        )
        async with stdio_server() as (read_stream, write_stream):
            await self.server.run(read_stream, write_stream, init_options)


# ---------------------------------------------------------------------------
# Tool implementations (plain synchronous functions — no async I/O needed)
# ---------------------------------------------------------------------------

def _resolve_sessions_dir(args: dict[str, Any]) -> Path:
    raw = args.get("sessions_dir", ".recordloop/sessions")
    return Path(raw)


def _tool_list_sessions(args: dict[str, Any]) -> dict[str, Any]:
    sessions_dir = _resolve_sessions_dir(args)
    limit: int = int(args.get("limit", 20))

    if not sessions_dir.is_dir():
        return {"sessions": []}

    entries: list[dict[str, Any]] = []
    for path in sessions_dir.glob("*.json"):
        try:
            raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue

        recorded_at: str = raw.get("recorded_at", "")
        entries.append({
            "id": raw.get("id", path.stem),
            "recorded_at": recorded_at,
            "duration_ms": int(raw.get("duration_ms", 0)),
            "action_count": len(raw.get("actions", [])),
            "base_url": raw.get("base_url", ""),
        })

    # Sort newest first — ISO-8601 strings sort lexicographically.
    entries.sort(key=lambda e: e["recorded_at"], reverse=True)
    return {"sessions": entries[:limit]}


def _tool_get_session(args: dict[str, Any]) -> dict[str, Any]:
    session_id: str = args.get("session_id", "")
    if not session_id:
        return {"error": "session_id is required"}

    sessions_dir = _resolve_sessions_dir(args)
    try:
        session = _load_session_by_id(session_id, sessions_dir)
    except FileNotFoundError as exc:
        return {"error": str(exc)}
    except (ValueError, KeyError, TypeError) as exc:
        return {"error": f"Failed to parse session '{session_id}': {exc}"}

    return session.to_dict()


def _tool_diff_sessions(args: dict[str, Any]) -> dict[str, Any]:
    session_a_id: str = args.get("session_a_id", "")
    session_b_id: str = args.get("session_b_id", "")

    if not session_a_id:
        return {"error": "session_a_id is required"}
    if not session_b_id:
        return {"error": "session_b_id is required"}

    sessions_dir = _resolve_sessions_dir(args)

    try:
        session_a = _load_session_by_id(session_a_id, sessions_dir)
    except FileNotFoundError as exc:
        return {"error": str(exc)}
    except (ValueError, KeyError, TypeError) as exc:
        return {"error": f"Failed to parse session '{session_a_id}': {exc}"}

    try:
        session_b = _load_session_by_id(session_b_id, sessions_dir)
    except FileNotFoundError as exc:
        return {"error": str(exc)}
    except (ValueError, KeyError, TypeError) as exc:
        return {"error": f"Failed to parse session '{session_b_id}': {exc}"}

    try:
        diff = _diff_sessions(session_a, session_b)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Diff computation failed: {exc}"}

    return diff.to_dict()


def _tool_replay_session(args: dict[str, Any]) -> dict[str, Any]:
    session_id: str = args.get("session_id", "")
    if not session_id:
        return {"success": False, "video_path": None, "error": "session_id is required", "duration_ms": 0}

    headless: bool = bool(args.get("headless", True))
    sessions_dir = _resolve_sessions_dir(args)

    try:
        session = _load_session_by_id(session_id, sessions_dir)
    except FileNotFoundError as exc:
        return {"success": False, "video_path": None, "error": str(exc), "duration_ms": 0}
    except (ValueError, KeyError, TypeError) as exc:
        return {
            "success": False,
            "video_path": None,
            "error": f"Failed to parse session '{session_id}': {exc}",
            "duration_ms": 0,
        }

    try:
        from recordloop.capture import replay_session  # noqa: PLC0415
    except ImportError:
        return {
            "success": False,
            "video_path": None,
            "error": (
                "recordloop[capture] is not installed. "
                "Install with: pip install recordloop[capture] && playwright install chromium"
            ),
            "duration_ms": 0,
        }

    try:
        result = replay_session(session, headless=headless)
    except Exception as exc:  # noqa: BLE001
        return {
            "success": False,
            "video_path": None,
            "error": f"Replay failed: {exc}",
            "duration_ms": 0,
        }

    return {
        "success": result.success,
        "video_path": result.video_path,
        "error": result.error,
        "duration_ms": result.duration_ms,
    }


def _tool_start_bridge(args: dict[str, Any], mcp_instance: RecordLoopMCP) -> dict[str, Any]:
    port: int = int(args.get("port", 8787))
    sessions_dir_str: str = args.get("sessions_dir", ".recordloop/sessions")
    url = f"http://localhost:{port}"

    with mcp_instance._bridge_lock:
        existing = mcp_instance._bridge_server
        if (
            existing is not None
            and existing.port == port
            and existing._thread is not None
            and existing._thread.is_alive()
        ):
            return {
                "status": "already_running",
                "port": port,
                "url": url,
            }

        bridge = BridgeServer(port=port, sessions_dir=sessions_dir_str)
        try:
            bridge.start_background()
        except RuntimeError:
            # start_background raises if the server is already running on that
            # BridgeServer instance — treat as already_running.
            return {
                "status": "already_running",
                "port": port,
                "url": url,
            }

        mcp_instance._bridge_server = bridge

    return {
        "status": "started",
        "port": port,
        "url": url,
        "sessions_dir": sessions_dir_str,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def start_mcp_server(sessions_dir: str = ".recordloop/sessions") -> None:
    """Entry point called from the CLI.

    Starts the MCP server on stdio and blocks until the client disconnects.
    """
    mcp = RecordLoopMCP(sessions_dir=sessions_dir)
    mcp.run()
