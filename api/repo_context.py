"""
Repo context loader — parses .github/recordloop.md.

The file has two parts:
  1. YAML frontmatter (``---`` delimited) — config consumed directly by the
     action (ignore_paths, context_globs, login_config, etc.)
  2. Freeform markdown body — injected as a cacheable system message for the
     LLM so it doesn't re-discover repo conventions on every PR.

Public API:
    parse_recordloop_md(raw) -> RepoContext
    fetch_recordloop_md(repo, token, ref) -> RepoContext | None
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

import yaml


def _glob_match(filename: str, pattern: str) -> bool:
    """Match *filename* against a gitignore-style glob *pattern*.

    Unlike ``fnmatch``, ``**`` matches across ``/`` boundaries (zero or more
    path segments).  A single ``*`` matches anything except ``/``.
    """
    i = 0
    regex = "^"
    while i < len(pattern):
        if pattern[i : i + 2] == "**":
            regex += ".*"
            i += 2
            # skip trailing / after ** (e.g. **/foo)
            if i < len(pattern) and pattern[i] == "/":
                i += 1
        elif pattern[i] == "*":
            regex += "[^/]*"
            i += 1
        elif pattern[i] == "?":
            regex += "[^/]"
            i += 1
        else:
            regex += re.escape(pattern[i])
            i += 1
    regex += "$"
    return bool(re.match(regex, filename))

# Paths to try, in priority order. First hit wins.
_CANDIDATE_PATHS = (
    ".github/recordloop.md",
    "RECORDLOOP.md",
)


@dataclass
class RepoContext:
    """Parsed .github/recordloop.md."""

    # --- frontmatter (action-consumed, NOT sent to LLM) ---
    ignore_paths: list[str] = field(default_factory=list)
    context_globs: list[str] = field(default_factory=list)
    selector_convention: str = ""
    default_navigate_to: str = "/"
    login_config: str = ""  # "storage-state" or "" for v1

    # --- body (LLM-consumed) ---
    body: str = ""

    def apply_ignore_paths(self, changed_files: list[dict]) -> list[dict]:
        """Drop files matching any ignore_paths glob."""
        if not self.ignore_paths:
            return changed_files
        return [
            f for f in changed_files
            if not any(
                _glob_match(f["filename"], pat) for pat in self.ignore_paths
            )
        ]

    def to_system_message(self) -> Optional[str]:
        """Render the body as a cacheable system message, or None if empty."""
        text = self.body.strip()
        if not text:
            return None
        return (
            "Repository context (from .github/recordloop.md). "
            "This describes durable conventions for the repo. Per-PR "
            "specifics come in the next user message.\n\n"
            + text
        )


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _split_frontmatter(raw: str) -> tuple[Optional[str], str]:
    """Return (frontmatter_text, body_text). frontmatter_text is None if absent."""
    # Normalise Windows line endings once, up front.
    raw = raw.replace("\r\n", "\n")

    stripped = raw.lstrip()
    if not stripped.startswith("---"):
        return None, raw

    # Find the closing ---
    after_open = stripped[3:]
    # Skip the rest of the opening --- line
    newline = after_open.find("\n")
    if newline == -1:
        return None, raw
    after_open = after_open[newline + 1 :]

    close = after_open.find("\n---")
    if close == -1:
        return None, raw

    frontmatter = after_open[:close]
    body = after_open[close + 4 :]  # skip "\n---"
    # Skip the rest of the closing --- line
    nl = body.find("\n")
    if nl != -1:
        body = body[nl + 1 :]
    else:
        body = ""

    return frontmatter, body


def parse_recordloop_md(raw: str) -> RepoContext:
    """Parse YAML frontmatter + markdown body from raw file contents."""
    if not raw or not raw.strip():
        return RepoContext()

    frontmatter_text, body = _split_frontmatter(raw)

    if frontmatter_text is None:
        return RepoContext(body=body.strip())

    try:
        data = yaml.safe_load(frontmatter_text) or {}
    except yaml.YAMLError:
        # Malformed YAML — use body only (without --- delimiters), don't crash.
        return RepoContext(body=body.strip())

    if not isinstance(data, dict):
        return RepoContext(body=body.strip())

    return RepoContext(
        ignore_paths=list(data.get("ignore_paths") or []),
        context_globs=list(data.get("context_globs") or []),
        selector_convention=str(data.get("selector_convention") or ""),
        default_navigate_to=str(data.get("default_navigate_to") or "/"),
        login_config=str(data.get("login_config") or ""),
        body=body.strip(),
    )


# ---------------------------------------------------------------------------
# Fetching from GitHub
# ---------------------------------------------------------------------------


async def fetch_recordloop_md(
    repo: str, token: str, ref: str = "",
) -> Optional[RepoContext]:
    """Fetch .github/recordloop.md (or RECORDLOOP.md) from the repo.

    Returns None if neither candidate file exists.
    """
    from .github_client import fetch_file_contents

    for path in _CANDIDATE_PATHS:
        try:
            raw = await fetch_file_contents(repo, path, token, ref=ref)
        except Exception:
            # Network / API errors — skip this candidate, try the next one.
            continue
        if raw is not None:
            return parse_recordloop_md(raw)
    return None
