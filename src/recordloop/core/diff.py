"""
recordloop.core.diff
~~~~~~~~~~~~~~~~~~~~
Structural diff of two browser sessions at the semantic-intent level.

Algorithm
---------
Phase 1 — Tokenize
    Each session's actions are run through :class:`~recordloop.core.normalizer.Normalizer`
    to produce a sequence of :class:`~recordloop.core.normalizer.IntentToken` objects.

Phase 2 — Weighted Smith-Waterman local alignment
    A Smith-Waterman dynamic-programming matrix is built over the two token
    sequences using a custom scoring function that awards partial credit for
    matching action types and element fingerprints.  Gap penalty is -0.3.

    After the matrix is filled, the single highest-scoring cell is found and
    the alignment is traced back to yield a list of (index_a, index_b) pairs
    plus the unaligned indices from each side.

Phase 3 — Classify aligned pairs
    Each aligned pair receives a similarity score (0.0 – 1.0) from
    ``_score_tokens``.

    * similarity >= 0.95  →  UNCHANGED
    * 0.40 <= similarity < 0.95  →  MODIFIED
    * similarity < 0.40   →  split into REMOVED (from A) + ADDED (from B)

    Unaligned positions from A become REMOVED; from B become ADDED.

Phase 4 — Build output
    :class:`DiffEntry` objects are collected in source order, wrapped in a
    :class:`DiffSummary`, and returned inside a :class:`SessionDiff`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from .session import Action, Session
from .normalizer import IntentToken, Normalizer


# ---------------------------------------------------------------------------
# ChangeKind
# ---------------------------------------------------------------------------

class ChangeKind(str, Enum):
    UNCHANGED = "unchanged"
    MODIFIED = "modified"   # same element, different value or params
    ADDED = "added"         # in B not in A
    REMOVED = "removed"     # in A not in B


# ---------------------------------------------------------------------------
# DiffEntry
# ---------------------------------------------------------------------------

@dataclass
class DiffEntry:
    """A single entry in the diff result.

    Attributes
    ----------
    kind:
        The nature of the change.
    action_a:
        Action from session A (the baseline).  ``None`` for ADDED entries.
    action_b:
        Action from session B (the candidate).  ``None`` for REMOVED entries.
    similarity:
        A 0.0 – 1.0 similarity score.  Meaningful for MODIFIED entries;
        1.0 for UNCHANGED, 0.0 for pure ADDED / REMOVED.
    """

    kind: ChangeKind
    action_a: Action | None   # None if ADDED
    action_b: Action | None   # None if REMOVED
    similarity: float         # 0.0-1.0, meaningful for MODIFIED

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "action_a": self.action_a.to_dict() if self.action_a is not None else None,
            "action_b": self.action_b.to_dict() if self.action_b is not None else None,
            "similarity": round(self.similarity, 4),
        }


# ---------------------------------------------------------------------------
# DiffSummary
# ---------------------------------------------------------------------------

@dataclass
class DiffSummary:
    """Aggregate statistics for a :class:`SessionDiff`."""

    total_a: int
    total_b: int
    unchanged: int
    modified: int
    added: int
    removed: int
    similarity_score: float   # 0.0-1.0 overall

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_a": self.total_a,
            "total_b": self.total_b,
            "unchanged": self.unchanged,
            "modified": self.modified,
            "added": self.added,
            "removed": self.removed,
            "similarity_score": round(self.similarity_score, 4),
        }


# ---------------------------------------------------------------------------
# SessionDiff
# ---------------------------------------------------------------------------

@dataclass
class SessionDiff:
    """Full diff result between two sessions.

    Attributes
    ----------
    session_a_id:
        ``Session.id`` of the baseline session (A).
    session_b_id:
        ``Session.id`` of the candidate session (B).
    computed_at:
        UTC datetime at which this diff was computed.
    entries:
        Ordered list of :class:`DiffEntry` objects.
    summary:
        Aggregate statistics.
    """

    session_a_id: str
    session_b_id: str
    computed_at: datetime
    entries: list[DiffEntry]
    summary: DiffSummary

    def __len__(self) -> int:
        return len(self.entries)

    @property
    def changes(self) -> list[DiffEntry]:
        """MODIFIED, ADDED, and REMOVED entries only (excludes UNCHANGED)."""
        return [e for e in self.entries if e.kind != ChangeKind.UNCHANGED]

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_a_id": self.session_a_id,
            "session_b_id": self.session_b_id,
            "computed_at": self.computed_at.isoformat(),
            "summary": self.summary.to_dict(),
            "entries": [e.to_dict() for e in self.entries],
        }

    def to_json(self, *, indent: int | None = None) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Scoring function
# ---------------------------------------------------------------------------

# Gap penalty applied in the Smith-Waterman DP matrix.
_GAP_PENALTY: float = -0.3


def _score_tokens(a: IntentToken, b: IntentToken) -> float:
    """Return a similarity score for two IntentTokens.

    The score is used both in the Smith-Waterman DP matrix (where positive
    values attract alignment) and later as the ``DiffEntry.similarity`` field
    (normalised to 0.0 – 1.0).

    Raw ranges
    ----------
    * Type mismatch                          → -0.5
    * Same type, different key_fingerprint   → 0.1  (+0.1 page bonus possible)
    * Same type, same key, different value   → 0.7  (+0.1 page bonus possible)
    * Exact match (type + key + value)       → 1.0  (+0.1 page bonus possible)

    Page-path bonus: +0.1 when ``page_path`` matches (regardless of other
    fields), capped so the total never exceeds 1.0.
    """
    if a.type != b.type:
        return -0.5

    page_bonus: float = 0.1 if a.page_path == b.page_path else 0.0

    if a.key_fingerprint != b.key_fingerprint:
        base = 0.1
    elif a.value_normalized != b.value_normalized:
        base = 0.7
    else:
        base = 1.0

    return min(1.0, base + page_bonus)


def _to_similarity(raw_score: float) -> float:
    """Clamp a raw ``_score_tokens`` value to the [0.0, 1.0] range."""
    return max(0.0, min(1.0, raw_score))


# ---------------------------------------------------------------------------
# Smith-Waterman alignment
# ---------------------------------------------------------------------------

def _smith_waterman(
    tokens_a: list[IntentToken],
    tokens_b: list[IntentToken],
) -> list[tuple[int, int]]:
    """Run Smith-Waterman and return an ordered list of aligned index pairs.

    Each element of the returned list is ``(index_in_a, index_in_b)`` where
    both indices are 0-based.  The alignment is guaranteed to be in ascending
    order for both sequences (no crossing).

    Time complexity:  O(n * m)
    Space complexity: O(n * m)  — acceptable for sessions < 200 actions each.
    """
    m = len(tokens_a)
    n = len(tokens_b)

    if m == 0 or n == 0:
        return []

    # H[i][j] = best local-alignment score ending at (i, j)
    # Using (m+1) x (n+1) table; row 0 and col 0 are 0 (Smith-Waterman init).
    H: list[list[float]] = [[0.0] * (n + 1) for _ in range(m + 1)]

    best_score: float = 0.0
    best_i: int = 0
    best_j: int = 0

    for i in range(1, m + 1):
        for j in range(1, n + 1):
            match_score = H[i - 1][j - 1] + _score_tokens(tokens_a[i - 1], tokens_b[j - 1])
            gap_a = H[i - 1][j] + _GAP_PENALTY   # gap in B (skip a[i-1])
            gap_b = H[i][j - 1] + _GAP_PENALTY   # gap in A (skip b[j-1])
            H[i][j] = max(0.0, match_score, gap_a, gap_b)
            if H[i][j] > best_score:
                best_score = H[i][j]
                best_i = i
                best_j = j

    if best_score <= 0.0:
        # No alignment found — every pairing is unfavourable.
        return []

    # Traceback from (best_i, best_j) until we reach a cell with score 0.
    pairs_reversed: list[tuple[int, int]] = []
    i, j = best_i, best_j

    while i > 0 and j > 0 and H[i][j] > 0.0:
        score_here = H[i][j]
        diag = H[i - 1][j - 1]
        up   = H[i - 1][j]
        left = H[i][j - 1]

        # Reconstruct which move led to H[i][j].
        # Prefer diagonal (match/mismatch) over gap moves to maximise pairs.
        diag_candidate = diag + _score_tokens(tokens_a[i - 1], tokens_b[j - 1])
        up_candidate   = up + _GAP_PENALTY
        left_candidate = left + _GAP_PENALTY

        if abs(score_here - diag_candidate) < 1e-9:
            # Diagonal move — this is an aligned pair.
            pairs_reversed.append((i - 1, j - 1))
            i -= 1
            j -= 1
        elif up_candidate >= left_candidate and abs(score_here - up_candidate) < 1e-9:
            # Gap in B — a[i-1] is unaligned, skip it.
            i -= 1
        else:
            # Gap in A — b[j-1] is unaligned, skip it.
            j -= 1

    pairs_reversed.reverse()
    return pairs_reversed


# ---------------------------------------------------------------------------
# Alignment → DiffEntry list
# ---------------------------------------------------------------------------

def _build_entries(
    tokens_a: list[IntentToken],
    tokens_b: list[IntentToken],
    actions_a: list[Action],
    actions_b: list[Action],
    pairs: list[tuple[int, int]],
) -> list[DiffEntry]:
    """Classify aligned pairs and unaligned indices into DiffEntry objects.

    The output list preserves logical source order: entries are interleaved
    by their position in A (for A-side items) and B (for B-side items),
    matching the intuitive "before / after" reading order.
    """
    aligned_a: set[int] = set()
    aligned_b: set[int] = set()

    # Pre-classify each aligned pair so we can emit entries in order.
    # We'll walk through A's indices 0..m-1 and B's indices 0..n-1 together.
    pair_map: dict[int, int] = {}   # index_a -> index_b (for aligned pairs)
    for ia, ib in pairs:
        pair_map[ia] = ib
        aligned_a.add(ia)
        aligned_b.add(ib)

    entries: list[DiffEntry] = []

    # Cursor into B to know which ADDED items from B come "before" the next
    # pair (so the output order is stable and readable).
    b_emitted: set[int] = set()
    a_ptr = 0

    for ia in range(len(tokens_a)):
        # Emit any B items that are unaligned and whose index is less than
        # the current B-anchor (the ib that ia aligns to, or the next one).
        if ia in pair_map:
            ib_anchor = pair_map[ia]
        else:
            # Find the next aligned B index after the last emitted B item.
            next_ib = next(
                (pair_map[ka] for ka in sorted(pair_map) if ka > ia),
                len(tokens_b),
            )
            ib_anchor = next_ib

        # Flush unaligned B entries that fall before the current B-anchor.
        for ib in range(max(b_emitted) + 1 if b_emitted else 0, ib_anchor):
            if ib not in aligned_b:
                entries.append(DiffEntry(
                    kind=ChangeKind.ADDED,
                    action_a=None,
                    action_b=actions_b[ib],
                    similarity=0.0,
                ))
                b_emitted.add(ib)

        if ia in pair_map:
            ib = pair_map[ia]
            raw = _score_tokens(tokens_a[ia], tokens_b[ib])
            sim = _to_similarity(raw)

            if sim >= 0.95:
                entries.append(DiffEntry(
                    kind=ChangeKind.UNCHANGED,
                    action_a=actions_a[ia],
                    action_b=actions_b[ib],
                    similarity=sim,
                ))
            elif sim >= 0.40:
                entries.append(DiffEntry(
                    kind=ChangeKind.MODIFIED,
                    action_a=actions_a[ia],
                    action_b=actions_b[ib],
                    similarity=sim,
                ))
            else:
                # Similarity too low — treat as independent REMOVED + ADDED.
                entries.append(DiffEntry(
                    kind=ChangeKind.REMOVED,
                    action_a=actions_a[ia],
                    action_b=None,
                    similarity=0.0,
                ))
                entries.append(DiffEntry(
                    kind=ChangeKind.ADDED,
                    action_a=None,
                    action_b=actions_b[ib],
                    similarity=0.0,
                ))
            b_emitted.add(ib)
        else:
            entries.append(DiffEntry(
                kind=ChangeKind.REMOVED,
                action_a=actions_a[ia],
                action_b=None,
                similarity=0.0,
            ))

    # Flush any remaining unaligned B entries that trail after all of A.
    for ib in range(max(b_emitted) + 1 if b_emitted else 0, len(tokens_b)):
        if ib not in aligned_b:
            entries.append(DiffEntry(
                kind=ChangeKind.ADDED,
                action_a=None,
                action_b=actions_b[ib],
                similarity=0.0,
            ))

    return entries


# ---------------------------------------------------------------------------
# Summary helper
# ---------------------------------------------------------------------------

def _build_summary(
    entries: list[DiffEntry],
    total_a: int,
    total_b: int,
) -> DiffSummary:
    unchanged = sum(1 for e in entries if e.kind == ChangeKind.UNCHANGED)
    modified  = sum(1 for e in entries if e.kind == ChangeKind.MODIFIED)
    added     = sum(1 for e in entries if e.kind == ChangeKind.ADDED)
    removed   = sum(1 for e in entries if e.kind == ChangeKind.REMOVED)

    denom = max(total_a, total_b)
    similarity_score = (unchanged / denom) if denom > 0 else 1.0

    return DiffSummary(
        total_a=total_a,
        total_b=total_b,
        unchanged=unchanged,
        modified=modified,
        added=added,
        removed=removed,
        similarity_score=similarity_score,
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def diff_sessions(
    a: Session,
    b: Session,
    *,
    normalizer: Normalizer | None = None,
) -> SessionDiff:
    """Compare sessions *a* (baseline) and *b* (candidate).

    Parameters
    ----------
    a:
        Baseline / reference session.
    b:
        Candidate / new session to compare against *a*.
    normalizer:
        Optional custom :class:`~recordloop.core.normalizer.Normalizer`.
        A default instance is created if not supplied.

    Returns
    -------
    SessionDiff
        Full diff result with per-action entries and aggregate statistics.

    Algorithm complexity
    --------------------
    O(n * m) time and space, where n and m are the action counts of *a* and
    *b* respectively.  Sessions are typically < 200 actions so correctness
    is prioritised over micro-optimisation.
    """
    norm = normalizer or Normalizer()

    # Phase 1 — Tokenize.
    tokens_a: list[IntentToken] = [norm.normalize(act) for act in a.actions]
    tokens_b: list[IntentToken] = [norm.normalize(act) for act in b.actions]

    # Phase 2 — Smith-Waterman alignment.
    pairs = _smith_waterman(tokens_a, tokens_b)

    # Phase 3 + 4 — Classify and build entries.
    entries = _build_entries(tokens_a, tokens_b, a.actions, b.actions, pairs)

    summary = _build_summary(entries, len(a.actions), len(b.actions))

    return SessionDiff(
        session_a_id=a.id,
        session_b_id=b.id,
        computed_at=datetime.now(tz=timezone.utc),
        entries=entries,
        summary=summary,
    )
