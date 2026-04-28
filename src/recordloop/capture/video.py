"""
recordloop.capture.video
~~~~~~~~~~~~~~~~~~~~~~~~
Video path helpers. Playwright records .webm natively; we serve that directly.
"""

from pathlib import Path


def ensure_mp4(video_path: Path) -> Path:
    """No-op — kept for import compatibility. Returns the path unchanged."""
    return video_path
