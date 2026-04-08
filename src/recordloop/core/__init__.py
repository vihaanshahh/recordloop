"""
recordloop.core
~~~~~~~~~~~~~~~
Public re-exports for the recordloop core data model and algorithms.
"""

from .session import Session, Action, SemanticKey, ActionType
from .normalizer import Normalizer, IntentToken
from .diff import diff_sessions, SessionDiff, DiffEntry, ChangeKind, DiffSummary

__all__ = [
    # session types
    "Session",
    "Action",
    "SemanticKey",
    "ActionType",
    # normalizer
    "Normalizer",
    "IntentToken",
    # diff
    "diff_sessions",
    "SessionDiff",
    "DiffEntry",
    "ChangeKind",
    "DiffSummary",
]
