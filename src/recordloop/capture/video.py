"""
recordloop.capture.video
~~~~~~~~~~~~~~~~~~~~~~~~
FFmpeg helpers for converting Playwright's .webm recordings to .mp4.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def ffmpeg_available() -> bool:
    """Check if ffmpeg is on PATH."""
    return shutil.which("ffmpeg") is not None


def ensure_mp4(video_path: Path) -> Path:
    """Convert webm to mp4 using ffmpeg.

    Returns the original path unchanged when:
    - the file is already an .mp4
    - ffmpeg is not available
    - ffmpeg conversion fails

    On success the original .webm file is removed and the new .mp4 path is
    returned.

    Args:
        video_path: Path to the video file (typically .webm from Playwright).

    Returns:
        Path to the .mp4 file, or the original path if conversion was skipped.
    """
    if video_path.suffix.lower() != ".webm":
        return video_path

    if not ffmpeg_available():
        return video_path

    mp4_path = video_path.with_suffix(".mp4")

    try:
        subprocess.run(
            [
                "ffmpeg",
                "-i", str(video_path),
                "-c:v", "libx264",
                "-c:a", "aac",
                "-y",           # overwrite output if it already exists
                str(mp4_path),
            ],
            check=True,
            capture_output=True,
        )
        # Remove original webm only after successful conversion
        video_path.unlink()
        return mp4_path
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        # ffmpeg failed or disappeared; return the original path so callers
        # still have something usable.
        return video_path
