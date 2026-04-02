"""
Setup wizard for RecordLoop.

Run: python -m recordloop init

Detects your frontend framework, generates a .env file,
and prints a quick-start snippet.
"""

import sys
from pathlib import Path

from .config import RecordLoopConfig, detect_framework, FRAMEWORK_DEFAULTS


def init(project_dir: str = "."):
    """Auto-detect framework and write a .env config file."""
    project = Path(project_dir)
    framework = detect_framework(project_dir)

    print("RecordLoop Setup")
    print("=" * 40)

    if framework:
        defaults = FRAMEWORK_DEFAULTS.get(framework, {})
        port = defaults.get("port", 3000)
        dev_cmd = defaults.get("dev_cmd", "npm run dev")
        print(f"Detected: {framework}")
        print(f"Dev server: {dev_cmd} (port {port})")
    else:
        port = 3000
        dev_cmd = None
        print("No framework detected (no package.json or unknown deps).")
        print("Using defaults (localhost:3000).")

    # Write .env file
    env_path = project / ".env"
    if env_path.exists():
        print(f"\n.env already exists at {env_path}")
        print("Skipping .env generation. Edit it manually or delete to re-run.")
    else:
        env_content = _generate_env(framework or "", port)
        env_path.write_text(env_content)
        print(f"\nCreated {env_path}")

    # Create .recordloop/sessions/ directory
    from .runner import init_sessions_dir
    sessions_path = init_sessions_dir(str(project))
    print(f"Created {sessions_path}")

    # Ensure .gitignore has .env
    _ensure_gitignore(project)

    # Print quick-start
    print("\n" + "-" * 40)
    print("Quick start:\n")

    if dev_cmd:
        print(f"  1. Start your app:  {dev_cmd}")
    else:
        print("  1. Start your app on localhost:3000")

    print(f"\n  2. Add the JS SDK to your frontend:\n")
    print("     npm install recordloop")
    print("     # or copy js/dist/recordloop.js into your project\n")

    if framework in ("react", "vite", "next"):
        print("     // React")
        print("     import { RecordLoopProvider } from 'recordloop/react'")
        print("")
        print("     <RecordLoopProvider endpoint=\"http://localhost:8787\">")
        print("       <App />")
        print("     </RecordLoopProvider>")
    elif framework in ("vue", "nuxt"):
        print("     // Vue")
        print("     import { RecordLoopPlugin } from 'recordloop/vue'")
        print("     app.use(RecordLoopPlugin, { endpoint: 'http://localhost:8787' })")
    else:
        print("     // Any framework / vanilla JS")
        print("     import { RecordLoop } from 'recordloop'")
        print("     const rl = new RecordLoop({ endpoint: 'http://localhost:8787' })")
        print("     rl.start()")

    print(f"\n  3. Start the bridge server:\n")
    print("     python -m recordloop serve\n")
    print("     This receives sessions from the JS SDK, converts them")
    print("     to Playwright tests, and generates test code.")

    print(f"\n  4. View results:\n")
    print("     python -m recordloop report")
    print()


def _generate_env(framework: str, port: int) -> str:
    """Generate a .env file with sensible defaults."""
    lines = [
        "# RecordLoop Configuration",
        "# Auto-generated — edit as needed",
        "",
        f"RECORDLOOP_FRAMEWORK={framework}",
        f"RECORDLOOP_BASE_URL=http://localhost:{port}",
        f"RECORDLOOP_PORT={port}",
        "",
        "# Recording settings",
        "RECORDLOOP_VIDEO_DIR=test-videos",
        "RECORDLOOP_TEST_OUTPUT_DIR=generated-tests",
        "RECORDLOOP_HEADLESS=true",
        "RECORDLOOP_SLOW_MO=0",
        "",
        "# Viewport",
        "RECORDLOOP_VIEWPORT_WIDTH=1280",
        "RECORDLOOP_VIEWPORT_HEIGHT=720",
        "",
    ]
    return "\n".join(lines)


def _ensure_gitignore(project: Path):
    """Make sure .env is in .gitignore."""
    gitignore = project / ".gitignore"
    if gitignore.exists():
        content = gitignore.read_text()
        if ".env" not in content:
            with open(gitignore, "a") as f:
                f.write("\n.env\n")
            print("Added .env to .gitignore")
    else:
        gitignore.write_text(".env\n")
        print("Created .gitignore with .env entry")


def main():
    """CLI entry point."""
    args = sys.argv[1:]

    if not args or args[0] == "init":
        project_dir = args[1] if len(args) > 1 else "."
        init(project_dir)
    elif args[0] == "report":
        from .report import generate_report
        recordings_dir = args[1] if len(args) > 1 else "generated-tests"
        video_dir = args[2] if len(args) > 2 else "test-videos"
        output = generate_report(recordings_dir, video_dir)
        print(f"Report generated: {output}")
    elif args[0] == "serve":
        from .bridge import serve
        port = 8787
        for i, a in enumerate(args[1:], 1):
            if a == "--port" and i + 1 < len(args):
                port = int(args[i + 1])
        serve(port=port)
    elif args[0] == "run":
        from .runner import Runner
        import json as _json
        sessions_dir = ".recordloop/sessions"
        session_id = None
        output_json = False
        for i, a in enumerate(args[1:]):
            if a == "--sessions-dir" and i + 2 <= len(args[1:]):
                sessions_dir = args[i + 2]
            elif a == "--output" and i + 2 <= len(args[1:]) and args[i + 2] == "json":
                output_json = True
            elif not a.startswith("--"):
                session_id = a
        runner = Runner(sessions_dir=sessions_dir)
        results = runner.run(session_id)
        if output_json:
            print(_json.dumps(results, indent=2, default=str))
        else:
            for r in results:
                status = r.get("status", "unknown")
                sid = r.get("session_id", "?")
                print(f"  [{status}] {sid}")
                if r.get("video_url"):
                    print(f"    Video: {r['video_url']}")
                if r.get("video"):
                    print(f"    Local: {r['video']}")
                if r.get("test"):
                    print(f"    Test:  {r['test']}")
    elif args[0] == "setup-s3":
        from .storage import setup_bucket
        setup_bucket()
    elif args[0] == "config":
        config = RecordLoopConfig()
        print(config.summary())
    else:
        print("Usage:")
        print("  python -m recordloop init              — Setup config and session dir")
        print("  python -m recordloop serve             — Start bridge server (receives JS SDK)")
        print("  python -m recordloop run [session_id]  — Replay sessions, record video, upload")
        print("  python -m recordloop report            — Generate HTML report")
        print("  python -m recordloop setup-s3          — Create and configure S3 bucket")
        print("  python -m recordloop config            — Show current config")


if __name__ == "__main__":
    main()
