"""
recordloop
~~~~~~~~~~
Structured behavioral diff for browser sessions.
Local-first, no cloud required.

Quick start::

    from recordloop import Session, diff_sessions

    before = Session.from_json(open("baseline.json").read())
    after  = Session.from_json(open("candidate.json").read())

    diff = diff_sessions(before, after)
    print(diff.summary)
"""

from recordloop.core import (
    Session,
    Action,
    SemanticKey,
    ActionType,
    diff_sessions,
    SessionDiff,
    DiffEntry,
    ChangeKind,
    DiffSummary,
    Normalizer,
    IntentToken,
)

__version__ = "2.0.0"
__author__ = "recordloop contributors"

__all__ = [
    "__version__",
    # session types
    "Session",
    "Action",
    "SemanticKey",
    "ActionType",
    # diff
    "diff_sessions",
    "SessionDiff",
    "DiffEntry",
    "ChangeKind",
    "DiffSummary",
    # normalizer (advanced use)
    "Normalizer",
    "IntentToken",
]
