"""
Integration tests for recordloop.

Each test uses the record_failure fixture — if a test fails, its steps are
saved as a recordloop Session so you can inspect what went wrong:

    recordloop sessions list
    recordloop sessions show fail-<id>

Run:
    PYTHONPATH=src pytest tests/ -v
"""

import json
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path

import pytest

from recordloop.core.diff import ChangeKind, diff_sessions
from recordloop.core.normalizer import Normalizer
from recordloop.core.session import Action, ActionType, SemanticKey, Session
from recordloop.bridge.server import BridgeServer


# ── helpers ──────────────────────────────────────────────────────────────────

def make_session(id: str, actions: list[Action], base_url: str = "http://localhost:3000") -> Session:
    return Session(
        id=id,
        recorded_at=datetime.now(),
        duration_ms=2000,
        base_url=base_url,
        actions=actions,
    )


def click(testid: str, page: str = "http://localhost:3000") -> Action:
    return Action(
        id=testid,
        timestamp_ms=100,
        type=ActionType.CLICK,
        key=SemanticKey(strategy="testid", value=testid, tag="button"),
        page_url=page,
    )


def navigate(url: str) -> Action:
    return Action(id="nav", timestamp_ms=0, type=ActionType.NAVIGATE, value=url, page_url=url)


def type_into(testid: str, text: str) -> Action:
    return Action(
        id=testid,
        timestamp_ms=200,
        type=ActionType.TYPE,
        key=SemanticKey(strategy="testid", value=testid, tag="input"),
        value=text,
        page_url="http://localhost:3000",
    )


# ── Session serialization ─────────────────────────────────────────────────────

def test_session_v2_roundtrip(record_failure):
    record_failure.step("build session", "create Session with 3 actions")
    session = make_session("test-abc", [
        navigate("http://localhost:3000"),
        click("submit-btn"),
        type_into("email-input", "user@example.com"),
    ])

    record_failure.step("serialize to JSON")
    raw = session.to_json()
    assert isinstance(raw, str)
    data = json.loads(raw)
    assert data["schema_version"] == "2"
    assert len(data["actions"]) == 3

    record_failure.step("deserialize from JSON")
    restored = Session.from_json(raw)

    record_failure.step("assert fields match")
    assert restored.id == session.id
    assert restored.base_url == session.base_url
    assert len(restored.actions) == 3

    record_failure.step("assert action types preserved")
    assert restored.actions[0].type == ActionType.NAVIGATE
    assert restored.actions[1].type == ActionType.CLICK
    assert restored.actions[2].type == ActionType.TYPE

    record_failure.step("assert SemanticKey preserved")
    assert restored.actions[1].key.strategy == "testid"
    assert restored.actions[1].key.value == "submit-btn"


def test_session_v1_legacy_deserialization(record_failure):
    """Old sessions used flat selector strings. They must still load."""
    record_failure.step("build v1 session dict", "mimics what the old JS SDK posted")
    v1_dict = {
        "id": "legacy-001",
        "recorded_at": datetime.now().isoformat(),
        "duration_ms": 1500,
        "base_url": "http://localhost:3000",
        "viewport": [1280, 720],
        "schema_version": "1",
        "meta": {},
        "actions": [
            {
                "id": "a1",
                "timestamp_ms": 0,
                "type": "navigate",
                "selector": None,
                "value": "http://localhost:3000",
                "page_url": "http://localhost:3000",
            },
            {
                "id": "a2",
                "timestamp_ms": 800,
                "type": "click",
                "selector": "[data-testid='checkout']",   # v1 format
                "value": None,
                "page_url": "http://localhost:3000/cart",
            },
            {
                "id": "a3",
                "timestamp_ms": 1200,
                "type": "click",
                "selector": "#submit",                    # v1 id selector
                "value": None,
                "page_url": "http://localhost:3000/cart",
            },
            {
                "id": "a4",
                "timestamp_ms": 1400,
                "type": "click",
                "selector": ":has-text('Continue')",      # v1 Playwright pseudo
                "value": None,
                "page_url": "http://localhost:3000/cart",
            },
        ],
    }

    record_failure.step("deserialize v1 dict")
    session = Session.from_dict(v1_dict)

    record_failure.step("assert actions loaded", f"{len(session.actions)} actions")
    assert len(session.actions) == 4

    record_failure.step("assert data-testid parsed to SemanticKey")
    checkout_action = session.actions[1]
    assert checkout_action.key is not None
    assert checkout_action.key.strategy == "testid"
    assert checkout_action.key.value == "checkout"

    record_failure.step("assert #id parsed to SemanticKey")
    submit_action = session.actions[2]
    assert submit_action.key is not None
    assert submit_action.key.strategy == "id"
    assert submit_action.key.value == "submit"

    record_failure.step("assert :has-text parsed to SemanticKey")
    continue_action = session.actions[3]
    assert continue_action.key is not None
    assert continue_action.key.strategy == "role_text"
    assert continue_action.key.value == "Continue"


