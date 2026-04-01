"""
Playwright Recorder - Lightweight Playwright abstraction for test recording.

A simple, intuitive API for recording Playwright actions, generating test code,
and capturing videos of test runs.

Usage:
    from playwright_recorder import PlaywrightRecorder, recordable, video_capture

    # Basic recording
    with PlaywrightRecorder() as recorder:
        page = recorder.start_recording("https://example.com")
        page.click("#button")
        recorder.stop_recording()
        code = recorder.generate_test_code()
"""

# Core recorder
from .recorder import (
    PlaywrightRecorder,
    RecorderConfig,
    RecordedAction,
    ActionType,
    AutoRecordingHandler,
    watch_for_changes,
)

# Test runner
from .test_runner import (
    TestRunner,
    TestResult,
    VideoRecorder,
    run_test_with_video,
)

# Decorators
from .decorators import (
    recordable,
    video_capture,
    watch_changes,
    step,
    RecordedTest,
    replay,
    DecoratorConfig,
)

__version__ = "1.0.0"
__all__ = [
    # Core
    "PlaywrightRecorder",
    "RecorderConfig",
    "RecordedAction",
    "ActionType",
    # Test runner
    "TestRunner",
    "TestResult",
    "VideoRecorder",
    "run_test_with_video",
    # Decorators
    "recordable",
    "video_capture",
    "watch_changes",
    "step",
    "RecordedTest",
    "replay",
    "DecoratorConfig",
    # Utilities
    "AutoRecordingHandler",
    "watch_for_changes",
]
