"""
recordloop.core.normalizer
~~~~~~~~~~~~~~~~~~~~~~~~~~
Converts raw Actions into stable IntentTokens for diffing.

An IntentToken is a content-addressed representation of *what the user
intended* — stripped of ephemeral noise (full URLs with query strings,
exact casing, leading/trailing whitespace, etc.).  Two actions that map
to the same IntentToken are considered semantically equivalent.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from urllib.parse import urlparse

from .session import Action, ActionType, SemanticKey


# ---------------------------------------------------------------------------
# IntentToken
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class IntentToken:
    """Normalised, content-addressed representation of a single action.

    Attributes
    ----------
    type:
        The action category (same as ``Action.type``).
    key_fingerprint:
        A short, stable hex digest that uniquely identifies the target
        element's semantic selector (strategy + value).  Empty string when
        the action has no associated element (e.g. navigate, scroll).
    page_path:
        URL path component only — query strings and fragments are stripped
        so that sessions recorded against different environments can still
        be compared structurally.
    value_normalized:
        Input value, lowercased and stripped of surrounding whitespace.
        ``None`` for actions that carry no value (clicks, hovers, etc.).
    """

    type: ActionType
    key_fingerprint: str
    page_path: str
    value_normalized: str | None


# ---------------------------------------------------------------------------
# Normalizer
# ---------------------------------------------------------------------------

class Normalizer:
    """Stateless transformer: ``Action`` → ``IntentToken``.

    Usage::

        n = Normalizer()
        tokens = [n.normalize(a) for a in session.actions]
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def normalize(self, action: Action) -> IntentToken:
        """Convert *action* into a stable :class:`IntentToken`."""
        return IntentToken(
            type=action.type,
            key_fingerprint=self.key_fingerprint(action.key),
            page_path=self.normalize_url_path(action.page_url or ""),
            value_normalized=self.normalize_value(action.value),
        )

    def normalize_url_path(self, url: str) -> str:
        """Return only the path component of *url*.

        Strips scheme, host, query string, and fragment so that::

            https://staging.example.com/checkout?ref=email#top

        becomes::

            /checkout

        Empty or un-parseable strings return ``"/"`` as a safe fallback.
        """
        if not url:
            return "/"
        try:
            parsed = urlparse(url)
            path = parsed.path or "/"
            # Normalise double-slashes and trailing slash (preserve root "/")
            # e.g. /foo/bar/ -> /foo/bar  but  / stays /
            if len(path) > 1 and path.endswith("/"):
                path = path.rstrip("/")
            return path if path else "/"
        except Exception:
            return "/"

    def normalize_value(self, value: str | None) -> str | None:
        """Lowercase and strip *value*; return ``None`` if the result is empty."""
        if value is None:
            return None
        stripped = value.strip().lower()
        return stripped if stripped else None

    def key_fingerprint(self, key: SemanticKey | None) -> str:
        """Return a stable, short hex digest for *key*.

        The digest is computed from ``strategy + ":" + value`` so that two
        selectors pointing to the same logical element always produce the
        same fingerprint, regardless of tag or text hints (which can change
        across builds).

        Returns an empty string when *key* is ``None``.
        """
        if key is None:
            return ""
        raw = f"{key.strategy}:{key.value}".encode("utf-8")
        # 16-hex chars (64 bits) — low collision probability, compact diffs
        return hashlib.sha256(raw).hexdigest()[:16]