# ── Diff algorithm ────────────────────────────────────────────────────────────

def test_diff_identical_sessions(record_failure):
    record_failure.step("create two identical sessions")
    actions = [navigate("http://localhost:3000"), click("submit")]
    a = make_session("a", actions)
    b = make_session("b", actions)

    record_failure.step("diff them")
    result = diff_sessions(a, b)

    record_failure.step("assert 100% similarity")
    assert result.summary.similarity_score == 1.0

    record_failure.step("assert all entries are UNCHANGED")
    kinds = {e.kind for e in result.entries}
    assert kinds == {ChangeKind.UNCHANGED}

    record_failure.step("assert summary counts")
    assert result.summary.unchanged == 2
    assert result.summary.modified == 0
    assert result.summary.added == 0
    assert result.summary.removed == 0


def test_diff_detects_modified_step(record_failure):
    """Same element, different typed value → MODIFIED (not remove+add)."""
    record_failure.step("build session A", "types 'hello' into search")
    a = make_session("a", [
        navigate("http://localhost:3000"),
        type_into("search-input", "hello"),
        click("search-btn"),
    ])

    record_failure.step("build session B", "types 'world' into search")
    b = make_session("b", [
        navigate("http://localhost:3000"),
        type_into("search-input", "world"),
        click("search-btn"),
    ])

    record_failure.step("diff sessions")
    result = diff_sessions(a, b)

    record_failure.step("assert modified entry exists")
    modified = [e for e in result.entries if e.kind == ChangeKind.MODIFIED]
    assert len(modified) >= 1, "Expected at least one MODIFIED entry for the changed input value"

    record_failure.step("assert modified entry has both sides")
    m = modified[0]
    assert m.action_a is not None
    assert m.action_b is not None

    record_failure.step("assert similarity > 0 (same element, different value)")
    assert m.similarity > 0.0


def test_diff_detects_added_step(record_failure):
    """Session B has an extra step that A doesn't → ADDED."""
    record_failure.step("build session A", "2 steps")
    a = make_session("a", [
        navigate("http://localhost:3000"),
        click("login-btn"),
    ])

    record_failure.step("build session B", "3 steps — extra click added")
    b = make_session("b", [
        navigate("http://localhost:3000"),
        click("login-btn"),
        click("dashboard-link"),   # new step
    ])

    record_failure.step("diff sessions")
    result = diff_sessions(a, b)

    record_failure.step("assert one ADDED entry")
    added = [e for e in result.entries if e.kind == ChangeKind.ADDED]
    assert len(added) == 1

    record_failure.step("assert added action has correct testid")
    assert added[0].action_b.key.value == "dashboard-link"


def test_diff_detects_removed_step(record_failure):
    """Session A has a step that B removed → REMOVED."""
    record_failure.step("build session A", "3 steps")
    a = make_session("a", [
        navigate("http://localhost:3000"),
        click("cookie-banner-close"),  # this got removed
        click("login-btn"),
    ])

    record_failure.step("build session B", "2 steps — banner gone")
    b = make_session("b", [
        navigate("http://localhost:3000"),
        click("login-btn"),
    ])

    record_failure.step("diff sessions")
    result = diff_sessions(a, b)

    record_failure.step("assert one REMOVED entry")
    removed = [e for e in result.entries if e.kind == ChangeKind.REMOVED]
    assert len(removed) == 1

    record_failure.step("assert removed action is the banner close")
    assert removed[0].action_a.key.value == "cookie-banner-close"


def test_diff_completely_different_sessions(record_failure):
    record_failure.step("build two unrelated sessions")
    a = make_session("a", [click("add-to-cart"), click("checkout")])
    b = make_session("b", [click("sign-up"), click("verify-email")])

    record_failure.step("diff them")
    result = diff_sessions(a, b)

    record_failure.step("assert low similarity")
    assert result.summary.similarity_score < 0.5

    record_failure.step("assert no UNCHANGED entries")
    assert result.summary.unchanged == 0


