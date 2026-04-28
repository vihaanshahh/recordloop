"""
recordloop.config.settings
~~~~~~~~~~~~~~~~~~~~~~~~~~
Environment-aware configuration for RecordLoop.

Reads from environment variables (RECORDLOOP_* prefix), optional .env files,
and auto-detects the frontend framework from package.json.

Priority order (highest to lowest):
  1. Explicit environment variables (RECORDLOOP_*)
  2. Values from .env file (loaded into os.environ if not already set)
  3. Framework-derived defaults (port inferred from detected framework)
  4. Hard-coded fallback defaults
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Framework port / dev-command registry
# ---------------------------------------------------------------------------

FRAMEWORK_DEFAULTS: dict[str, dict[str, object]] = {
    "react":     {"port": 3000, "dev_cmd": "npm start"},
    "next":      {"port": 3000, "dev_cmd": "npm run dev"},
    "vite":      {"port": 5173, "dev_cmd": "npm run dev"},
    "vue":       {"port": 5173, "dev_cmd": "npm run dev"},
    "nuxt":      {"port": 3000, "dev_cmd": "npm run dev"},
    "angular":   {"port": 4200, "dev_cmd": "ng serve"},
    "svelte":    {"port": 5173, "dev_cmd": "npm run dev"},
    "gatsby":    {"port": 8000, "dev_cmd": "gatsby develop"},
    "remix":     {"port": 3000, "dev_cmd": "npm run dev"},
    "astro":     {"port": 4321, "dev_cmd": "npm run dev"},
    "storybook": {"port": 6006, "dev_cmd": "npx storybook dev -p 6006"},
}


# ---------------------------------------------------------------------------
# Framework detection
# ---------------------------------------------------------------------------

def _has_storybook_config(project_dir: Path) -> bool:
    """True if any ``.storybook/main.{js,ts,mjs,cjs,tsx}`` file exists below
    *project_dir* (excluding ``node_modules``).

    A package.json with ``@storybook/*`` in its deps is *not* sufficient — many
    apps include Storybook for component dev but want their main app served in
    CI. We therefore require an explicit config directory.
    """
    if not project_dir.is_dir():
        return False
    suffixes = ("main.js", "main.ts", "main.mjs", "main.cjs", "main.tsx")
    for path in project_dir.rglob("*"):
        # rglob walks node_modules — short-circuit on it.
        try:
            if "node_modules" in path.parts:
                continue
        except OSError:
            continue
        if path.is_file() and path.parent.name == ".storybook" and path.name in suffixes:
            return True
    return False


def detect_framework(project_dir: str = ".") -> Optional[str]:
    """Detect the frontend framework by inspecting *package.json*.

    Returns the framework name (e.g. ``"react"``, ``"vue"``) or ``None`` when
    no package.json is found or no recognised framework is listed.

    The check order matters: a Storybook config dir wins over everything else
    (Storybook auto-detection in the action invokes ``storybook build``
    instead of ``npm start``). Then more-specific meta-frameworks (Next, Nuxt,
    …) are probed before the generic base libraries (React, Vue, …) so that a
    Next.js project is reported as ``"next"`` rather than ``"react"``.

    When a Vite build tool is detected alongside React, Vue, or Svelte the
    function returns ``"vite"`` so that the correct default dev-server port
    (5173) is used instead of the framework's own default.
    """
    pkg_path = Path(project_dir) / "package.json"
    if not pkg_path.exists():
        return None

    # Storybook config dir wins regardless of other deps. This mirrors the
    # action.yml auto-start branch: an explicit .storybook/main.* file is the
    # clearest signal that this repo wants Storybook served, not the app.
    if _has_storybook_config(Path(project_dir)):
        return "storybook"

    try:
        pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    all_deps: dict[str, str] = {}
    all_deps.update(pkg.get("dependencies", {}))
    all_deps.update(pkg.get("devDependencies", {}))

    # Ordered: specific meta-frameworks first, then generic base libs.
    checks: list[tuple[str, str]] = [
        ("next",    "next"),
        ("nuxt",    "nuxt"),
        ("gatsby",  "gatsby"),
        ("remix",   "@remix-run/react"),
        ("astro",   "astro"),
        ("svelte",  "svelte"),
        ("angular", "@angular/core"),
        ("vue",     "vue"),
        ("react",   "react"),
    ]

    has_vite = "vite" in all_deps

    for name, dep in checks:
        if dep in all_deps:
            # Vite-powered React/Vue/Svelte: report "vite" for the correct port.
            if has_vite and name in ("react", "vue", "svelte"):
                return "vite"
            return name

    return None


# ---------------------------------------------------------------------------
# .env loader (no external dependency)
# ---------------------------------------------------------------------------

def load_env(filepath: str = ".env") -> dict[str, str]:
    """Parse a ``.env`` file and return its contents as a plain dict.

    Lines beginning with ``#`` and blank lines are ignored.  Values may be
    quoted with single or double quotes; the quotes are stripped.  The file is
    *not* written into ``os.environ`` — callers decide whether to merge.

    Returns an empty dict when the file does not exist.
    """
    env: dict[str, str] = {}
    path = Path(filepath)
    if not path.exists():
        return env

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        if key:
            env[key] = value

    return env


# ---------------------------------------------------------------------------
# RecordLoopSettings dataclass
# ---------------------------------------------------------------------------

@dataclass
class RecordLoopSettings:
    """Unified settings object for the RecordLoop toolchain.

    Fields
    ------
    base_url:
        The origin of the application under test, e.g. ``http://localhost:3000``.
    port:
        Bridge server port.  Default: 8787.
    sessions_dir:
        Directory where session JSON files are stored.
    video_dir:
        Directory where replay videos are written.
    headless:
        Run Playwright in headless mode when replaying.
    slow_mo:
        Milliseconds of delay between Playwright actions (useful for debugging).
    viewport_width:
        Browser viewport width in pixels.
    viewport_height:
        Browser viewport height in pixels.
    framework:
        Detected or configured frontend framework name (e.g. ``"react"``).
        Empty string when unknown.
    """

    base_url: str = ""
    port: int = 8787
    sessions_dir: str = ".recordloop/sessions"
    video_dir: str = ".recordloop/videos"
    headless: bool = True
    slow_mo: int = 0
    viewport_width: int = 1280
    viewport_height: int = 720
    framework: str = ""


# ---------------------------------------------------------------------------
# Factory: get_settings()
# ---------------------------------------------------------------------------

def get_settings(project_dir: str = ".") -> RecordLoopSettings:
    """Build a :class:`RecordLoopSettings` from the environment.

    Loading order:

    1. Read ``.env`` from *project_dir*, merging any keys not already in
       ``os.environ`` (so explicit env vars always win).
    2. Auto-detect the framework from ``package.json`` when
       ``RECORDLOOP_FRAMEWORK`` is not set.
    3. Derive ``base_url`` from ``RECORDLOOP_BASE_URL``, or fall back to the
       framework's default port (or port 3000 as a last resort).
    4. Apply all remaining ``RECORDLOOP_*`` overrides.
    """
    # Step 1: merge .env file into process env without clobbering.
    dotenv_path = Path(project_dir) / ".env"
    for key, value in load_env(str(dotenv_path)).items():
        os.environ.setdefault(key, value)

    # Step 2: framework detection.
    framework = os.environ.get(
        "RECORDLOOP_FRAMEWORK",
        detect_framework(project_dir) or "",
    )

    # Step 3: base_url resolution.
    base_url = os.environ.get("RECORDLOOP_BASE_URL", "")
    if not base_url:
        fw_defaults = FRAMEWORK_DEFAULTS.get(framework, {})
        fw_port = int(fw_defaults.get("port", 3000))  # type: ignore[arg-type]
        app_port = int(os.environ.get("PORT", fw_port))
        base_url = f"http://localhost:{app_port}"

    # Step 4: remaining settings from env.
    port = int(os.environ.get("RECORDLOOP_PORT", 8787))
    sessions_dir = os.environ.get("RECORDLOOP_SESSIONS_DIR", ".recordloop/sessions")
    video_dir = os.environ.get("RECORDLOOP_VIDEO_DIR", ".recordloop/videos")
    headless = os.environ.get("RECORDLOOP_HEADLESS", "true").lower() in ("true", "1", "yes")
    slow_mo = int(os.environ.get("RECORDLOOP_SLOW_MO", 0))
    viewport_width = int(os.environ.get("RECORDLOOP_VIEWPORT_WIDTH", 1280))
    viewport_height = int(os.environ.get("RECORDLOOP_VIEWPORT_HEIGHT", 720))

    return RecordLoopSettings(
        base_url=base_url,
        port=port,
        sessions_dir=sessions_dir,
        video_dir=video_dir,
        headless=headless,
        slow_mo=slow_mo,
        viewport_width=viewport_width,
        viewport_height=viewport_height,
        framework=framework,
    )
