"""
Cloud Playwright runner.

Takes AI-generated InteractionFlows and records each one against a live
preview URL. Returns video paths (and optionally S3 URLs).
"""

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .analyzer import InteractionFlow, InteractionStep

VIDEO_DIR = "/tmp/recordloop-videos"
MAX_VIEWPORTS = 4

_MOBILE_USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
    "Mobile/15E148 Safari/604.1"
)


@dataclass(frozen=True)
class ViewportProfile:
    name: str
    label: str
    width: int
    height: int
    is_mobile: bool = False
    has_touch: bool = False
    device_scale_factor: float | None = None
    user_agent: str = ""


_VIEWPORT_PRESETS: dict[str, ViewportProfile] = {
    "desktop": ViewportProfile("desktop", "Desktop", 1280, 720),
    "mobile": ViewportProfile(
        "mobile",
        "Mobile",
        390,
        844,
        is_mobile=True,
        has_touch=True,
        device_scale_factor=3,
        user_agent=_MOBILE_USER_AGENT,
    ),
    "tablet": ViewportProfile(
        "tablet",
        "Tablet",
        768,
        1024,
        has_touch=True,
        device_scale_factor=2,
    ),
    "tall": ViewportProfile("tall", "Tall", 1280, 1600),
}

_VIEWPORT_ALIASES = {
    "phone": "mobile",
    "iphone": "mobile",
    "desktop-tall": "tall",
    "long": "tall",
}


def _resolve_viewports(viewports: list[str] | str | None = None) -> list[ViewportProfile]:
    """Resolve named/custom viewport specs into concrete Playwright profiles."""
    if viewports is None or viewports == "":
        parts = ["desktop"]
    elif isinstance(viewports, str):
        parts = [p.strip() for p in viewports.split(",") if p.strip()]
    else:
        parts = []
        for item in viewports:
            parts.extend(p.strip() for p in str(item).split(",") if p.strip())

    if not parts:
        parts = ["desktop"]

    deduped: list[str] = []
    seen: set[str] = set()
    for part in parts:
        key = _VIEWPORT_ALIASES.get(part.lower(), part.lower())
        if key not in seen:
            deduped.append(part)
            seen.add(key)

    if len(deduped) > MAX_VIEWPORTS:
        raise ValueError(f"at most {MAX_VIEWPORTS} viewport profiles can be recorded per job")

    profiles: list[ViewportProfile] = []
    for raw in deduped:
        key = _VIEWPORT_ALIASES.get(raw.lower(), raw.lower())
        preset = _VIEWPORT_PRESETS.get(key)
        if preset:
            profiles.append(preset)
            continue

        m = re.fullmatch(r"(\d{3,4})x(\d{3,4})", key)
        if not m:
            valid = ", ".join(sorted(_VIEWPORT_PRESETS))
            raise ValueError(
                f"unknown viewport profile {raw!r}; use one of {valid} or WIDTHxHEIGHT"
            )
        width, height = int(m.group(1)), int(m.group(2))
        if not (320 <= width <= 2560 and 480 <= height <= 3000):
            raise ValueError(
                f"viewport {raw!r} is outside the supported range "
                "(width 320-2560, height 480-3000)"
            )
        profiles.append(ViewportProfile(f"{width}x{height}", f"{width}x{height}", width, height))

    return profiles


def record_flows(
    flows: list[InteractionFlow],
    preview_url: str,
    base_url: str = "",  # accepted for backwards compat but no longer used
    *,
    storage_state: dict | None = None,
    viewports: list[str] | str | None = None,
    wait_until: str = "networkidle",
    settle_ms: int = 300,
) -> list[dict]:
    """Record each interaction flow against the preview URL.

    One clean recording per flow. base_url is intentionally ignored — we used
    to also record the base branch for a before/after table, but it doubled
    runtime for marginal value and the agent now generates a single targeted
    flow per PR anyway.
    """
    profiles = _resolve_viewports(viewports)
    results: list[dict] = []
    for flow in flows:
        for profile in profiles:
            results.append(
                _record_one(
                    flow,
                    preview_url,
                    label=profile.name,
                    storage_state=storage_state,
                    viewport=profile,
                    wait_until=wait_until,
                    settle_ms=settle_ms,
                )
            )
    return results


