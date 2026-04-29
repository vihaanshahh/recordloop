"""
Build an animated GIF from page screenshots taken during a flow.

GitHub PR comments don't render `<video>` tags from arbitrary CDNs (only
`github.com/user-attachments/assets/...` URLs auto-embed, and that endpoint
isn't accessible from a token-authed runner). Animated GIFs served via
release assets DO render inline as `<img>`. So we take a screenshot after
each meaningful step and stitch them into a slideshow GIF.

Pillow is the only dependency — no ffmpeg, no system binaries. The .webm
recording is preserved alongside the GIF as a "Watch full recording" link.
"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image

# Slideshow timing — each step's outcome holds long enough for a reviewer
# to read the resulting screen. Total length: roughly N_steps × FRAME_MS.
FRAME_MS = 1500
# Last frame holds longer so the viewer can absorb the final state before
# the GIF loops back to the start.
FINAL_FRAME_MS = 2500
# Cap GIF width to keep file size sane. 960 reads cleanly on a PR comment
# (max comment width is ~720–820px on github.com); over-resolution wastes
# bytes the reviewer can't see.
MAX_WIDTH = 960


def build_gif(frames_png: list[bytes], out_path: Path) -> Path | None:
    """Stitch PNG frames into an animated GIF at ``out_path``.

    Returns the output path on success, ``None`` if there are no frames or
    Pillow rejects them. Caller decides whether to fail loudly or fall back.
    """
    if not frames_png:
        return None

    images: list[Image.Image] = []
    for png in frames_png:
        try:
            img = Image.open(io.BytesIO(png)).convert("RGB")
        except Exception:
            # Bad frame — skip rather than abort the whole GIF.
            continue
        if img.width > MAX_WIDTH:
            ratio = MAX_WIDTH / img.width
            img = img.resize((MAX_WIDTH, int(img.height * ratio)), Image.LANCZOS)
        # Adaptive palette gives much smaller files than the default web
        # palette while keeping screenshots readable.
        img = img.convert("P", palette=Image.ADAPTIVE, colors=128)
        images.append(img)

    if not images:
        return None

    durations = [FRAME_MS] * len(images)
    durations[-1] = FINAL_FRAME_MS

    out_path.parent.mkdir(parents=True, exist_ok=True)
    images[0].save(
        out_path,
        format="GIF",
        save_all=True,
        append_images=images[1:],
        duration=durations,
        loop=0,
        disposal=2,
        optimize=True,
    )
    return out_path
