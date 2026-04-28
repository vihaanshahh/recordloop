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


def record_flows(
    flows: list[InteractionFlow],
    preview_url: str,
    base_url: str = "",  # accepted for backwards compat but no longer used
    *,
    storage_state: dict | None = None,
) -> list[dict]:
    """Record each interaction flow against the preview URL.

    One clean recording per flow. base_url is intentionally ignored — we used
    to also record the base branch for a before/after table, but it doubled
    runtime for marginal value and the agent now generates a single targeted
    flow per PR anyway.
    """
    return [
        _record_one(flow, preview_url, label="after", storage_state=storage_state)
        for flow in flows
    ]


def _record_one(
    flow: InteractionFlow,
    preview_url: str,
    label: str = "after",
    *,
    storage_state: dict | None = None,
) -> dict:
    # Lazy import — only pulled in when an actual recording is requested.
    from recordloop.capture.recorder import PlaywrightRecorder, RecorderConfig

    # No slow_mo: Playwright's natural action timing reads as smooth in
    # the recording. slow_mo=200 inserted hitches between every operation
    # which made the GIF look choppy.
    config = RecorderConfig(
        base_url=preview_url,
        video_dir=VIDEO_DIR,
        headless=True,
        storage_state=storage_state,
    )

    result = {
        "name": flow.name,
        "description": flow.description,
        "component_file": flow.component_file,
        "status": "recording",
        "video": None,
        "video_url": None,
        # Assertion outcomes — used by run_action.py to render pass/fail.
        "assertions_total": 0,
        "assertions_passed": 0,
        "assertion_failures": [],  # list of {selector, value, reason}
        # Interaction outcomes (excluding assertions).
        "interactions_total": 0,
        "interactions_done": 0,
        "interaction_failures": [],
    }

    try:
        with PlaywrightRecorder(config) as recorder:
            start = flow.navigate_to or "/"
            if not start.startswith("http"):
                start = preview_url.rstrip("/") + "/" + start.lstrip("/")

            page = recorder.start_recording(start)
            # Pages with streaming media, WebGL, or infinite animations never
            # reach networkidle. Try it first (gives cleaner frame for SPAs
            # that do have a quiescent state), but fall back gracefully.
            try:
                page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                try:
                    page.wait_for_load_state("load", timeout=5000)
                except Exception:
                    pass
            # Brief settle so the first frame isn't mid-paint.
            time.sleep(0.5)

            for i, step in enumerate(flow.steps):
                is_assertion = step.is_assertion
                if is_assertion:
                    result["assertions_total"] += 1
                else:
                    result["interactions_total"] += 1

                # The agent almost always emits navigate as the first step
                # even though we're already on that page from start_recording.
                # Skip the redundant initial navigate so the recording doesn't
                # double-load and so we don't fight Playwright's relative-URL
                # handling.
                if i == 0 and step.action.lower() == "navigate":
                    result["interactions_done"] += 1
                    continue

                try:
                    _execute(page, recorder, step, preview_url)
                    if is_assertion:
                        result["assertions_passed"] += 1
                    else:
                        result["interactions_done"] += 1
                    # Pause long enough that each interaction is visible and
                    # any triggered animations (scroll-reveal, hover states)
                    # have time to play out before the next step.
                    action_lower = step.action.lower()
                    if action_lower == "scroll":
                        time.sleep(1.2)  # let scroll-reveal animations play
                    elif action_lower == "navigate":
                        time.sleep(0.8)
                    else:
                        time.sleep(0.4)
                except Exception as e:
                    reason = str(e).splitlines()[0][:200]
                    print(f"[cloud-recorder] {step.action} {step.selector!r} failed: {reason}")
                    if is_assertion:
                        result["assertion_failures"].append({
                            "selector": step.selector,
                            "value": step.value or "",
                            "reason": reason,
                        })
                    else:
                        result["interaction_failures"].append({
                            "action": step.action,
                            "selector": step.selector,
                            "reason": reason,
                        })

            # Hold on the final state so viewers can read it.
            time.sleep(1.5)

            recorder.stop_recording()

            video_path = recorder.get_video_path()
            if video_path:
                result["video"] = str(video_path)
                gif_path = _make_preview_gif(Path(video_path), label=label)
                if gif_path:
                    result["gif"] = str(gif_path)

            # A flow passes only if every assertion passed AND we recorded
            # at least one assertion. A no-assertion flow is degraded to
            # "demo" status — recorded but not trusted.
            if result["assertions_total"] == 0:
                result["status"] = "demo"
            elif result["assertion_failures"]:
                result["status"] = "failed"
            else:
                result["status"] = "passed"

    except Exception as e:
        result["status"] = "failed"
        result["error"] = str(e)

    return result


