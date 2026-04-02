"""
Environment-aware configuration for RecordLoop.

Reads from environment variables (RECORDLOOP_*), .env files,
and auto-detects frontend framework from package.json.
"""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# Default ports for common frontend frameworks
FRAMEWORK_DEFAULTS = {
    "react": {"port": 3000, "dev_cmd": "npm start"},
    "next": {"port": 3000, "dev_cmd": "npm run dev"},
    "vite": {"port": 5173, "dev_cmd": "npm run dev"},
    "vue": {"port": 5173, "dev_cmd": "npm run dev"},
    "nuxt": {"port": 3000, "dev_cmd": "npm run dev"},
    "angular": {"port": 4200, "dev_cmd": "ng serve"},
    "svelte": {"port": 5173, "dev_cmd": "npm run dev"},
    "gatsby": {"port": 8000, "dev_cmd": "gatsby develop"},
    "remix": {"port": 3000, "dev_cmd": "npm run dev"},
    "astro": {"port": 4321, "dev_cmd": "npm run dev"},
}


def detect_framework(project_dir: str = ".") -> Optional[str]:
    """
    Detect the frontend framework from package.json.

    Returns the framework name (e.g. "react", "vue") or None.
    """
    pkg_path = Path(project_dir) / "package.json"
    if not pkg_path.exists():
        return None

    try:
        pkg = json.loads(pkg_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None

    all_deps = {}
    all_deps.update(pkg.get("dependencies", {}))
    all_deps.update(pkg.get("devDependencies", {}))

    # Order matters: check specific frameworks before generic ones
    checks = [
        ("next", "next"),
        ("nuxt", "nuxt"),
        ("gatsby", "gatsby"),
        ("remix", "@remix-run/react"),
        ("astro", "astro"),
        ("svelte", "svelte"),
        ("angular", "@angular/core"),
        ("vue", "vue"),
        ("react", "react"),
    ]

    # Check if vite is the bundler (common for Vue/React/Svelte)
    has_vite = "vite" in all_deps

    for name, dep in checks:
        if dep in all_deps:
            # If it's react/vue/svelte with vite, use vite's port
            if has_vite and name in ("react", "vue", "svelte"):
                return "vite"
            return name

    return None


def load_dotenv(filepath: str = ".env") -> dict[str, str]:
    """Load a .env file into a dict (no external dependency needed)."""
    env = {}
    path = Path(filepath)
    if not path.exists():
        return env

    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        env[key] = value

    return env


@dataclass
class RecordLoopConfig:
    """
    Unified config that loads from env vars, .env file, and framework detection.

    Priority: explicit kwargs > env vars > .env file > framework defaults > hardcoded defaults
    """

    base_url: str = ""
    video_dir: str = "test-videos"
    test_output_dir: str = "generated-tests"
    test_file_prefix: str = "test_recording"
    headless: bool = True
    slow_mo: int = 0
    viewport_width: int = 1280
    viewport_height: int = 720
    framework: str = ""
    project_dir: str = "."

    def __post_init__(self):
        # Load .env file into process env (don't overwrite existing)
        dotenv_path = Path(self.project_dir) / ".env"
        dotenv_vars = load_dotenv(str(dotenv_path))
        for k, v in dotenv_vars.items():
            os.environ.setdefault(k, v)

        # Auto-detect framework if not set
        if not self.framework:
            self.framework = os.environ.get(
                "RECORDLOOP_FRAMEWORK",
                detect_framework(self.project_dir) or "",
            )

        # Resolve base_url from env or framework defaults
        if not self.base_url:
            env_url = os.environ.get("RECORDLOOP_BASE_URL", "")
            if env_url:
                self.base_url = env_url
            else:
                defaults = FRAMEWORK_DEFAULTS.get(self.framework, {})
                port = int(
                    os.environ.get(
                        "RECORDLOOP_PORT",
                        os.environ.get("PORT", defaults.get("port", 3000)),
                    )
                )
                self.base_url = f"http://localhost:{port}"

        # Override remaining fields from env
        self.video_dir = os.environ.get("RECORDLOOP_VIDEO_DIR", self.video_dir)
        self.test_output_dir = os.environ.get(
            "RECORDLOOP_TEST_OUTPUT_DIR", self.test_output_dir
        )
        self.headless = os.environ.get(
            "RECORDLOOP_HEADLESS", str(self.headless)
        ).lower() in ("true", "1", "yes")
        self.slow_mo = int(os.environ.get("RECORDLOOP_SLOW_MO", self.slow_mo))
        self.viewport_width = int(
            os.environ.get("RECORDLOOP_VIEWPORT_WIDTH", self.viewport_width)
        )
        self.viewport_height = int(
            os.environ.get("RECORDLOOP_VIEWPORT_HEIGHT", self.viewport_height)
        )

    def to_recorder_config(self):
        """Convert to the existing RecorderConfig for backwards compat."""
        from .recorder import RecorderConfig

        return RecorderConfig(
            base_url=self.base_url,
            video_dir=self.video_dir,
            test_output_dir=self.test_output_dir,
            test_file_prefix=self.test_file_prefix,
            headless=self.headless,
            slow_mo=self.slow_mo,
            viewport_width=self.viewport_width,
            viewport_height=self.viewport_height,
        )

    def summary(self) -> str:
        """Human-readable config summary."""
        fw = self.framework or "unknown"
        lines = [
            f"Framework:  {fw}",
            f"Base URL:   {self.base_url}",
            f"Video dir:  {self.video_dir}",
            f"Output dir: {self.test_output_dir}",
            f"Headless:   {self.headless}",
            f"Viewport:   {self.viewport_width}x{self.viewport_height}",
        ]
        if self.slow_mo:
            lines.append(f"Slow-mo:    {self.slow_mo}ms")
        return "\n".join(lines)
