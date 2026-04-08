"""
recordloop.cli.main
~~~~~~~~~~~~~~~~~~~
Command-line interface for RecordLoop.

Entry point:  ``recordloop`` (configured in pyproject.toml / setup.cfg)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

import typer
from rich import print as rprint
from rich.console import Console
from rich.table import Table
from rich.text import Text
from rich import box

# ---------------------------------------------------------------------------
# App + sub-app definitions
# ---------------------------------------------------------------------------

app = typer.Typer(
    name="recordloop",
    help=(
        "Browser session recording and diff tool.\n\n"
        "Record user interactions with the JS SDK, then diff, replay, "
        "and inspect sessions from the command line."
    ),
    no_args_is_help=True,
    rich_markup_mode="rich",
)

sessions_app = typer.Typer(
    name="sessions",
    help="List and inspect recorded sessions.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

app.add_typer(sessions_app, name="sessions")

console = Console()
err_console = Console(stderr=True)

# Default paths — overridable everywhere via options.
_DEFAULT_SESSIONS_DIR = ".recordloop/sessions"
_DEFAULT_VIDEO_DIR = ".recordloop/videos"
_DEFAULT_PORT = 8787


# ---------------------------------------------------------------------------
# Helper: load session by ID or file path
# ---------------------------------------------------------------------------

def _load_session(id_or_path: str, sessions_dir: Path):  # -> Session
    """Resolve *id_or_path* to a :class:`~recordloop.core.session.Session`.

    Resolution order:

    1. If it is an existing file path (absolute or relative), load it directly.
    2. Otherwise look for ``{sessions_dir}/{id_or_path}.json``.

    Raises :class:`typer.BadParameter` with a helpful message when nothing is
    found.
    """
    from recordloop.core import Session  # local import keeps startup fast

    # Attempt 1: treat as a literal path.
    candidate = Path(id_or_path)
    if candidate.exists():
        try:
            return Session.from_json(candidate.read_text(encoding="utf-8"))
        except (OSError, KeyError, ValueError, TypeError) as exc:
            raise typer.BadParameter(
                f"Found file '{candidate}' but could not parse it as a session: {exc}"
            )

    # Attempt 2: look up by session ID in sessions_dir.
    id_path = sessions_dir / f"{id_or_path}.json"
    if id_path.exists():
        try:
            return Session.from_json(id_path.read_text(encoding="utf-8"))
        except (OSError, KeyError, ValueError, TypeError) as exc:
            raise typer.BadParameter(
                f"Found '{id_path}' but could not parse it as a session: {exc}"
            )

    # Nothing found — give a helpful error.
    raise typer.BadParameter(
        f"Session not found: '{id_or_path}'\n"
        f"  Looked for file:  {candidate.resolve()}\n"
        f"  Looked for ID in: {sessions_dir / (id_or_path + '.json')}\n"
        f"  Run [bold]recordloop sessions list[/bold] to see available sessions."
    )


# ---------------------------------------------------------------------------
# Helper: selector display string
# ---------------------------------------------------------------------------

def _key_display(action) -> str:
    """Return a compact 'strategy:value' string for an action's SemanticKey."""
    if action.key is None:
        return ""
    return f"{action.key.strategy}:{action.key.value}"


# ---------------------------------------------------------------------------
# `recordloop serve`
# ---------------------------------------------------------------------------

@app.command()
def serve(
    port: int = typer.Option(
        _DEFAULT_PORT,
        "--port", "-p",
        help="TCP port for the bridge server.",
        show_default=True,
    ),
    sessions_dir: Path = typer.Option(
        _DEFAULT_SESSIONS_DIR,
        "--sessions-dir",
        help="Directory where received sessions are saved.",
        show_default=True,
    ),
) -> None:
    """Start the bridge server to receive recordings from the JS SDK.

    The server listens for POST /session requests from the browser SDK and
    persists each recording as a JSON file in [bold]--sessions-dir[/bold].
    It also serves the SDK bundle at [cyan]GET /sdk.js[/cyan].

    Press [bold]Ctrl+C[/bold] to stop.
    """
    from recordloop.bridge.server import BridgeServer

    sessions_dir.mkdir(parents=True, exist_ok=True)

    console.rule("[bold green]RecordLoop Bridge Server")
    console.print(f"  Listening on:  [cyan]http://localhost:{port}[/cyan]")
    console.print(f"  Sessions dir:  [cyan]{sessions_dir.resolve()}[/cyan]")
    console.print(f"  SDK endpoint:  [cyan]http://localhost:{port}/sdk.js[/cyan]")
    console.print()
    console.print(
        "  Add this snippet to your app:\n"
        f"  [dim]<script src=\"http://localhost:{port}/sdk.js\"></script>[/dim]\n"
        f"  [dim]<script>const rl = new RecordLoop({{ endpoint: 'http://localhost:{port}' }})</script>[/dim]"
    )
    console.print()
    console.print("  [bold]Ctrl+C[/bold] to stop.\n")

    server = BridgeServer(port=port, sessions_dir=sessions_dir)
    try:
        server.start()
    except KeyboardInterrupt:
        console.print("\n[yellow]Bridge server stopped.[/yellow]")


