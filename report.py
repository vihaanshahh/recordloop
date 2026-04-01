"""
HTML report generator for RecordLoop recordings.

Generates a single-file HTML report with:
- Action timeline per recording
- Video playback (if available)
- Generated test code preview
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional


def generate_report(
    recordings_dir: str = "generated-tests",
    video_dir: str = "test-videos",
    output_path: Optional[str] = None,
) -> str:
    """
    Generate an HTML report from recordings and videos.

    Args:
        recordings_dir: Directory containing recording JSON files
        video_dir: Directory containing video files
        output_path: Where to write the HTML (default: recordloop-report.html)

    Returns:
        Path to the generated report
    """
    rec_path = Path(recordings_dir)
    vid_path = Path(video_dir)

    # Collect recordings (JSON files)
    recordings = []
    if rec_path.exists():
        for f in sorted(rec_path.glob("recording_*.json"), reverse=True):
            try:
                data = json.loads(f.read_text())
                recordings.append({"file": f.name, "path": str(f), "actions": data})
            except (json.JSONDecodeError, OSError):
                continue

    # Collect test files
    test_files = []
    if rec_path.exists():
        for f in sorted(rec_path.glob("test_*.py"), reverse=True):
            try:
                test_files.append({"file": f.name, "code": f.read_text()})
            except OSError:
                continue

    # Collect videos
    videos = []
    if vid_path.exists():
        for ext in ("*.mp4", "*.webm"):
            for f in sorted(vid_path.glob(ext), reverse=True):
                videos.append({"file": f.name, "path": str(f)})

    html = _build_html(recordings, test_files, videos)

    if output_path is None:
        output_path = "recordloop-report.html"

    Path(output_path).write_text(html)
    return output_path


def _build_html(
    recordings: list[dict],
    test_files: list[dict],
    videos: list[dict],
) -> str:
    """Build the full HTML report."""

    # Build recordings section
    rec_cards = ""
    for rec in recordings:
        actions_html = ""
        for i, action in enumerate(rec["actions"]):
            action_type = action.get("action_type", "unknown")
            selector = action.get("selector", "")
            value = action.get("value", "")
            detail = selector or value or ""
            ts = action.get("timestamp", 0)
            if isinstance(ts, (int, float)):
                ts = f"{ts:.2f}s"
            actions_html += f"""
            <div class="action">
                <span class="action-num">{i + 1}</span>
                <span class="action-type">{action_type}</span>
                <span class="action-detail">{_escape(detail)}</span>
                <span class="action-time">{ts}</span>
            </div>"""

        rec_cards += f"""
        <div class="card">
            <h3>{rec['file']}</h3>
            <div class="action-timeline">{actions_html}</div>
        </div>"""

    # Build test code section
    test_cards = ""
    for tf in test_files:
        test_cards += f"""
        <div class="card">
            <h3>{tf['file']}</h3>
            <pre><code>{_escape(tf['code'])}</code></pre>
        </div>"""

    # Build video section
    video_cards = ""
    for vid in videos:
        mime = "video/mp4" if vid["file"].endswith(".mp4") else "video/webm"
        video_cards += f"""
        <div class="card">
            <h3>{vid['file']}</h3>
            <video controls width="100%">
                <source src="{vid['path']}" type="{mime}">
            </video>
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>RecordLoop Report</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: #0d1117; color: #c9d1d9; padding: 2rem; }}
  h1 {{ color: #58a6ff; margin-bottom: 0.5rem; }}
  .subtitle {{ color: #8b949e; margin-bottom: 2rem; }}
  h2 {{ color: #c9d1d9; margin: 2rem 0 1rem; border-bottom: 1px solid #21262d; padding-bottom: 0.5rem; }}
  .card {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px;
           padding: 1.25rem; margin-bottom: 1rem; }}
  .card h3 {{ color: #58a6ff; margin-bottom: 0.75rem; font-size: 0.95rem; }}
  .action {{ display: flex; align-items: center; gap: 0.75rem; padding: 0.4rem 0;
             border-bottom: 1px solid #21262d; font-size: 0.85rem; }}
  .action:last-child {{ border-bottom: none; }}
  .action-num {{ background: #30363d; color: #8b949e; border-radius: 50%;
                 width: 24px; height: 24px; display: flex; align-items: center;
                 justify-content: center; font-size: 0.75rem; flex-shrink: 0; }}
  .action-type {{ background: #1f6feb33; color: #58a6ff; padding: 2px 8px;
                  border-radius: 4px; font-family: monospace; font-size: 0.8rem; }}
  .action-detail {{ color: #8b949e; font-family: monospace; font-size: 0.8rem;
                    overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; }}
  .action-time {{ color: #484f58; font-size: 0.75rem; flex-shrink: 0; }}
  pre {{ background: #0d1117; border: 1px solid #30363d; border-radius: 6px;
         padding: 1rem; overflow-x: auto; font-size: 0.8rem; line-height: 1.5; }}
  code {{ color: #c9d1d9; }}
  video {{ border-radius: 6px; margin-top: 0.5rem; }}
  .empty {{ color: #484f58; font-style: italic; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(400px, 1fr)); gap: 1rem; }}
</style>
</head>
<body>
<h1>RecordLoop Report</h1>
<p class="subtitle">Generated {datetime.now().strftime("%Y-%m-%d %H:%M")}</p>

<h2>Recordings ({len(recordings)})</h2>
{rec_cards if rec_cards else '<p class="empty">No recordings found. Run a test to generate recordings.</p>'}

<h2>Generated Tests ({len(test_files)})</h2>
{test_cards if test_cards else '<p class="empty">No generated tests found.</p>'}

<h2>Videos ({len(videos)})</h2>
<div class="grid">
{video_cards if video_cards else '<p class="empty">No videos found. Enable video capture to record test runs.</p>'}
</div>

</body>
</html>"""


def _escape(text: str) -> str:
    """Escape HTML special characters."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
