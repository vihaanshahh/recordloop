"""
Login automation — v1: storage-state only.

Decodes a base64-encoded Playwright storageState.json from a RECORDLOOP_*
environment variable and returns it as a dict that Playwright's
browser.new_context(storage_state=...) can consume directly.

The decoded content is never logged, never sent to the LLM, and never
included in PR comments.
"""

from __future__ import annotations

import base64
import json
import os


def decode_storage_state(base64_encoded: str) -> dict:
    """Decode a base64-encoded Playwright storageState JSON.

    Raises ValueError if decoding or JSON parsing fails, or if the result
    doesn't look like a valid Playwright storage state.
    """
    # Fix missing padding — common when values are pasted from web UIs or
    # environment managers that strip trailing '=' characters.
    padded = base64_encoded + "=" * (-len(base64_encoded) % 4)
    try:
        decoded = base64.b64decode(padded).decode("utf-8")
    except Exception as e:
        raise ValueError(
            f"could not base64-decode storage state: {e}  "
            "(hint: make sure the value is plain base64, not a file path)"
        ) from e

    try:
        state = json.loads(decoded)
    except json.JSONDecodeError:
        # Don't include the original error — it contains a snippet of the
        # decoded content which may include session tokens / cookies.
        raise ValueError("storage state is not valid JSON (decoded base64 is not parseable)")

    if not isinstance(state, dict) or "cookies" not in state:
        raise ValueError(
            "storage state doesn't look like a Playwright storageState "
            "(expected a JSON object with a `cookies` key)"
        )

    return state


def resolve_storage_state(
    env_var: str = "RECORDLOOP_STORAGE_STATE",
) -> dict | None:
    """Read and decode storage state from an environment variable.

    Returns None if the env var is unset or empty.
    Raises ValueError if set but cannot be decoded.
    """
    encoded = os.environ.get(env_var, "").strip()
    if not encoded:
        return None
    return decode_storage_state(encoded)