# ---------------------------------------------------------------------------
# `recordloop diff <session_a> <session_b>`
# ---------------------------------------------------------------------------

@app.command()
def diff(
    session_a: str = typer.Argument(
        ...,
        help="Baseline session: ID or path to a .json file.",
        metavar="SESSION_A",
    ),
    session_b: str = typer.Argument(
        ...,
        help="Candidate session: ID or path to a .json file.",
        metavar="SESSION_B",
    ),
    sessions_dir: Path = typer.Option(
        _DEFAULT_SESSIONS_DIR,
        "--sessions-dir",
        help="Directory to search when resolving session IDs.",
        show_default=True,
    ),
    output_json: bool = typer.Option(
        False,
        "--json",
        help="Print raw JSON diff instead of the formatted table.",
        is_flag=True,
    ),
) -> None:
    """Diff two recorded sessions.

    SESSION_A is the [bold]baseline[/bold]; SESSION_B is the [bold]candidate[/bold].
    Each argument can be a session ID (looked up in [bold]--sessions-dir[/bold])
    or a path to a ``.json`` file.

    Colour legend in the diff table:
    [green]green = added[/green]  [red]red = removed[/red]  [yellow]yellow = modified[/yellow]
    Unchanged actions are not shown (their count appears in the summary).
    """
    from recordloop.core import diff_sessions, ChangeKind

    sessions_dir = sessions_dir.resolve() if not sessions_dir.is_absolute() else sessions_dir

    with console.status("Loading sessions…"):
        sess_a = _load_session(session_a, sessions_dir)
        sess_b = _load_session(session_b, sessions_dir)

    with console.status("Computing diff…"):
        result = diff_sessions(sess_a, sess_b)

    # ------------------------------------------------------------------
    # JSON output
    # ------------------------------------------------------------------
    if output_json:
        typer.echo(result.to_json(indent=2))
        return

    # ------------------------------------------------------------------
    # Rich table output
    # ------------------------------------------------------------------
    s = result.summary
    pct = int(round(s.similarity_score * 100))

    console.print()
    console.print(
        f"[bold]Diff:[/bold] [cyan]{sess_a.id[:8]}[/cyan] "
        f"[dim]→[/dim] [cyan]{sess_b.id[:8]}[/cyan]  "
        f"[dim]({len(sess_a)} actions → {len(sess_b)} actions)[/dim]"
    )
    console.print()

    table = Table(
        box=box.SIMPLE_HEAD,
        show_header=True,
        header_style="bold",
        expand=True,
    )
    table.add_column("#", style="dim", width=4, no_wrap=True)
    table.add_column("Change", width=10, no_wrap=True)
    table.add_column("Type", width=10, no_wrap=True)
    table.add_column("Selector", overflow="fold")
    table.add_column("Value / Note", overflow="fold")

    row_num = 0
    for entry in result.entries:
        kind = entry.kind

        if kind == ChangeKind.UNCHANGED:
            continue  # deliberately hidden — noisy

        row_num += 1

        if kind == ChangeKind.ADDED:
            action = entry.action_b
            assert action is not None
            style = "green"
            change_label = Text("+ added", style="bold green")
            selector = _key_display(action)
            value_note = action.value or ""
        elif kind == ChangeKind.REMOVED:
            action = entry.action_a
            assert action is not None
            style = "red"
            change_label = Text("- removed", style="bold red")
            selector = _key_display(action)
            value_note = action.value or ""
        else:  # MODIFIED
            action_a = entry.action_a
            action_b = entry.action_b
            assert action_a is not None and action_b is not None
            style = "yellow"
            change_label = Text("~ modified", style="bold yellow")
            selector = _key_display(action_b) or _key_display(action_a)

            # Show what changed between A and B.
            parts: list[str] = []
            if (action_a.value or "") != (action_b.value or ""):
                a_val = action_a.value or "(none)"
                b_val = action_b.value or "(none)"
                parts.append(f'"{a_val}" → "{b_val}"')
            if _key_display(action_a) != _key_display(action_b):
                parts.append(f"selector changed")
            if action_a.type != action_b.type:
                parts.append(f"type: {action_a.type} → {action_b.type}")
            sim_pct = int(round(entry.similarity * 100))
            parts.append(f"{sim_pct}% similar")
            value_note = "; ".join(parts) if parts else ""

            action = action_b  # use B-side for type display

        table.add_row(
            str(row_num),
            change_label,
            Text(str(action.type), style=style),
            Text(selector, style=style),
            Text(value_note, style=style),
        )

    if row_num == 0:
        console.print("  [dim]No changes — sessions are identical.[/dim]")
    else:
        console.print(table)

    # Summary line.
    summary_parts = [
        f"[green]✓ {s.unchanged} unchanged[/green]",
        f"[yellow]~ {s.modified} modified[/yellow]",
        f"[green]+ {s.added} added[/green]",
        f"[red]- {s.removed} removed[/red]",
        f"[bold]({pct}% similar)[/bold]",
    ]
    console.print("  " + "  ".join(summary_parts))
    console.print()