def _normalize_selector(sel: str) -> str:
    """Normalize selectors the LLM emits into shapes Playwright understands.

    The model frequently writes a bare visible-text string as a selector
    (e.g. ``Click me``) when it means ``text=Click me``. Playwright treats
    bare strings as CSS, so the click silently times out. Detect that case
    and add the ``text=`` prefix. Recognised CSS / Playwright engine shapes
    pass through unchanged.
    """
    s = (sel or "").strip()
    if not s:
        return s
    # Already a Playwright engine selector or a CSS expression we know.
    if s.startswith((
        "text=", "css=", "xpath=", "id=", "data-testid=", "role=",
        "//", "#", ".", "[", ":", "*",
    )):
        return s
    # CSS-like tokens (tag, tag.class, tag#id) — no spaces, only CSS chars.
    if " " not in s and re.fullmatch(r"[A-Za-z][A-Za-z0-9_.#-]*", s):
        return s
    # Anything else with spaces / punctuation is almost certainly visible text.
    return f"text={s}"


def _execute(page, recorder, step: InteractionStep, preview_url: str = ""):
    """Execute one step on the page and record it."""
    from recordloop.core.session import ActionType  # lazy

    action = step.action.lower()
    val = step.value

    # Navigate is special: its "selector" field carries a URL path, not a
    # DOM selector, so we must NOT push it through _normalize_selector
    # (which would mistake "/" for visible text and turn it into "text=/").
    if action == "navigate":
        url = val or step.selector
        if not url.startswith(("http://", "https://", "/")):
            raise ValueError(f"navigate step has non-URL target {url!r} — skipping")
        # Resolve relative paths against preview_url; Playwright's page.goto
        # requires an absolute URL when no BrowserContext base_url is set.
        if url.startswith("/") and preview_url:
            url = preview_url.rstrip("/") + url
        page.goto(url, wait_until="domcontentloaded")
        recorder.record_navigate(url)
        return

    sel = _normalize_selector(step.selector)

    match action:

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

        case "scroll":
            # Scroll a specific element into view, or fall back to scrolling
            # to the bottom of the page when selector is empty/generic.
            if sel and sel.lower() not in ("body", "html", "window", "page", "bottom"):
                page.locator(sel).scroll_into_view_if_needed(timeout=8000)
            else:
                page.evaluate(
                    "window.scrollTo({top: document.body.scrollHeight, behavior: 'smooth'})"
                )
            recorder.record_action(ActionType.SCROLL, value=step.selector)

        # -------- assertions --------------------------------------------------
        # These raise on failure (caught by the loop in _record_one) and the
        # raised reason flows into result["assertion_failures"]. They DO NOT
        # call recorder.record_* — assertions are post-hoc oracles, not user
        # actions, so they shouldn't appear in the SemanticKey trail.

        case "assert_text":
            actual = (page.text_content(sel, timeout=8000) or "").strip()
            expected = (val or "").strip()
            if not expected:
                raise AssertionError("assert_text requires a non-empty value")
            if expected not in actual:
                raise AssertionError(
                    f"text mismatch — expected substring {expected!r} in {actual[:120]!r}"
                )

        case "assert_attribute":
            # value format: "attr_name=expected_substring"
            raw = val or ""
            if "=" not in raw:
                raise AssertionError(
                    f"assert_attribute value must be 'attr=expected', got {raw!r}"
                )
            attr, _, expected = raw.partition("=")
            attr = attr.strip()
            expected = expected.strip()
            page.wait_for_selector(sel, timeout=8000)
            actual = page.get_attribute(sel, attr)
            if actual is None:
                raise AssertionError(f"attribute {attr!r} not present on {sel!r}")
            if expected not in actual:
                raise AssertionError(
                    f"{attr} mismatch — expected substring {expected!r} in {actual!r}"
                )

        case "assert_url":
            expected = (val or step.selector or "").strip()
            actual = page.url
            if not expected:
                raise AssertionError("assert_url requires a non-empty value")
            if expected not in actual:
                raise AssertionError(
                    f"url mismatch — expected substring {expected!r} in {actual!r}"
                )

        case "assert_visible":
            page.wait_for_selector(sel, state="visible", timeout=8000)

        case _:
            raise ValueError(f"unknown action {step.action!r}")


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


def _make_preview_gif(mp4_path: Path, label: str = "after") -> Optional[Path]:
    """Convert an MP4 to a palette-optimised GIF for inline GitHub markdown display.

    Caps at 20 s, 8 fps, 640 px wide — keeps file size reasonable while
    remaining readable in a PR comment.
    """
    gif_path = mp4_path.with_name(f"{mp4_path.stem}-{label}.gif")
    try:
        # 15 fps + full 256-colour palette + sierra2_4a dither = noticeably
        # smoother motion than 8 fps / bayer. Cap at 15s to keep file size
        # reasonable when paired with the higher frame rate.
        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-t", "20",
                "-i", str(mp4_path),
                "-vf", (
                    "fps=15,"
                    "scale=720:-1:flags=lanczos,"
                    "split[s0][s1];"
                    "[s0]palettegen=stats_mode=diff[p];"
                    "[s1][p]paletteuse=dither=sierra2_4a:diff_mode=rectangle"
                ),
                "-loop", "0",
                str(gif_path),
            ],
            capture_output=True,
            timeout=180,
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
