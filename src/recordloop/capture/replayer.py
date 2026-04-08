"""
recordloop.capture.replayer
~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Replay a :class:`~recordloop.core.session.Session` through a real browser
using Playwright and capture the result as video.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from playwright.sync_api import sync_playwright, Page

from recordloop.core.session import Action, ActionType, SemanticKey, Session
from .recorder import RecorderConfig
from .video import ensure_mp4

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class ReplayResult:
    """Outcome of replaying a :class:`~recordloop.core.session.Session`."""

    session_id: str
    success: bool
    video_path: str | None
    error: str | None
    duration_ms: int


# ---------------------------------------------------------------------------
# SemanticKey → Playwright locator
# ---------------------------------------------------------------------------

def _key_to_locator(page: Page, key: SemanticKey):
    """Translate a :class:`~recordloop.core.session.SemanticKey` to a Playwright locator."""
    match key.strategy:
        case "testid":
            return page.get_by_test_id(key.value)
        case "id":
            return page.locator(f"#{key.value}")
        case "name":
            return page.locator(f'[name="{key.value}"]')
        case "aria_label":
            return page.get_by_label(key.value)
        case "role_text":
            if key.tag:
                return page.get_by_role(key.tag, name=key.value)  # type: ignore[arg-type]
            return page.get_by_text(key.value)
        case "xpath":
            return page.locator(f"xpath={key.value}")
        case _:
            return page.locator(key.value)


# ---------------------------------------------------------------------------
# Per-action replay
# ---------------------------------------------------------------------------

def _replay_action(page: Page, action: Action) -> None:
    """Execute a single *action* on *page*.

    Raises whatever Playwright raises — the caller is responsible for catching.
    """
    match action.type:
        case ActionType.NAVIGATE:
            if action.value:
                page.goto(action.value, wait_until="domcontentloaded")

        case ActionType.CLICK:
            if action.key:
                _key_to_locator(page, action.key).click(timeout=5000)

        case ActionType.DBLCLICK:
            if action.key:
                _key_to_locator(page, action.key).dblclick(timeout=5000)

        case ActionType.TYPE | ActionType.FILL:
            if action.key and action.value is not None:
                _key_to_locator(page, action.key).fill(action.value, timeout=5000)

        case ActionType.SELECT:
            if action.key and action.value is not None:
                _key_to_locator(page, action.key).select_option(
                    action.value, timeout=5000
                )

        case ActionType.CHECK:
            if action.key:
                _key_to_locator(page, action.key).check(timeout=5000)

        case ActionType.UNCHECK:
            if action.key:
                _key_to_locator(page, action.key).uncheck(timeout=5000)

        case ActionType.HOVER:
            if action.key:
                _key_to_locator(page, action.key).hover(timeout=5000)

        case ActionType.FOCUS:
            if action.key:
                _key_to_locator(page, action.key).focus(timeout=5000)

        case ActionType.SCROLL:
            x = action.scroll_x or 0
            y = action.scroll_y or 0
            page.evaluate(f"window.scrollTo({x}, {y})")

        case ActionType.KEY_DOWN:
            if action.value:
                page.keyboard.down(action.value)

        case ActionType.KEY_UP:
            if action.value:
                page.keyboard.up(action.value)

        case ActionType.SUBMIT:
            if action.key:
                # Most forms submit via pressing Enter on the active element.
                _key_to_locator(page, action.key).press("Enter", timeout=5000)

        case ActionType.WAIT:
            ms = int(action.value) if action.value and action.value.isdigit() else 500
            page.wait_for_timeout(ms)

        case ActionType.SCREENSHOT:
            path = action.value or f"screenshot_{action.id}.png"
            page.screenshot(path=path)

        case _:
            logger.debug("replay: no handler for action type %s — skipped", action.type)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def replay_session(
    session: Session,
    config: Optional[RecorderConfig] = None,
    *,
    headless: bool = True,
) -> ReplayResult:
    """Replay a session using Playwright.

    Opens a Chromium browser, visits the session's ``base_url``, replays every
    action in order, records the whole thing as video, and returns a
    :class:`ReplayResult`.

    Individual action failures are logged as warnings and skipped — they do not
    abort the replay.

    Args:
        session: The :class:`~recordloop.core.session.Session` to replay.
        config: Optional :class:`~recordloop.capture.recorder.RecorderConfig`
                that controls browser options and video output directory.  A
                default config is created when ``None`` is passed.
        headless: Whether to run the browser in headless mode.  Overrides the
                  value inside *config* (if supplied).

    Returns:
        A :class:`ReplayResult` describing the outcome.
    """
    cfg = RecorderConfig() if config is None else config
    cfg.headless = headless

    start_wall = time.time()
    video_path: Optional[Path] = None
    error: Optional[str] = None
    success = False

    pw = None
    browser = None
    context = None

    try:
        pw = sync_playwright().start()

        launch_args: dict = {
            "headless": cfg.headless,
            "slow_mo": cfg.slow_mo,
        }
        if cfg.executable_path:
            launch_args["executable_path"] = cfg.executable_path

        browser = pw.chromium.launch(**launch_args)

        video_dir = Path(cfg.video_dir)
        video_dir.mkdir(parents=True, exist_ok=True)

        context = browser.new_context(
            viewport={"width": cfg.viewport_width, "height": cfg.viewport_height},
            record_video_dir=str(video_dir),
            record_video_size={
                "width": cfg.viewport_width,
                "height": cfg.viewport_height,
            },
        )

        page = context.new_page()

        # Navigate to the session's base_url first (if set and non-empty) so
        # the browser has an origin to work with before the first action runs.
        if session.base_url:
            try:
                page.goto(session.base_url, wait_until="domcontentloaded")
            except Exception as exc:
                logger.warning("replay: initial navigation to %s failed: %s", session.base_url, exc)

        for action in session.actions:
            try:
                _replay_action(page, action)
            except Exception as exc:
                logger.warning(
                    "replay: action %s (%s) failed — %s",
                    action.id,
                    action.type,
                    exc,
                )

        # Capture video path before closing (file is written on close).
        raw_video: Optional[Path] = None
        if page.video:
            raw_video = Path(page.video.path())

        context.close()
        context = None

        if raw_video:
            video_path = ensure_mp4(raw_video)

        success = True

    except Exception as exc:
        error = str(exc)
        logger.error("replay: session %s failed: %s", session.id, exc)

    finally:
        if context:
            try:
                context.close()
            except Exception:
                pass
        if browser:
            try:
                browser.close()
            except Exception:
                pass
        if pw:
            try:
                pw.stop()
            except Exception:
                pass

    duration_ms = int((time.time() - start_wall) * 1000)

    return ReplayResult(
        session_id=session.id,
        success=success,
        video_path=str(video_path) if video_path else None,
        error=error,
        duration_ms=duration_ms,
    )
