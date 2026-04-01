"""
RecordLoop - Developer-first screen recording SDK for any frontend.

Works with React, Vue, Next.js, Vite, Angular, Svelte, and more.
Auto-detects your framework and configures sensible defaults.

Quick start:
    python -m recordloop init        # auto-detect & generate .env
    python -m recordloop report      # visualize recordings

Usage:
    from recordloop import PlaywrightRecorder, RecordLoopConfig

    config = RecordLoopConfig()  # reads .env + detects framework
    with PlaywrightRecorder(config.to_recorder_config()) as recorder:
        page = recorder.start_recording(config.base_url)
        page.click("#button")
        recorder.stop_recording()
        print(recorder.generate_test_code())
"""

# Config (env-aware, framework-detecting)
from .config import (
    RecordLoopConfig,
    detect_framework,
    FRAMEWORK_DEFAULTS,
)

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

# Bridge (JS SDK → Python)
from .bridge import convert_session, replay_session, serve as serve_bridge

# Report
from .report import generate_report

__version__ = "1.0.0"
__all__ = [
    # Config
    "RecordLoopConfig",
    "detect_framework",
    "FRAMEWORK_DEFAULTS",
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
    # Bridge
    "convert_session",
    "replay_session",
    "serve_bridge",
    # Report
    "generate_report",
    # Utilities
    "AutoRecordingHandler",
    "watch_for_changes",
]