def _record_one(
    flow: InteractionFlow,
    preview_url: str,
    label: str = "after",
    *,
    storage_state: dict | None = None,
    viewport: ViewportProfile | None = None,
    wait_until: str = "networkidle",
    settle_ms: int = 300,
) -> dict:
    # Lazy import — only pulled in when an actual recording is requested.
    from recordloop.capture.recorder import PlaywrightRecorder, RecorderConfig

    profile = viewport or _VIEWPORT_PRESETS["desktop"]
    settle_seconds = max(0, min(int(settle_ms or 0), 10_000)) / 1000

    # No slow_mo: Playwright's natural action timing reads as smooth in
    # the recording. slow_mo=200 inserted hitches between every operation
    # which made the GIF look choppy.
    config = RecorderConfig(
        base_url=preview_url,
        video_dir=VIDEO_DIR,
        headless=True,
        viewport_width=profile.width,
        viewport_height=profile.height,
        is_mobile=profile.is_mobile,
        has_touch=profile.has_touch,
        device_scale_factor=profile.device_scale_factor,
        user_agent=profile.user_agent,
        storage_state=storage_state,
    )

    result = {
        "name": flow.name,
        "description": flow.description,
        "component_file": flow.component_file,
        "status": "recording",
        "video": None,
        "video_url": None,
        # GitHub strips <video> tags from arbitrary CDNs but renders animated
        # GIFs inline via <img>. We build a step-by-step slideshow GIF from
        # page screenshots so reviewers see the flow without leaving the PR.
        "gif": None,
        "gif_url": None,
        "viewport": profile.name,
        "viewport_label": profile.label,
        "viewport_width": profile.width,
        "viewport_height": profile.height,
        # Assertion outcomes — used by run_action.py to render pass/fail.
        "assertions_total": 0,
        "assertions_passed": 0,
        "assertion_failures": [],  # list of {selector, value, reason}
        # Interaction outcomes (excluding assertions).
        "interactions_total": 0,
        "interactions_done": 0,
        "interaction_failures": [],
    }

    frames_png: list[bytes] = []

    def snap():
        """Best-effort screenshot for the slideshow GIF. Never raises."""
        try:
            frames_png.append(page.screenshot(type="png", full_page=False))
        except Exception:
            pass

    try:
        with PlaywrightRecorder(config) as recorder:
            start = flow.navigate_to or "/"
            if not start.startswith("http"):
                start = preview_url.rstrip("/") + "/" + start.lstrip("/")

            page = recorder.start_recording(start)
            _wait_for_ready_state(page, wait_until=wait_until)
            # Wait for the initial page load to settle.
            time.sleep(settle_seconds)
            snap()  # opening frame of the slideshow

            for i, step in enumerate(flow.steps):
                is_assertion = step.is_assertion
                if is_assertion:
                    result["assertions_total"] += 1
                else:
                    result["interactions_total"] += 1

                # The agent almost always emits navigate as the first step
                # even though we're already on that page from start_recording.
                # Skip the redundant initial navigate so the recording doesn't
                # double-load.
                if i == 0 and step.action.lower() == "navigate":
                    result["interactions_done"] += 1
                    continue

                try:
                    _execute(page, recorder, step, preview_url)
                    if is_assertion:
                        result["assertions_passed"] += 1
                    else:
                        result["interactions_done"] += 1
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

                # Snapshot after each step (success OR failure) — reviewers
                # benefit from seeing the state when an assertion failed too.
                snap()

            # Hold on the final state so viewers can read it.
            time.sleep(1.0)
            snap()  # closing frame

            recorder.stop_recording()

            video_path = recorder.get_video_path()
            if video_path:
                result["video"] = str(video_path)

            # Build the slideshow GIF. Failures here are non-fatal — the
            # .webm link still works, the reviewer just loses inline preview.
            try:
                from api.gif_builder import build_gif
                gif_path = Path(VIDEO_DIR) / f"{flow.name}-{label}.gif"
                if build_gif(frames_png, gif_path):
                    result["gif"] = str(gif_path)
            except Exception as e:
                print(f"[cloud-recorder] GIF build failed: {e}")

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


def _wait_for_ready_state(page, wait_until: str = "networkidle") -> None:
    """Wait for the page to be record-ready without hanging on busy apps."""
    state = (wait_until or "networkidle").lower()
    if state not in {"networkidle", "load", "domcontentloaded"}:
        state = "networkidle"

    if state == "networkidle":
        # Pages with streaming media, WebGL, or infinite animations never
        # reach networkidle. Try it first, then fall back gracefully.
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
            return
        except Exception:
            state = "load"

    timeout = 5000 if state == "load" else 3000
    try:
        page.wait_for_load_state(state, timeout=timeout)
    except Exception:
        pass


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

        case "hover":
            page.hover(sel, timeout=8000)
            recorder.record_action(ActionType.HOVER, key=_to_key(sel))

        case "scroll":
            # Scroll a specific element into view, or scroll to the bottom.
            # Use instant positioning — smooth scroll is async in the browser
            # and Playwright can't know when it finishes.
            if sel and sel.lower() not in ("body", "html", "window", "page", "bottom"):
                page.locator(sel).scroll_into_view_if_needed(timeout=8000)
            else:
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            recorder.record_action(ActionType.SCROLL, value=step.selector)

        case "wait":
            # Explicit pause emitted by the agent when it needs animations
            # to settle. Value is seconds (float); selector is optional element
            # to wait for visibility. Default: 1s.
            duration = float(val or 1.0)
            if sel:
                try:
                    page.wait_for_selector(sel, state="visible", timeout=int(duration * 1000 + 5000))
                except Exception:
                    pass
            time.sleep(duration)
            recorder.record_action(ActionType.WAIT, key=_to_key(sel) if sel else None)

        # -------- assertions --------------------------------------------------
        # These raise on failure (caught by the loop in _record_one) and the
        # raised reason flows into result["assertion_failures"]. They DO NOT
        # call recorder.record_* — assertions are post-hoc oracles, not user
        # actions, so they shouldn't appear in the SemanticKey trail.

        case "assert_text":
            loc = page.locator(sel)
            loc.scroll_into_view_if_needed(timeout=8000)
            actual = (loc.text_content() or "").strip()
            expected = (val or "").strip()
            if not expected:
                raise AssertionError("assert_text requires a non-empty value")
            if expected not in actual:
                raise AssertionError(
                    f"text mismatch — expected substring {expected!r} in {actual[:120]!r}"
                )

        case "assert_attribute":
            raw = val or ""
            if "=" not in raw:
                raise AssertionError(
                    f"assert_attribute value must be 'attr=expected', got {raw!r}"
                )
            attr, _, expected = raw.partition("=")
            attr = attr.strip()
            expected = expected.strip()
            loc = page.locator(sel)
            loc.scroll_into_view_if_needed(timeout=8000)
            actual = loc.get_attribute(attr)
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
            loc = page.locator(sel)
            loc.scroll_into_view_if_needed(timeout=8000)
            loc.wait_for(state="visible", timeout=8000)

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
