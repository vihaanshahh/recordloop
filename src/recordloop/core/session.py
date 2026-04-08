"""
recordloop.core.session
~~~~~~~~~~~~~~~~~~~~~~~
Core data types for recorded browser sessions.

Schema versions:
  v1 - flat selector string on each action
  v2 - structured SemanticKey dict on each action  (current)
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# ActionType
# ---------------------------------------------------------------------------

class ActionType(str, Enum):
    """All browser interaction categories.

    Inheriting from ``str`` means instances serialise as plain strings, e.g.
    ``json.dumps(ActionType.CLICK)`` produces ``"click"`` not
    ``"ActionType.CLICK"``.
    """

    CLICK = "click"
    DBLCLICK = "dblclick"
    TYPE = "type"
    FILL = "fill"
    SELECT = "select"
    CHECK = "check"
    UNCHECK = "uncheck"
    HOVER = "hover"
    FOCUS = "focus"
    BLUR = "blur"
    SCROLL = "scroll"
    NAVIGATE = "navigate"
    SUBMIT = "submit"
    KEY_DOWN = "keydown"
    KEY_UP = "keyup"
    DRAG = "drag"
    DROP = "drop"
    RIGHT_CLICK = "rightclick"
    SCREENSHOT = "screenshot"
    WAIT = "wait"
    CUSTOM = "custom"


# ---------------------------------------------------------------------------
# SemanticKey
# ---------------------------------------------------------------------------

_VALID_STRATEGIES = frozenset(
    {"testid", "id", "name", "aria_label", "role_text", "xpath"}
)


@dataclass(frozen=True)
class SemanticKey:
    """A stable, semantic selector for a DOM element.

    ``strategy`` describes how the element is identified:

    * ``testid``    — ``data-testid`` / ``data-test-id`` attribute
    * ``id``        — HTML ``id`` attribute
    * ``name``      — ``name`` attribute (inputs, forms)
    * ``aria_label``— ``aria-label`` / ``aria-labelledby`` text
    * ``role_text`` — ARIA role + visible text, e.g. ``button:Submit``
    * ``xpath``     — full XPath expression (fallback)
    """

    strategy: str           # one of _VALID_STRATEGIES
    value: str              # the selector value
    tag: str | None = None  # lowercase HTML tag hint, e.g. "button"
    text: str | None = None # visible text content hint

    def __post_init__(self) -> None:
        if self.strategy not in _VALID_STRATEGIES:
            raise ValueError(
                f"Invalid strategy {self.strategy!r}. "
                f"Must be one of: {sorted(_VALID_STRATEGIES)}"
            )
        if not self.value:
            raise ValueError("SemanticKey.value must be a non-empty string")

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "value": self.value,
            "tag": self.tag,
            "text": self.text,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SemanticKey:
        return cls(
            strategy=d["strategy"],
            value=d["value"],
            tag=d.get("tag"),
            text=d.get("text"),
        )


# ---------------------------------------------------------------------------
# v1 backwards-compat selector parser
# ---------------------------------------------------------------------------

def _parse_v1_selector(selector: str) -> SemanticKey:
    """Best-effort conversion of a flat CSS/text selector string to SemanticKey.

    Handles the most common patterns emitted by the v1 recorder:
      ``[data-testid="foo"]``  -> strategy=testid
      ``#my-id``               -> strategy=id
      ``[name="email"]``       -> strategy=name
      ``[aria-label="Close"]`` -> strategy=aria_label
      ``:has-text("Submit")``  -> strategy=role_text
      anything else            -> strategy=xpath
    """
    s = selector.strip()

    # Helper: extract the value from  [attr="value"]  [attr='value']  [attr=bare]
    def _attr(attr: str, text: str) -> re.Match | None:  # type: ignore[type-arg]
        # Quoted form first (handles spaces/brackets inside quotes)
        m2 = re.search(
            r'\[' + re.escape(attr) + r'=["\']([^"\']+)["\']',
            text, re.I,
        )
        if m2:
            return m2
        # Bare (unquoted) form — value ends at ] or whitespace
        return re.search(
            r'\[' + re.escape(attr) + r'=([^\]"\'>\s]+)',
            text, re.I,
        )

    # data-testid / data-test-id
    m = _attr("data-testid", s) or _attr("data-test-id", s)
    if m:
        return SemanticKey(strategy="testid", value=m.group(1))

    # #id shorthand
    m = re.match(r'^#([\w-]+)$', s)
    if m:
        return SemanticKey(strategy="id", value=m.group(1))

    # [id="..."]
    m = _attr("id", s)
    if m:
        return SemanticKey(strategy="id", value=m.group(1))

    # [name="..."]
    m = _attr("name", s)
    if m:
        return SemanticKey(strategy="name", value=m.group(1))

    # [aria-label="..."]
    m = _attr("aria-label", s)
    if m:
        return SemanticKey(strategy="aria_label", value=m.group(1))

    # :has-text("...") — Playwright text pseudo-class
    m = re.search(r':has-text\(["\']([^"\']+)["\']\)', s)
    if m:
        return SemanticKey(strategy="role_text", value=m.group(1))

    # text= shorthand (Playwright v1 style)
    m = re.match(r'^text=(.+)$', s, re.I)
    if m:
        return SemanticKey(strategy="role_text", value=m.group(1).strip('"\''))

    # Everything else falls through to xpath / generic
    return SemanticKey(strategy="xpath", value=s)


# ---------------------------------------------------------------------------
# Action
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Action:
    """A single recorded browser interaction."""

    id: str
    timestamp_ms: int
    type: ActionType
    key: SemanticKey | None = None
    value: str | None = None
    page_url: str | None = None
    page_title: str | None = None
    scroll_x: int | None = None
    scroll_y: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "timestamp_ms": self.timestamp_ms,
            "type": self.type,          # str via ActionType.__str__
            "key": self.key.to_dict() if self.key is not None else None,
            "value": self.value,
            "page_url": self.page_url,
            "page_title": self.page_title,
            "scroll_x": self.scroll_x,
            "scroll_y": self.scroll_y,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Action:
        # -- Resolve SemanticKey (v2) or fall back to v1 flat selector -------
        key: SemanticKey | None = None
        if "key" in d and d["key"] is not None:
            key = SemanticKey.from_dict(d["key"])
        elif "selector" in d and d["selector"]:
            # v1 backwards compatibility
            key = _parse_v1_selector(d["selector"])

        return cls(
            id=d.get("id") or str(uuid.uuid4()),
            timestamp_ms=int(d["timestamp_ms"]),
            type=ActionType(d["type"]),
            key=key,
            value=d.get("value"),
            page_url=d.get("page_url"),
            page_title=d.get("page_title"),
            scroll_x=d.get("scroll_x"),
            scroll_y=d.get("scroll_y"),
        )


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------

@dataclass
class Session:
    """A complete recorded browser session.

    Attributes
    ----------
    id:
        Unique session identifier (UUID4 recommended).
    recorded_at:
        UTC datetime when the session began.
    duration_ms:
        Total wall-clock duration of the recording in milliseconds.
    base_url:
        The origin URL of the application under test.
    actions:
        Ordered list of recorded actions.
    viewport:
        Browser viewport size as ``(width, height)``.  Defaults to 1280×720.
    user_agent:
        Optional ``User-Agent`` string from the recording browser.
    meta:
        Arbitrary key/value metadata (test name, branch, CI run-id, etc.).
    schema_version:
        Data format version; ``"2"`` is current.
    """

    id: str
    recorded_at: datetime
    duration_ms: int
    base_url: str
    actions: list[Action]
    viewport: tuple[int, int] = (1280, 720)
    user_agent: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    schema_version: str = "2"

    # ------------------------------------------------------------------
    # Dunder helpers
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        return len(self.actions)

    def __repr__(self) -> str:
        return (
            f"Session(id={self.id!r}, actions={len(self.actions)}, "
            f"recorded_at={self.recorded_at.isoformat()!r})"
        )

    # ------------------------------------------------------------------
    # Serialisation helpers
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "recorded_at": self.recorded_at.isoformat(),
            "duration_ms": self.duration_ms,
            "base_url": self.base_url,
            "viewport": list(self.viewport),
            "user_agent": self.user_agent,
            "meta": self.meta,
            "actions": [a.to_dict() for a in self.actions],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Session:
        # Parse recorded_at — accept both tz-aware and naive ISO strings
        raw_dt = d["recorded_at"]
        if isinstance(raw_dt, datetime):
            recorded_at = raw_dt
        else:
            # Python 3.10 fromisoformat does not handle trailing 'Z'
            recorded_at = datetime.fromisoformat(raw_dt.replace("Z", "+00:00"))

        # Ensure UTC awareness; if naive, assume UTC
        if recorded_at.tzinfo is None:
            recorded_at = recorded_at.replace(tzinfo=timezone.utc)

        # viewport — stored as list in JSON, desired as tuple
        raw_vp = d.get("viewport", [1280, 720])
        viewport: tuple[int, int] = (int(raw_vp[0]), int(raw_vp[1]))

        actions = [Action.from_dict(a) for a in d.get("actions", [])]

        return cls(
            id=d.get("id") or str(uuid.uuid4()),
            recorded_at=recorded_at,
            duration_ms=int(d.get("duration_ms", 0)),
            base_url=d.get("base_url", ""),
            actions=actions,
            viewport=viewport,
            user_agent=d.get("user_agent"),
            meta=dict(d.get("meta", {})),
            schema_version=str(d.get("schema_version", "1")),
        )

    def to_json(self, *, indent: int | None = None) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    @classmethod
    def from_json(cls, s: str) -> Session:
        return cls.from_dict(json.loads(s))
