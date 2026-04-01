"""
RecordLoop runner — processes session recordings.

This is the engine used by:
  - GitHub Action (CI/CD)
  - Local CLI (python -m recordloop run)
  - Any custom integration

Flow:
  1. Find session JSON files (.recordloop/sessions/*.json)
  2. Replay each with Playwright, record video
  3. Upload video to S3
  4. Update InstantDB metadata
  5. Return results (video URLs, test paths)

Usage:
    from recordloop.runner import Runner
    runner = Runner()
    results = runner.run()          # process all pending sessions
    results = runner.run("abc123")  # process specific session
"""

import json
import os
import sys
from pathlib import Path
from typing import Optional

from .bridge import convert_session, replay_session, _replay_action
from .recorder import RecorderConfig, PlaywrightRecorder, ActionType


# Where sessions live in the repo
SESSIONS_DIR = ".recordloop/sessions"


class Runner:
    """
    Processes recorded sessions — replays, records video, uploads.

    Args:
        sessions_dir: Where to find session JSON files
        output_dir: Where to write generated tests
        video_dir: Where to write videos (before upload)
        upload: Whether to upload videos to S3
        sync_db: Whether to sync metadata to InstantDB
        pr_number: GitHub PR number (set by GitHub Action)
        repo: GitHub repo slug (set by GitHub Action)
    """

    def __init__(
        self,
        sessions_dir: str = SESSIONS_DIR,
        output_dir: str = "generated-tests",
        video_dir: str = "test-videos",
        upload: bool = False,
        sync_db: bool = False,
        pr_number: Optional[int] = None,
        repo: Optional[str] = None,
    ):
        self.sessions_dir = Path(sessions_dir)
        self.output_dir = output_dir
        self.video_dir = video_dir
        self.upload = upload or bool(os.environ.get("RECORDLOOP_S3_BUCKET"))
        self.sync_db = sync_db or bool(os.environ.get("RECORDLOOP_INSTANTDB_APP_ID"))
        self.pr_number = pr_number or _env_int("PR_NUMBER") or _env_int("GITHUB_PR_NUMBER")
        self.repo = repo or os.environ.get("GITHUB_REPOSITORY", "")

    def find_sessions(self) -> list[Path]:
        """Find all session JSON files."""
        if not self.sessions_dir.exists():
            return []
        return sorted(self.sessions_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)

    def run(self, session_id: Optional[str] = None) -> list[dict]:
        """
        Process sessions.

        Args:
            session_id: Specific session to process. If None, processes all.

        Returns:
            List of result dicts, one per session:
            [{ "session_id": "...", "video": "...", "video_url": "...", "test": "...", "status": "..." }]
        """
        if session_id:
            session_path = self.sessions_dir / f"{session_id}.json"
            if not session_path.exists():
                # Also check raw session files from the bridge
                session_path = Path(self.output_dir) / f"session_{session_id}_raw.json"
            if not session_path.exists():
                return [{"session_id": session_id, "status": "not_found"}]
            return [self._process_session(session_path)]

        sessions = self.find_sessions()
        if not sessions:
            print(f"No sessions found in {self.sessions_dir}")
            return []

        print(f"Found {len(sessions)} session(s)")
        results = []
        for path in sessions:
            result = self._process_session(path)
            results.append(result)

        return results

    def _process_session(self, session_path: Path) -> dict:
        """Process a single session file."""
        session_id = session_path.stem.replace("session_", "").replace("_raw", "")
        result = {"session_id": session_id, "status": "processing"}

        print(f"\n--- Processing session: {session_id} ---")

        # Load session
        try:
            session = json.loads(session_path.read_text())
        except (json.JSONDecodeError, OSError) as e:
            result["status"] = "error"
            result["error"] = f"Failed to load session: {e}"
            print(f"  Error: {e}")
            return result

        action_count = len(session.get("actions", []))
        print(f"  Actions: {action_count}")

        # Update InstantDB status
        if self.sync_db:
            self._update_db(session_id, "replaying", session=session)

        # Replay with Playwright
        try:
            config = RecorderConfig(
                video_dir=self.video_dir,
                test_output_dir=self.output_dir,
                headless=True,
            )
            replay_result = replay_session(
                session,
                config=config,
                video=True,
                generate_test=True,
                output_dir=self.output_dir,
            )
            result.update(replay_result)
            print(f"  Video: {replay_result.get('video', 'none')}")
            print(f"  Test:  {replay_result.get('test', 'none')}")
        except Exception as e:
            result["status"] = "replay_failed"
            result["error"] = str(e)
            print(f"  Replay failed: {e}")
            if self.sync_db:
                self._update_db(session_id, "failed")
            return result

        # Upload video to S3
        video_url = None
        if self.upload and result.get("video"):
            try:
                from .storage import upload_video, get_video_url
                key = upload_video(result["video"], session_id)
                video_url = get_video_url(session_id)
                result["video_url"] = video_url
                result["video_key"] = key
                print(f"  Uploaded: {key}")
            except Exception as e:
                print(f"  Upload failed: {e}")
                result["upload_error"] = str(e)

        # Update InstantDB
        if self.sync_db:
            self._update_db(session_id, "uploaded", video_url=video_url, session=session)

        result["status"] = "done"
        print(f"  Done")
        return result

    def _update_db(self, session_id: str, status: str, video_url: str = None, session: dict = None):
        """Update session status in InstantDB."""
        try:
            from .cloud import save_session, update_status
            if status == "replaying" and session:
                save_session(
                    session_id=session_id,
                    status=status,
                    pr_number=self.pr_number,
                    repo=self.repo,
                    action_count=len(session.get("actions", [])),
                    duration_ms=session.get("duration", 0),
                    base_url=session.get("url"),
                )
            else:
                update_status(session_id, status, video_url=video_url)
        except Exception as e:
            print(f"  DB sync failed: {e}")

    def format_pr_comment(self, results: list[dict]) -> str:
        """
        Generate a GitHub PR comment with video links.

        Args:
            results: List of runner results

        Returns:
            Markdown string for the PR comment
        """
        lines = ["## RecordLoop", ""]

        successful = [r for r in results if r.get("video_url")]
        failed = [r for r in results if r.get("status") == "replay_failed"]

        if successful:
            for r in successful:
                session_id = r["session_id"]
                url = r["video_url"]
                lines.append(f"**Session `{session_id}`**")
                lines.append(f"[Watch recording]({url})")
                lines.append("")

        if failed:
            lines.append("### Failed")
            for r in failed:
                lines.append(f"- `{r['session_id']}`: {r.get('error', 'unknown error')}")
            lines.append("")

        if not successful and not failed:
            lines.append("No sessions to process.")

        return "\n".join(lines)


def _env_int(key: str) -> Optional[int]:
    val = os.environ.get(key)
    if val and val.isdigit():
        return int(val)
    return None


def init_sessions_dir(project_dir: str = "."):
    """
    Create the .recordloop/sessions directory and add a .gitkeep.

    Called during `recordloop init` to set up the repo structure.
    """
    sessions_dir = Path(project_dir) / SESSIONS_DIR
    sessions_dir.mkdir(parents=True, exist_ok=True)
    gitkeep = sessions_dir / ".gitkeep"
    if not gitkeep.exists():
        gitkeep.touch()

    # Add to .gitignore: ignore videos but keep sessions
    gitignore = Path(project_dir) / ".recordloop" / ".gitignore"
    gitignore.write_text("# Keep session JSONs, ignore everything else\n*.mp4\n*.webm\n*.png\n")

    return str(sessions_dir)
