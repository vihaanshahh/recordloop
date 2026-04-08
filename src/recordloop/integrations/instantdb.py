"""
InstantDB integration for session metadata sync.

Stores session metadata in InstantDB so the dashboard can read it
in real-time without a backend server.

The Python side writes via InstantDB's admin REST API.
The JS dashboard reads via InstantDB's client SDK.

Requires: pip install recordloop[cloud]
"""

import json
import time
from typing import Optional
from urllib.request import Request, urlopen
from urllib.error import URLError

try:
    import requests as _requests_lib
except ImportError:
    _requests_lib = None

INSTANTDB_API = "https://api.instantdb.com/admin"


def _require_requests():
    if _requests_lib is None:
        raise ImportError(
            "requests is required for InstantDB sync. "
            "Install with: pip install recordloop[cloud]"
        )


def _request(endpoint: str, data: dict, admin_token: str, app_id: str) -> dict:
    """Make an authenticated request to InstantDB admin API."""
    _require_requests()

    url = f"{INSTANTDB_API}/{endpoint}"
    resp = _requests_lib.post(
        url,
        json=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {admin_token}",
        },
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def sync_session(
    session_id: str,
    metadata: dict,
    app_id: str,
    admin_token: str,
    namespace: str = "sessions",
) -> bool:
    """
    Sync session metadata to InstantDB.

    Args:
        session_id: Unique session identifier
        metadata: Arbitrary metadata dict to store alongside the session
        app_id: InstantDB app ID
        admin_token: InstantDB admin token (server-side only)
        namespace: InstantDB namespace/collection name (default: "sessions")

    Returns:
        True on success, raises on failure
    """
    record = {
        "id": session_id,
        "sessionId": session_id,
        "updatedAt": int(time.time() * 1000),
    }
    record.update({k: v for k, v in metadata.items() if v is not None})

    _request(
        "transact",
        {
            "app_id": app_id,
            "steps": [
                ["update", namespace, session_id, record],
            ],
        },
        admin_token=admin_token,
        app_id=app_id,
    )

    return True


def get_session(
    session_id: str,
    app_id: str,
    admin_token: str,
    namespace: str = "sessions",
) -> Optional[dict]:
    """
    Get session metadata from InstantDB.

    Args:
        session_id: Session identifier
        app_id: InstantDB app ID
        admin_token: InstantDB admin token
        namespace: InstantDB namespace/collection name (default: "sessions")

    Returns:
        Session dict or None
    """
    result = _request(
        "query",
        {
            "app_id": app_id,
            "query": {
                namespace: {
                    "$": {"where": {"sessionId": session_id}},
                }
            },
        },
        admin_token=admin_token,
        app_id=app_id,
    )

    records = result.get(namespace, [])
    return records[0] if records else None


def list_sessions(
    app_id: str,
    admin_token: str,
    namespace: str = "sessions",
    filters: Optional[dict] = None,
    limit: int = 50,
) -> list:
    """
    List sessions from InstantDB.

    Args:
        app_id: InstantDB app ID
        admin_token: InstantDB admin token
        namespace: InstantDB namespace/collection name (default: "sessions")
        filters: Optional dict of field/value pairs to filter by
        limit: Max results

    Returns:
        List of session dicts
    """
    query_clause: dict = {"$": {"limit": limit}}
    if filters:
        query_clause["$"]["where"] = filters

    result = _request(
        "query",
        {
            "app_id": app_id,
            "query": {namespace: query_clause},
        },
        admin_token=admin_token,
        app_id=app_id,
    )

    return result.get(namespace, [])


def delete_session(
    session_id: str,
    app_id: str,
    admin_token: str,
    namespace: str = "sessions",
) -> bool:
    """
    Delete a session from InstantDB.

    Args:
        session_id: Session identifier
        app_id: InstantDB app ID
        admin_token: InstantDB admin token
        namespace: InstantDB namespace/collection name (default: "sessions")

    Returns:
        True on success
    """
    _request(
        "transact",
        {
            "app_id": app_id,
            "steps": [
                ["delete", namespace, session_id],
            ],
        },
        admin_token=admin_token,
        app_id=app_id,
    )

    return True
