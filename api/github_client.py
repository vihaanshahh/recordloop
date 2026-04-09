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


async def upsert_pr_comment(
    repo: str,
    pr_number: int,
    body: str,
    token: str,
    marker: str = "## RecordLoop",
) -> dict:
    """Edit the existing RecordLoop comment in place, or create one if missing.

    Looks for the first comment whose body starts with ``marker`` and PATCHes
    it. Falls back to POST when no match is found. This keeps PR threads
    clean: re-runs on push update the same comment instead of stacking new
    ones.
    """
    list_url = f"{_BASE}/repos/{repo}/issues/{pr_number}/comments?per_page=100"
    try:
        comments = await _get(list_url, token)
    except Exception as e:
        print(f"[github_client] could not list PR comments, falling back to POST: {e}")
        return await post_pr_comment(repo, pr_number, body, token)

    existing_id: Optional[int] = None
    if isinstance(comments, list):
        for c in comments:
            if isinstance(c, dict) and (c.get("body") or "").lstrip().startswith(marker):
                existing_id = c.get("id")
                break

    if existing_id is not None:
        patch_url = f"{_BASE}/repos/{repo}/issues/comments/{existing_id}"
        try:
            updated = await _patch(patch_url, token, {"body": body})
            print(f"[github_client] updated existing PR comment {existing_id}")
            return updated
        except Exception as e:
            print(f"[github_client] PATCH failed ({e}), falling back to new comment")

    return await post_pr_comment(repo, pr_number, body, token)


async def upload_pr_video(repo: str, pr_number: int, file_path: str, token: str) -> Optional[str]:
    """Upload a video as a GitHub release asset and return its browser_download_url.

    Uses the official releases API (works with GITHUB_TOKEN + contents:write).
    Videos are stored under a pre-release tagged 'recordloop-recordings'.
    """
    import mimetypes
    import os
    import urllib.parse

    filename = f"pr-{pr_number}-{os.path.basename(file_path)}"
    content_type = mimetypes.guess_type(file_path)[0] or "video/mp4"

    release_id = await _get_or_create_recordings_release(repo, token)
    if not release_id:
        return None

    def _upload():
        with open(file_path, "rb") as fh:
            data = fh.read()
        safe_name = urllib.parse.quote(filename)
        url = (
            f"https://uploads.github.com/repos/{repo}/releases/{release_id}"
            f"/assets?name={safe_name}"
        )
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Authorization": f"token {token}",
                "Accept": "application/vnd.github+json",
                "Content-Type": content_type,
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read())
            return result.get("browser_download_url")

    try:
        url = await asyncio.to_thread(_upload)
        print(f"[github_client] uploaded {filename} → {url}")
        return url
    except Exception as e:
        print(f"[github_client] video upload failed for {filename}: {e}")
        return None


async def _get_or_create_recordings_release(repo: str, token: str) -> Optional[int]:
    """Return the id of the 'recordloop-recordings' pre-release, creating it if needed."""
    tag = "recordloop-recordings"
    tag_url = f"{_BASE}/repos/{repo}/releases/tags/{tag}"

    try:
        release = await _get(tag_url, token)
        return release["id"]
    except Exception:
        pass

    try:
        release = await _post(
            f"{_BASE}/repos/{repo}/releases",
            token,
            {
                "tag_name": tag,
                "name": "RecordLoop Recordings",
                "body": "Video recordings auto-generated by the RecordLoop GitHub Action.",
                "draft": False,
                "prerelease": True,
            },
        )
        return release["id"]
    except Exception:
        # Parallel upload may have created it first (race) — retry the GET.
        pass

    try:
        release = await _get(tag_url, token)
        return release["id"]
    except Exception as e:
        print(f"[github_client] could not get/create recordings release: {e}")
        return None


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


async def _patch(url: str, token: str, data: dict) -> dict:
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
            method="PATCH",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())

    return await asyncio.to_thread(_do)