# ── Normalizer ────────────────────────────────────────────────────────────────

def test_normalizer_fingerprint_stability(record_failure):
    record_failure.step("create two identical SemanticKeys")
    key1 = SemanticKey(strategy="testid", value="submit-btn", tag="button")
    key2 = SemanticKey(strategy="testid", value="submit-btn", tag="input")  # different tag

    record_failure.step("compute fingerprints")
    n = Normalizer()
    fp1 = n.key_fingerprint(key1)
    fp2 = n.key_fingerprint(key2)

    record_failure.step("assert fingerprints match (tag is ignored in fingerprint)")
    assert fp1 == fp2, "Fingerprint should only depend on strategy+value, not tag"

    record_failure.step("assert different values produce different fingerprints")
    key3 = SemanticKey(strategy="testid", value="cancel-btn", tag="button")
    fp3 = n.key_fingerprint(key3)
    assert fp1 != fp3


def test_normalizer_url_path_strips_query(record_failure):
    record_failure.step("normalize URL with query string")
    n = Normalizer()
    path = n.normalize_url_path("http://localhost:3000/checkout?step=2&ref=home")

    record_failure.step("assert only path returned")
    assert path == "/checkout"
    assert "?" not in path
    assert "step" not in path


# ── Bridge server ─────────────────────────────────────────────────────────────

@pytest.fixture
def bridge(tmp_path):
    """Start a bridge server on a random-ish port, yield the server, then stop it."""
    server = BridgeServer(port=18787, sessions_dir=tmp_path / "sessions")
    thread = server.start_background()
    time.sleep(0.15)  # let the server bind
    yield server
    server.stop()


def _post(url: str, body: dict) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())


def _get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=5) as r:
        return json.loads(r.read())


def test_bridge_health(record_failure, bridge):
    record_failure.step("hit /health endpoint")
    resp = _get("http://localhost:18787/health")

    record_failure.step("assert ok=true")
    assert resp["ok"] is True


def test_bridge_post_session(record_failure, bridge):
    record_failure.step("build a session dict to POST")
    session = make_session("bridge-test-001", [
        navigate("http://localhost:3000"),
        click("login-btn"),
    ])
    payload = session.to_dict()

    record_failure.step("POST to /session")
    resp = _post("http://localhost:18787/session", payload)

    record_failure.step("assert response contains session_id")
    assert resp["session_id"] == "bridge-test-001"
    assert resp["actions"] == 2

    record_failure.step("assert session file was written to disk")
    sessions_dir = bridge.sessions_dir
    saved = list(sessions_dir.glob("*.json"))
    assert len(saved) == 1


def test_bridge_sessions_list(record_failure, bridge):
    record_failure.step("POST two sessions")
    for sid in ["list-test-a", "list-test-b"]:
        s = make_session(sid, [click("btn")])
        _post("http://localhost:18787/session", s.to_dict())

    record_failure.step("GET /sessions")
    resp = _get("http://localhost:18787/sessions")

    record_failure.step("assert both sessions appear")
    ids = {s["id"] for s in resp["sessions"]}
    assert "list-test-a" in ids
    assert "list-test-b" in ids


def test_bridge_diff_endpoint(record_failure, bridge):
    record_failure.step("POST session A — clicks submit")
    a = make_session("diff-a", [navigate("http://localhost:3000"), click("submit")])
    _post("http://localhost:18787/session", a.to_dict())

    record_failure.step("POST session B — clicks checkout instead")
    b = make_session("diff-b", [navigate("http://localhost:3000"), click("checkout")])
    _post("http://localhost:18787/session", b.to_dict())

    record_failure.step("POST to /diff")
    resp = _post("http://localhost:18787/diff", {"session_a": "diff-a", "session_b": "diff-b"})

    record_failure.step("assert diff response has summary")
    assert "summary" in resp
    assert "entries" in resp

    record_failure.step("assert similarity < 1.0 (sessions differ)")
    assert resp["summary"]["similarity_score"] < 1.0

    record_failure.step("assert session IDs correct in response")
    assert resp["session_a_id"] == "diff-a"
    assert resp["session_b_id"] == "diff-b"
