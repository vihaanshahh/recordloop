"""
recordloop.capture
~~~~~~~~~~~~~~~~~~
Playwright-based capture layer: record browser sessions and replay them.

Requires the ``capture`` extra::

    pip install recordloop[capture]
    playwright install chromium
"""

try:
    from playwright.sync_api import sync_playwright  # noqa: F401
except ImportError as e:
    raise ImportError(
        "recordloop[capture] is required. Install with:\n"
        "  pip install recordloop[capture]\n"
        "  playwright install chromium"
    ) from e

from .recorder import PlaywrightRecorder, RecorderConfig
from .replayer import replay_session, ReplayResult

__all__ = [
    "PlaywrightRecorder",
    "RecorderConfig",
    "replay_session",
    "ReplayResult",
]