# ---------------------------------------------------------------------------
# `recordloop sessions list`
# ---------------------------------------------------------------------------

@sessions_app.command("list")
def sessions_list(
    sessions_dir: Path = typer.Option(
        _DEFAULT_SESSIONS_DIR,
        "--sessions-dir",
        help="Directory to scan for session files.",
        show_default=True,
    ),
    output_json: bool = typer.Option(
        False,
        "--json",
        help="Print raw JSON array instead of a table.",
        is_flag=True,
    ),
) -> None:
    """List all recorded sessions.

    Scans [bold]--sessions-dir[/bold] for ``.json`` files and prints a
    summary table sorted by recording date (newest first).
    """
    from recordloop.core import Session

    sessions_dir = Path(sessions_dir).resolve()

    if not sessions_dir.exists():
        err_console.print(
            f"[yellow]Sessions directory does not exist:[/yellow] {sessions_dir}\n"
            "Run [bold]recordloop init[/bold] to set up RecordLoop, or "
            "[bold]recordloop serve[/bold] to start receiving recordings."
        )
        raise typer.Exit(1)

    session_files = sorted(sessions_dir.glob("*.json"))
    if not session_files:
        console.print(f"[dim]No sessions found in {sessions_dir}[/dim]")
        console.print("Start the bridge server with [bold]recordloop serve[/bold] to record sessions.")
        return

    sessions: list[Session] = []
    bad_files: list[str] = []
    for sf in session_files:
        try:
            sessions.append(Session.from_json(sf.read_text(encoding="utf-8")))
        except Exception:
            bad_files.append(sf.name)

    # Sort newest-first.
    sessions.sort(key=lambda s: s.recorded_at, reverse=True)

    if output_json:
        payload = [
            {
                "id": s.id,
                "recorded_at": s.recorded_at.isoformat(),
                "duration_ms": s.duration_ms,
                "actions": len(s),
                "base_url": s.base_url,
            }
            for s in sessions
        ]
        typer.echo(json.dumps(payload, indent=2))
        return

    table = Table(
        title=f"Sessions in {sessions_dir}",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold cyan",
        expand=False,
    )
    table.add_column("ID", style="cyan", no_wrap=True, min_width=12)
    table.add_column("Recorded at", no_wrap=True)
    table.add_column("Duration", justify="right", no_wrap=True)
    table.add_column("Actions", justify="right", no_wrap=True)
    table.add_column("Base URL", overflow="fold")

    for s in sessions:
        dt_str = s.recorded_at.strftime("%Y-%m-%d %H:%M:%S UTC")
        dur_s = s.duration_ms / 1000.0
        dur_str = f"{dur_s:.1f}s" if dur_s < 3600 else f"{dur_s/3600:.1f}h"
        table.add_row(s.id[:16], dt_str, dur_str, str(len(s)), s.base_url)

    console.print()
    console.print(table)
    console.print(f"\n  [dim]{len(sessions)} session(s) total.[/dim]")

    if bad_files:
        err_console.print(
            f"\n[yellow]Warning:[/yellow] Skipped {len(bad_files)} unreadable file(s): "
            + ", ".join(bad_files)
        )


