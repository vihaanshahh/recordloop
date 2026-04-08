"""
recordloop.capture.recorder
~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Playwright-based browser recorder that produces Session objects.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from playwright.sync_api import sync_playwright, Browser, Page, BrowserContext

from recordloop.core.session import (
    Action,
    ActionType,
    SemanticKey,
    Session,
)
from .video import ensure_mp4


@dataclass
class RecorderConfig:
    """Configuration for :class:`PlaywrightRecorder`."""

    base_url: str = "http://localhost:3000"
    video_dir: str = "test-videos"
    headless: bool = True
    slow_mo: int = 0
    viewport_width: int = 1280
    viewport_height: int = 720
    executable_path: str = ""


class PlaywrightRecorder:
    """Capture browser interactions and return a :class:`~recordloop.core.session.Session`.

    Usage::

        config = RecorderConfig(base_url="https://example.com")
        with PlaywrightRecorder(config) as recorder:
            page = recorder.start_recording(navigate_to="https://example.com/login")
            # drive the browser however you like, or just let users interact
            recorder.record_click(SemanticKey("testid", "submit-btn"))
            session = recorder.stop_recording()
    """

    def __init__(self, config: Optional[RecorderConfig] = None) -> None:
        self.config = config or RecorderConfig()

        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

        self._is_recording: bool = False
        self._actions: list[Action] = []
        self._start_time: Optional[float] = None
        self._recorded_at: Optional[datetime] = None
        self._video_path: Optional[Path] = None

    # ------------------------------------------------------------------
    # Context-manager protocol
    # ------------------------------------------------------------------

    def __enter__(self) -> "PlaywrightRecorder":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.cleanup()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _generate_action_id(self) -> str:
        return str(uuid.uuid4())[:8]

    def _setup_browser(self) -> None:
        """Launch Playwright browser with video recording enabled."""
        self._playwright = sync_playwright().start()

        launch_args: dict = {
            "headless": self.config.headless,
            "slow_mo": self.config.slow_mo,
        }
        if self.config.executable_path:
            launch_args["executable_path"] = self.config.executable_path

        self._browser = self._playwright.chromium.launch(**launch_args)

        video_dir = Path(self.config.video_dir)
        video_dir.mkdir(parents=True, exist_ok=True)

        self._context = self._browser.new_context(
            viewport={
                "width": self.config.viewport_width,
                "height": self.config.viewport_height,
            },
            record_video_dir=str(video_dir),
            record_video_size={
                "width": self.config.viewport_width,
                "height": self.config.viewport_height,
            },
        )
        self._page = self._context.new_page()

    def _current_timestamp_ms(self) -> int:
        """Elapsed milliseconds since recording started."""
        if self._start_time is None:
            return 0
        return int((time.time() - self._start_time) * 1000)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start_recording(self, navigate_to: Optional[str] = None) -> Page:
        """Start recording.

        Args:
            navigate_to: URL to navigate to immediately after the browser opens.

        Returns:
            The Playwright :class:`~playwright.sync_api.Page` object.

        Raises:
            RuntimeError: If :meth:`start_recording` was already called.
        """
        if self._is_recording:
            raise RuntimeError("Already recording. Call stop_recording() first.")

        self._setup_browser()
        self._is_recording = True
        self._actions = []
        self._start_time = time.time()
        self._recorded_at = datetime.now(tz=timezone.utc)
        self._video_path = None

        if navigate_to:
            self._page.goto(navigate_to)

        return self._page

    def stop_recording(self) -> Session:
        """Stop recording and return a :class:`~recordloop.core.session.Session`.

        The browser context is *not* closed here — call :meth:`cleanup` (or use
        the context-manager form) when you are finished with the page.

        Returns:
            A fully-populated :class:`~recordloop.core.session.Session`.

        Raises:
            RuntimeError: If recording has not been started.
        """
        if not self._is_recording:
            raise RuntimeError("Not recording. Call start_recording() first.")

        self._is_recording = False

        duration_ms = self._current_timestamp_ms()

        # Capture video path before closing the context (Playwright finalises
        # the video file only after the context/page is closed, but the path
        # is available immediately).
        raw_video_path: Optional[Path] = None
        if self._page and self._page.video:
            raw_video_path = Path(self._page.video.path())

        # Close context so Playwright writes the video file to disk.
        if self._context:
            self._context.close()
            self._context = None

        if raw_video_path:
            self._video_path = ensure_mp4(raw_video_path)

        # Derive base_url from the first navigate action, or fall back to
        # the configured value.
        base_url = self.config.base_url
        for action in self._actions:
            if action.type == ActionType.NAVIGATE and action.page_url:
                base_url = action.page_url
                break

        session = Session(
            id=str(uuid.uuid4()),
            recorded_at=self._recorded_at or datetime.now(tz=timezone.utc),
            duration_ms=duration_ms,
            base_url=base_url,
            actions=list(self._actions),
            viewport=(self.config.viewport_width, self.config.viewport_height),
            meta={
                "video_path": str(self._video_path) if self._video_path else None,
            },
        )
        return session

    # ------------------------------------------------------------------
    # Action recording
    # ------------------------------------------------------------------

    def record_action(
        self,
        type: ActionType,
        key: Optional[SemanticKey] = None,
        value: Optional[str] = None,
        **options,
    ) -> Action:
        """Append an action to the in-progress recording.

        Args:
            type: The :class:`~recordloop.core.session.ActionType`.
            key: The :class:`~recordloop.core.session.SemanticKey` identifying
                 the target element, or ``None`` for element-less actions
                 (e.g. ``NAVIGATE``, ``SCROLL``).
            value: Optional string payload (URL for navigate, text for type,
                   option for select, …).
            **options: Extra fields forwarded directly to the
                       :class:`~recordloop.core.session.Action` constructor
                       (``scroll_x``, ``scroll_y``, ``page_url``,
                       ``page_title``).

        Returns:
            The created and stored :class:`~recordloop.core.session.Action`.

        Raises:
            RuntimeError: If :meth:`start_recording` has not been called.
        """
        if not self._is_recording:
            raise RuntimeError("Not recording. Call start_recording() first.")

        page_url = options.pop("page_url", self._page.url if self._page else None)
        page_title = options.pop(
            "page_title",
            self._page.title() if self._page else None,
        )
        scroll_x: Optional[int] = options.pop("scroll_x", None)
        scroll_y: Optional[int] = options.pop("scroll_y", None)

        action = Action(
            id=self._generate_action_id(),
            timestamp_ms=self._current_timestamp_ms(),
            type=type,
            key=key,
            value=value,
            page_url=page_url,
            page_title=page_title,
            scroll_x=scroll_x,
            scroll_y=scroll_y,
        )
        self._actions.append(action)
        return action

    def record_click(self, key: SemanticKey, **options) -> Action:
        """Convenience wrapper to record a :attr:`~recordloop.core.session.ActionType.CLICK`."""
        return self.record_action(ActionType.CLICK, key=key, **options)

    def record_type(self, key: SemanticKey, value: str, **options) -> Action:
        """Convenience wrapper to record a :attr:`~recordloop.core.session.ActionType.TYPE`."""
        return self.record_action(ActionType.TYPE, key=key, value=value, **options)

    def record_navigate(self, url: str, **options) -> Action:
        """Convenience wrapper to record a :attr:`~recordloop.core.session.ActionType.NAVIGATE`."""
        return self.record_action(ActionType.NAVIGATE, value=url, **options)

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def get_page(self) -> Optional[Page]:
        """Return the active Playwright page, or ``None`` if not started."""
        return self._page

    def get_video_path(self) -> Optional[Path]:
        """Return the path to the recorded video, or ``None`` if unavailable.

        The path is only populated after :meth:`stop_recording` is called.
        """
        return self._video_path

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def cleanup(self) -> None:
        """Release all Playwright resources.

        Safe to call multiple times or when recording was never started.
        """
        if self._context:
            try:
                self._context.close()
            except Exception:
                pass
            self._context = None

        if self._browser:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None

        if self._playwright:
            try:
                self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

        self._page = None
        self._is_recording = False
