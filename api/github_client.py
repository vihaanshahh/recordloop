"""
GitHub REST API client — stdlib only, no third-party HTTP lib.

Used by the cloud API to:
  - Fetch PR file diffs + content
  - Post PR comments
"""

import asyncio
import base64
import json
import urllib.request
import urllib.error
from typing import Optional

# Import the canonical filter — keeps in sync with the framework list in
# analyzer.py instead of duplicating a stale copy here.
from .analyzer import _is_component

_BASE = "https://api.github.com"


async def get_pr_files(repo: str, pr_number: int, token: str) -> list[dict]:
    """
    Return changed files in a PR, with full source content for component files.

    Each entry: {filename, status, patch, content}
    """
    url = f"{_BASE}/repos/{repo}/pulls/{pr_number}/files?per_page=100"
    raw_files = await _get(url, token)

    results = []
    for f in raw_files:
        filename = f["filename"]
        entry = {
            "filename": filename,
            "status": f.get("status", "modified"),
            "patch": f.get("patch", ""),
            "content": None,
        }

        # Fetch full source for component files that weren't deleted
        if f.get("status") != "removed" and _is_component(filename):
            try:
                content_url = f"{_BASE}/repos/{repo}/contents/{filename}"
                content_data = await _get(content_url, token)
                if isinstance(content_data, dict) and content_data.get("encoding") == "base64":
                    entry["content"] = base64.b64decode(
                        content_data["content"]
                    ).decode("utf-8", errors="replace")
            except Exception:
                pass  # fall back to patch

        results.append(entry)

    return results


async def post_pr_comment(repo: str, pr_number: int, body: str, token: str) -> dict:
    """Post a comment on a PR (issues endpoint works for PRs too)."""
    url = f"{_BASE}/repos/{repo}/issues/{pr_number}/comments"
    return await _post(url, token, {"body": body})


async def _get(url: str, token: str) -> dict | list:
    def _do():
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"token {token}",
                "Accept": "application/vnd.github.v3+json",
                "User-Agent": "recordloop-api/1.0",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())

    return await asyncio.to_thread(_do)


async def _post(url: str, token: str, data: dict) -> dict:
    def _do():
        body = json.dumps(data).encode()
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Authorization": f"token {token}",
                "Accept": "application/vnd.github.v3+json",
                "Content-Type": "application/json",
                "User-Agent": "recordloop-api/1.0",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())

    return await asyncio.to_thread(_do)


