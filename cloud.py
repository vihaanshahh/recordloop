"""
InstantDB integration for session metadata.

Stores session metadata (not videos) in InstantDB so the dashboard
can read it in real-time without a backend server.

The Python side writes via InstantDB's admin REST API.
The JS dashboard reads via InstantDB's client SDK.

Config:
  RECORDLOOP_INSTANTDB_APP_ID=...       # Your InstantDB app ID
  RECORDLOOP_INSTANTDB_ADMIN_TOKEN=...  # Admin token (server-side only)
"""

import json
import os
from typing import Optional
from urllib.request import Request, urlopen
from urllib.error import URLError

INSTANTDB_API = "https://api.instantdb.com/admin"


def _app_id():
    app_id = os.environ.get("RECORDLOOP_INSTANTDB_APP_ID", "")
    if not app_id:
        raise ValueError(
            "RECORDLOOP_INSTANTDB_APP_ID not set. "
            "Create an app at instantdb.com and add the app ID to your .env"
        )
    return app_id


def _admin_token():
    token = os.environ.get("RECORDLOOP_INSTANTDB_ADMIN_TOKEN", "")
    if not token:
        raise ValueError(
            "RECORDLOOP_INSTANTDB_ADMIN_TOKEN not set. "
            "Get your admin token from the InstantDB dashboard."
        )
    return token


def _request(endpoint: str, data: dict) -> dict:
    """Make an authenticated request to InstantDB admin API."""
    url = f"{INSTANTDB_API}/{endpoint}"
    payload = json.dumps(data).encode()

    req = Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {_admin_token()}",
        },
        method="POST",
    )

    try:
        with urlopen(req) as resp:
            return json.loads(resp.read().decode())
    except URLError as e:
        if hasattr(e, "read"):
            body = e.read().decode()
            raise RuntimeError(f"InstantDB error: {body}") from e
        raise


def save_session(
    session_id: str,
    video_url: Optional[str] = None,
    video_key: Optional[str] = None,
    pr_number: Optional[int] = None,
    repo: Optional[str] = None,
    status: str = "recorded",
    action_count: int = 0,
    duration_ms: int = 0,
    base_url: Optional[str] = None,
    framework: Optional[str] = None,
    meta: Optional[dict] = None,
) -> dict:
    """
    Save or update session metadata in InstantDB.

    Args:
        session_id: Unique session identifier
        video_url: Pre-signed S3 URL for the video
        video_key: S3 key for the video
        pr_number: GitHub PR number (if triggered from CI)
        repo: GitHub repo (owner/name)
        status: Session status (recorded, replaying, uploaded, failed)
        action_count: Number of recorded actions
        duration_ms: Recording duration in milliseconds
        base_url: The app URL that was recorded
        framework: Detected frontend framework
        meta: Additional metadata

    Returns:
        InstantDB response
    """
    import time

    record = {
        "id": session_id,
        "sessionId": session_id,
        "videoUrl": video_url,
        "videoKey": video_key,
        "prNumber": pr_number,
        "repo": repo,
        "status": status,
        "actionCount": action_count,
        "durationMs": duration_ms,
        "baseUrl": base_url,
        "framework": framework,
        "meta": json.dumps(meta or {}),
        "updatedAt": int(time.time() * 1000),
    }

    # Remove None values
    record = {k: v for k, v in record.items() if v is not None}

    return _request("transact", {
        "app_id": _app_id(),
        "steps": [
            ["update", "sessions", session_id, record],
        ],
    })


def get_session(session_id: str) -> Optional[dict]:
    """
    Get session metadata from InstantDB.

    Args:
        session_id: Session identifier

    Returns:
        Session dict or None
    """
    result = _request("query", {
        "app_id": _app_id(),
        "query": {
            "sessions": {
                "$": {"where": {"sessionId": session_id}},
            }
        },
    })

    sessions = result.get("sessions", [])
    return sessions[0] if sessions else None


def list_sessions(
    repo: Optional[str] = None,
    pr_number: Optional[int] = None,
    limit: int = 50,
) -> list[dict]:
    """
    List sessions from InstantDB.

    Args:
        repo: Filter by GitHub repo
        pr_number: Filter by PR number
        limit: Max results

    Returns:
        List of session dicts
    """
    where = {}
    if repo:
        where["repo"] = repo
    if pr_number is not None:
        where["prNumber"] = pr_number

    query = {"sessions": {}}
    if where:
        query["sessions"]["$"] = {"where": where, "limit": limit}
    else:
        query["sessions"]["$"] = {"limit": limit}

    result = _request("query", {
        "app_id": _app_id(),
        "query": query,
    })

    return result.get("sessions", [])


def update_status(session_id: str, status: str, video_url: Optional[str] = None):
    """
    Quick status update for a session.

    Args:
        session_id: Session identifier
        status: New status (recorded, replaying, uploaded, failed)
        video_url: Optional video URL to set
    """
    import time

    updates = {
        "status": status,
        "updatedAt": int(time.time() * 1000),
    }
    if video_url:
        updates["videoUrl"] = video_url

    return _request("transact", {
        "app_id": _app_id(),
        "steps": [
            ["update", "sessions", session_id, updates],
        ],
    })


def delete_session(session_id: str):
    """Delete a session from InstantDB."""
    return _request("transact", {
        "app_id": _app_id(),
        "steps": [
            ["delete", "sessions", session_id],
        ],
    })
