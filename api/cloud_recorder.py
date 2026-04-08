"""
Cloud Playwright runner.

Takes AI-generated InteractionFlows and records each one against a live
preview URL. Returns video paths (and optionally S3 URLs).
"""

import re
import subprocess
import time
from pathlib import Path
from typing import Optional

from .analyzer import InteractionFlow, InteractionStep

VIDEO_DIR = "/tmp/recordloop-videos"


def record_flows(flows: list[InteractionFlow], preview_url: str) -> list[dict]:
    """
    Record each interaction flow with Playwright.

    Returns a list of result dicts:
      [{ name, description, status, video, video_url, actions, error }]

    Playwright is imported lazily so the module can be loaded in environments
    that don't have it installed (e.g. Tier-1 CI smoke jobs that never call
    record_flows).
    """
    return [_record_one(flow, preview_url) for flow in flows]


def _record_one(flow: InteractionFlow, preview_url: str) -> dict:
    # Lazy import — only pulled in when an actual recording is requested.
    from recordloop.capture.recorder import PlaywrightRecorder, RecorderConfig

    config = RecorderConfig(
        base_url=preview_url,
        video_dir=VIDEO_DIR,
        headless=True,
        slow_mo=0,
    )

    result = {
        "name": flow.name,
        "description": flow.description,
        "component_file": flow.component_file,
        "status": "recording",
        "video": None,
        "video_url": None,
        "actions": 0,
    }

    try:
        with PlaywrightRecorder(config) as recorder:
            start = flow.navigate_to or "/"
            if not start.startswith("http"):
                start = preview_url.rstrip("/") + "/" + start.lstrip("/")

            page = recorder.start_recording(start)
            page.wait_for_load_state("domcontentloaded")
            time.sleep(0.3)

            recorded = 0
            for step in flow.steps:
                try:
                    _execute(page, recorder, step)
                    recorded += 1
                    time.sleep(0.25)
                except Exception as e:
                    print(f"[cloud-recorder] step skipped ({step.action} {step.selector}): {e}")

            recorder.stop_recording()

            video_path = recorder.get_video_path()
            if video_path:
                result["video"] = str(video_path)
                gif_path = _make_preview_gif(Path(video_path))
                if gif_path:
                    result["gif"] = str(gif_path)

            result["status"] = "done"
            result["actions"] = recorded

    except Exception as e:
        result["status"] = "failed"
        result["error"] = str(e)

    return result


def _execute(page, recorder, step: InteractionStep):
    """Execute one step on the page and record it."""
    from recordloop.core.session import ActionType  # lazy

    action = step.action.lower()
    sel = step.selector
    val = step.value

    match action:
        case "navigate":
            url = val or sel
            # Guard against the LLM emitting a CSS selector instead of a URL.
            if not url.startswith(("http://", "https://", "/")):
                raise ValueError(f"navigate step has non-URL target {url!r} — skipping")
            page.goto(url, wait_until="domcontentloaded")
            recorder.record_navigate(url)

        case "click":
            page.click(sel, timeout=8000)
            recorder.record_click(_to_key(sel))

        case "fill" | "type":
            page.fill(sel, val or "", timeout=8000)
            recorder.record_type(_to_key(sel), val or "")

        case "select":
            page.select_option(sel, val or "", timeout=8000)
            recorder.record_action(ActionType.SELECT, key=_to_key(sel), value=val)

        case "wait":
            page.wait_for_selector(sel, timeout=8000)
            recorder.record_action(ActionType.WAIT, key=_to_key(sel))

        case "hover":
            page.hover(sel, timeout=8000)
            recorder.record_action(ActionType.HOVER, key=_to_key(sel))


# ---------------------------------------------------------------------------
# Selector → SemanticKey bridge
# ---------------------------------------------------------------------------

# The LLM emits CSS-ish selector strings; the recorder stores SemanticKeys.
# Parse the common shapes and fall back to xpath for anything exotic. The
# raw string is kept on the Playwright call itself, so this conversion only
# affects the recorded session metadata.

_RE_TESTID = re.compile(r"""\[data-test(?:-)?id=['"]?([^'"\]]+)['"]?\]""")
_RE_NAME = re.compile(r"""\[name=['"]?([^'"\]]+)['"]?\]""")
_RE_ARIA = re.compile(r"""\[aria-label=['"]?([^'"\]]+)['"]?\]""")


def _make_preview_gif(mp4_path: Path) -> Optional[Path]:
    """Convert an MP4 to a palette-optimised GIF for inline GitHub markdown display.

    Caps at 20 s, 8 fps, 640 px wide — keeps file size reasonable while
    remaining readable in a PR comment.
    """
    gif_path = mp4_path.with_suffix(".gif")
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-t", "20",
                "-i", str(mp4_path),
                "-vf", (
                    "fps=8,"
                    "scale=640:-1:flags=lanczos,"
                    "split[s0][s1];"
                    "[s0]palettegen=max_colors=128[p];"
                    "[s1][p]paletteuse=dither=bayer"
                ),
                "-loop", "0",
                str(gif_path),
            ],
            capture_output=True,
            timeout=120,
        )
        if result.returncode == 0 and gif_path.exists() and gif_path.stat().st_size > 0:
            size_kb = gif_path.stat().st_size // 1024
            print(f"[cloud-recorder] GIF created: {gif_path.name} ({size_kb} KB)")
            return gif_path
        print(f"[cloud-recorder] GIF conversion failed (rc={result.returncode})")
    except Exception as e:
        print(f"[cloud-recorder] GIF conversion error: {e}")
    return None


def _to_key(selector: str):
    """Best-effort conversion from a CSS selector string to a SemanticKey."""
    from recordloop.core.session import SemanticKey  # lazy

    s = (selector or "").strip()
    if not s:
        return SemanticKey(strategy="xpath", value="//*")

    if s.startswith("#"):
        return SemanticKey(strategy="id", value=s[1:])

    m = _RE_TESTID.search(s)
    if m:
        return SemanticKey(strategy="testid", value=m.group(1))

    m = _RE_NAME.search(s)
    if m:
        return SemanticKey(strategy="name", value=m.group(1))

    m = _RE_ARIA.search(s)
    if m:
        return SemanticKey(strategy="aria_label", value=m.group(1))

    return SemanticKey(strategy="xpath", value=s)