# ---------------------------------------------------------------------------
# `recordloop sessions show <session_id>`
# ---------------------------------------------------------------------------

@sessions_app.command("show")
def sessions_show(
    session_id: str = typer.Argument(
        ...,
        help="Session ID or path to a .json file.",
        metavar="SESSION_ID",
    ),
    sessions_dir: Path = typer.Option(
        _DEFAULT_SESSIONS_DIR,
        "--sessions-dir",
        help="Directory to search when resolving the session ID.",
        show_default=True,
    ),
    output_json: bool = typer.Option(
        False,
        "--json",
        help="Print the raw session JSON instead of a formatted table.",
        is_flag=True,
    ),
) -> None:
    """Show all actions in a recorded session.

    SESSION_ID can be a session ID (looked up in [bold]--sessions-dir[/bold])
    or a path to a ``.json`` file.
    """
    sessions_dir = Path(sessions_dir).resolve()
    session = _load_session(session_id, sessions_dir)

    if output_json:
        typer.echo(session.to_json(indent=2))
        return

    console.print()
    console.print(f"[bold]Session[/bold] [cyan]{session.id}[/cyan]")
    console.print(f"  Recorded at : {session.recorded_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    dur_s = session.duration_ms / 1000.0
    console.print(f"  Duration    : {dur_s:.1f}s")
    console.print(f"  Base URL    : {session.base_url}")
    console.print(f"  Viewport    : {session.viewport[0]}x{session.viewport[1]}")
    if session.user_agent:
        console.print(f"  User-Agent  : [dim]{session.user_agent}[/dim]")
    if session.meta:
        for k, v in session.meta.items():
            console.print(f"  Meta [{k}]  : {v}")
    console.print()

    if not session.actions:
        console.print("  [dim]No actions recorded in this session.[/dim]")
        return

    table = Table(
        box=box.SIMPLE_HEAD,
        show_header=True,
        header_style="bold",
        expand=True,
    )
    table.add_column("#", style="dim", width=5, no_wrap=True)
    table.add_column("Time (ms)", justify="right", width=10, no_wrap=True)
    table.add_column("Type", width=12, no_wrap=True)
    table.add_column("Selector", overflow="fold")
    table.add_column("Value", overflow="fold")
    table.add_column("Page URL", overflow="fold", style="dim")

    for i, action in enumerate(session.actions, start=1):
        selector = _key_display(action)
        table.add_row(
            str(i),
            str(action.timestamp_ms),
            str(action.type),
            selector,
            action.value or "",
            action.page_url or "",
        )

    console.print(table)
    console.print(f"\n  [dim]{len(session)} action(s).[/dim]\n")


# ---------------------------------------------------------------------------
# `recordloop replay <session_id>`
# ---------------------------------------------------------------------------

@app.command()
def replay(
    session_id: str = typer.Argument(
        ...,
        help="Session ID or path to a .json file to replay.",
        metavar="SESSION_ID",
    ),
    sessions_dir: Path = typer.Option(
        _DEFAULT_SESSIONS_DIR,
        "--sessions-dir",
        help="Directory to search when resolving the session ID.",
        show_default=True,
    ),
    headless: Optional[bool] = typer.Option(
        None,
        "--headless/--no-headless",
        help=(
            "Run Playwright in headless mode.  "
            "Defaults to the RECORDLOOP_HEADLESS env var, or True."
        ),
    ),
    video_dir: Path = typer.Option(
        _DEFAULT_VIDEO_DIR,
        "--video-dir",
        help="Directory where the recorded video is saved.",
        show_default=True,
    ),
) -> None:
    """Replay a recorded session using Playwright and save a video.

    SESSION_ID can be a session ID (looked up in [bold]--sessions-dir[/bold])
    or a direct path to a ``.json`` file.

    Requires the [bold]recordloop[capture][/bold] extra:

        pip install recordloop[capture]
    """
    try:
        from recordloop.capture import SessionReplayer  # type: ignore[import]
    except ImportError:
        err_console.print(
            "\n[bold red]Missing dependency:[/bold red] "
            "The [bold]recordloop\\[capture][/bold] extra is required for replay.\n"
            "\n  Install it with:\n"
            "\n    [bold cyan]pip install 'recordloop[capture]'[/bold cyan]\n"
            "\nThis installs Playwright and its browser binaries.\n"
            "After installing, run [bold]playwright install chromium[/bold] "
            "if you have not already done so.\n"
        )
        raise typer.Exit(1)

    sessions_dir = Path(sessions_dir).resolve()
    video_dir = Path(video_dir).resolve()

    session = _load_session(session_id, sessions_dir)

    # Resolve headless setting: CLI flag > env var > default True.
    if headless is None:
        from recordloop.config.settings import get_settings
        headless = get_settings().headless

    video_dir.mkdir(parents=True, exist_ok=True)

    console.print()
    console.print(f"[bold]Replaying session[/bold] [cyan]{session.id[:16]}[/cyan]")
    console.print(f"  Actions  : {len(session)}")
    console.print(f"  Base URL : {session.base_url}")
    console.print(f"  Headless : {headless}")
    console.print(f"  Video dir: {video_dir}")
    console.print()

    with console.status("Replaying…"):
        replayer = SessionReplayer(
            session=session,
            headless=headless,
            video_dir=str(video_dir),
        )
        video_path = replayer.run()

    if video_path:
        console.print(f"[green]Done.[/green]  Video saved to: [cyan]{video_path}[/cyan]")
    else:
        console.print("[green]Replay complete.[/green]  (No video path returned.)")
    console.print()


# ---------------------------------------------------------------------------
# `recordloop init`
# ---------------------------------------------------------------------------

@app.command()
def init(
    project_dir: Path = typer.Option(
        ".",
        "--dir",
        help="Project directory to initialise.  Defaults to the current directory.",
        show_default=True,
    ),
) -> None:
    """Initialise RecordLoop in the current (or specified) directory.

    Creates [bold].recordloop/sessions/[/bold] and prints the JS snippet to
    add to your app.  Framework-specific instructions are shown when a
    supported framework is detected in [bold]package.json[/bold].
    """
    from recordloop.config.settings import detect_framework, FRAMEWORK_DEFAULTS

    project_dir = Path(project_dir).resolve()

    sessions_dir = project_dir / ".recordloop" / "sessions"
    videos_dir = project_dir / ".recordloop" / "videos"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    videos_dir.mkdir(parents=True, exist_ok=True)

    framework = detect_framework(str(project_dir))
    fw_info = FRAMEWORK_DEFAULTS.get(framework or "", {})
    app_port = fw_info.get("port", 3000)
    dev_cmd = fw_info.get("dev_cmd", "npm start")

    console.rule("[bold green]RecordLoop initialised")
    console.print(f"\n  [green]✓[/green] Created {sessions_dir}")
    console.print(f"  [green]✓[/green] Created {videos_dir}\n")

    if framework:
        console.print(
            f"  Detected framework: [bold cyan]{framework}[/bold cyan]  "
            f"(default dev server: [cyan]http://localhost:{app_port}[/cyan])\n"
        )

    console.rule("Step 1 — Start your dev server")
    console.print(f"\n    [bold cyan]{dev_cmd}[/bold cyan]\n")

    console.rule("Step 2 — Start the bridge server")
    console.print("\n    [bold cyan]recordloop serve[/bold cyan]\n")

    console.rule("Step 3 — Add the SDK snippet to your app")
    _print_sdk_snippet(framework, app_port=int(app_port))  # type: ignore[arg-type]

    console.rule("Step 4 — Record a session")
    console.print(
        "\n  Open your app in a browser, interact with it, "
        "then run:\n"
        "\n    [bold cyan]recordloop sessions list[/bold cyan]\n"
    )


def _print_sdk_snippet(framework: Optional[str], app_port: int = 3000) -> None:
    """Print framework-appropriate JS snippet to the console."""
    bridge_url = "http://localhost:8787"

    if framework in ("react", "next", "vite"):
        console.print(
            "\n  [dim]// In your root component (e.g. src/App.jsx or pages/_app.jsx):[/dim]\n"
            "  [bold cyan]import { useEffect } from 'react'[/bold cyan]\n"
            "\n  [bold cyan]useEffect(() => {[/bold cyan]\n"
            f"  [bold cyan]  const script = document.createElement('script')[/bold cyan]\n"
            f"  [bold cyan]  script.src = '{bridge_url}/sdk.js'[/bold cyan]\n"
            f"  [bold cyan]  script.onload = () => new window.RecordLoop({{ endpoint: '{bridge_url}' }})[/bold cyan]\n"
            "  [bold cyan]  document.body.appendChild(script)[/bold cyan]\n"
            "  [bold cyan]}, [])[/bold cyan]\n"
        )
    elif framework == "vue":
        console.print(
            "\n  [dim]// In your main.js or App.vue mounted() hook:[/dim]\n"
            f"  [bold cyan]const s = document.createElement('script')[/bold cyan]\n"
            f"  [bold cyan]s.src = '{bridge_url}/sdk.js'[/bold cyan]\n"
            f"  [bold cyan]s.onload = () => new window.RecordLoop({{ endpoint: '{bridge_url}' }})[/bold cyan]\n"
            "  [bold cyan]document.body.appendChild(s)[/bold cyan]\n"
        )
    else:
        # Plain HTML snippet that works for all frameworks.
        console.print(
            f"\n  [dim]<!-- Add before </body> in your HTML -->[/dim]\n"
            f"  [bold cyan]<script src=\"{bridge_url}/sdk.js\"></script>[/bold cyan]\n"
            f"  [bold cyan]<script>[/bold cyan]\n"
            f"  [bold cyan]  const rl = new RecordLoop({{ endpoint: '{bridge_url}' }})[/bold cyan]\n"
            f"  [bold cyan]</script>[/bold cyan]\n"
        )


# ---------------------------------------------------------------------------
# `recordloop mcp`
# ---------------------------------------------------------------------------

@app.command()
def mcp(
    sessions_dir: Path = typer.Option(
        _DEFAULT_SESSIONS_DIR,
        "--sessions-dir",
        help="Directory to expose to the MCP server.",
        show_default=True,
    ),
    port: int = typer.Option(
        _DEFAULT_PORT,
        "--port", "-p",
        help="Port hint passed through to the MCP server.",
        show_default=True,
    ),
) -> None:
    """Start the MCP server for Claude Code / Cursor integration.

    Exposes recorded sessions as MCP tools so that AI assistants can query,
    diff, and analyse recordings directly.

    Requires the [bold]recordloop[mcp][/bold] extra:

        pip install recordloop[mcp]
    """
    try:
        from recordloop.mcp import start_mcp_server  # type: ignore[import]
    except ImportError:
        err_console.print(
            "\n[bold red]Missing dependency:[/bold red] "
            "The [bold]recordloop\\[mcp][/bold] extra is required for MCP integration.\n"
            "\n  Install it with:\n"
            "\n    [bold cyan]pip install 'recordloop[mcp]'[/bold cyan]\n"
            "\nOnce installed, add the following to your Claude Code / Cursor config:\n"
            "\n    [dim]{[/dim]\n"
            '    [dim]  "mcpServers": {[/dim]\n'
            '    [dim]    "recordloop": {[/dim]\n'
            '    [dim]      "command": "recordloop",[/dim]\n'
            '    [dim]      "args": ["mcp"][/dim]\n'
            "    [dim]    }[/dim]\n"
            "    [dim]  }[/dim]\n"
            "    [dim]}[/dim]\n"
        )
        raise typer.Exit(1)

    sessions_dir = Path(sessions_dir).resolve()

    console.rule("[bold green]RecordLoop MCP Server")
    console.print(f"  Sessions dir : [cyan]{sessions_dir}[/cyan]")
    console.print(f"  Bridge port  : [cyan]{port}[/cyan]")
    console.print()
    console.print("  Waiting for MCP requests…  [bold]Ctrl+C[/bold] to stop.\n")

    try:
        start_mcp_server(sessions_dir=sessions_dir, port=port)
    except KeyboardInterrupt:
        console.print("\n[yellow]MCP server stopped.[/yellow]")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app()
