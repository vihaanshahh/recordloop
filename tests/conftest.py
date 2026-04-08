"""
pytest configuration + failure recorder fixture.

When a test fails, the steps it recorded are saved as a real recordloop
Session to .recordloop/sessions/test-failures/. This is the tool eating
its own dogfood — test failures are first-class recordloop sessions.
"""

import uuid
from datetime import datetime
from pathlib import Path

import pytest

from recordloop.core.session import Action, ActionType, SemanticKey, Session


class TestRecorder:
    """
    Records test steps as recordloop Actions.
    Call .step() at each meaningful point in your test.
    On failure, .save_failure() writes a Session JSON you can inspect with
    `recordloop sessions show <id>` or diff against a passing run.
    """

    def __init__(self, test_name: str):
        self.test_name = test_name
        self._actions: list[Action] = []
        self._start = datetime.now()
        self._counter = 0

    def step(self, description: str, detail: str = "") -> None:
        """Record a named step. Call this like a breadcrumb trail."""
        self._counter += 1
        elapsed_ms = int((datetime.now() - self._start).total_seconds() * 1000)
        self._actions.append(
            Action(
                id=f"step-{self._counter:03d}",
                timestamp_ms=elapsed_ms,
                type=ActionType.CLICK,  # generic "something happened"
                key=SemanticKey(
                    strategy="testid",
                    value=f"step-{self._counter}",
                    tag="test",
                    text=description,
                ),
                value=detail or None,
                page_url=f"test://{self.test_name}",
                page_title=description,
            )
        )

    def save_failure(self, error: str) -> Path:
        sessions_dir = Path(".recordloop/sessions/test-failures")
        sessions_dir.mkdir(parents=True, exist_ok=True)

        session = Session(
            id=f"fail-{uuid.uuid4().hex[:8]}",
            recorded_at=self._start,
            duration_ms=int((datetime.now() - self._start).total_seconds() * 1000),
            base_url=f"test://{self.test_name}",
            actions=self._actions,
            meta={
                "type": "test_failure",
                "test_name": self.test_name,
                "error": error[:500],  # truncate long tracebacks
            },
        )
        path = sessions_dir / f"{session.id}.json"
        path.write_text(session.to_json())
        return path


@pytest.fixture
def record_failure(request):
    recorder = TestRecorder(request.node.name)
    yield recorder
    # runs after the test body — check if it failed
    report = getattr(request.node, "rep_call", None)
    if report is not None and report.failed:
        error = str(report.longrepr)
        saved = recorder.save_failure(error)
        # pytest captures stdout, so use the warning system instead
        request.node.warn(
            pytest.PytestWarning(f"[recordloop] failure saved → {saved}")
        )


# hook so rep_call is set before the fixture teardown runs
@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    setattr(item, f"rep_{rep.when}", rep)
