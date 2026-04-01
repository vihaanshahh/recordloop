#!/usr/bin/env python3
"""
Example usage of the Playwright Recorder library.

This file demonstrates:
1. Basic recording and test generation
2. Using decorators
3. Running with video capture
4. Watch mode for auto-re-running tests
5. Context manager pattern
"""

import time
from pathlib import Path

from playwright_recorder import (
    PlaywrightRecorder,
    RecorderConfig,
    TestRunner,
    ActionType,
    recordable,
    video_capture,
    watch_changes,
    RecordedTest,
    replay,
)


def example_basic_recording():
    """
    Basic example: Record actions and generate test code.
    
    This is the simplest way to use the recorder.
    """
    print("\n" + "=" * 60)
    print("Example 1: Basic Recording")
    print("=" * 60)
    
    # Configure the recorder
    config = RecorderConfig(
        base_url="http://localhost:3000",
        video_dir="test-videos",
        test_output_dir="generated-tests",
        headless=True,
    )
    
    with PlaywrightRecorder(config) as recorder:
        # Start recording - optionally navigate to a URL
        page = recorder.start_recording("https://example.com")
        
        # Perform actions - these get recorded automatically
        # Note: For automatic recording, you need to use the recorder's methods
        # or wrap the page with action tracking
        
        # For now, we'll record actions manually
        recorder.record_navigate("https://example.com")
        recorder.record_click("body")
        recorder.record_type("input[name='search']", "Playwright")
        
        # Stop recording
        actions = recorder.stop_recording()
        
        print(f"\n📝 Recorded {len(actions)} actions:")
        for action in actions:
            print(f"  - {action.action_type.value}: {action.selector or action.value}")
        
        # Generate test code
        code = recorder.generate_test_code(test_name="test_example_search")
        print("\n📄 Generated test code:")
        print("-" * 40)
        print(code)
        
        # Save the test code
        saved_path = recorder.save_test_code()
        print(f"\n💾 Test saved to: {saved_path}")


def example_with_decorators():
    """
    Example using decorators for clean test code.
    
    Decorators provide a declarative way to add recording,
    video capture, and watch functionality to tests.
    """
    print("\n" + "=" * 60)
    print("Example 2: Using Decorators")
    print("=" * 60)
    
    # This is how you'd write a test with the recordable decorator
    @recordable(test_name="test_login_flow", navigate_to="https://example.com")
    def test_login(page):
        """Test login flow with automatic recording."""
        # These actions are recorded
        page.fill("#username", "testuser@example.com")
        page.fill("#password", "password123")
        page.click("#login-button")
        page.wait_for_url("**/dashboard**")
    
    # Run the test
    # test_login()
    print("✓ Decorated test function created (uncomment to run)")


def example_video_capture():
    """
    Example of capturing video during test execution.
    
    Videos are saved to the configured output directory
    with timestamps for easy identification.
    """
    print("\n" + "=" * 60)
    print("Example 3: Video Capture")
    print("=" * 60)
    
    @video_capture(output_dir="test-videos")
    def test_checkout(page):
        """Test checkout flow with video capture."""
        page.goto("https://example.com/cart")
        page.click("#checkout-button")
        page.fill("#card-number", "4242424242424242")
        page.click("#pay-button")
        page.wait_for_selector(".confirmation-message")
    
    # Run with video
    # test_checkout()
    print("✓ Video capture decorator ready (uncomment to run)")


def example_recorded_test_context():
    """
    Example using RecordedTest context manager.
    
    The context manager pattern provides the cleanest API
    and automatic cleanup.
    """
    print("\n" + "=" * 60)
    print("Example 4: RecordedTest Context Manager")
    print("=" * 60)
    
    with RecordedTest("example_interaction", video_enabled=True) as test:
        test.page.goto("https://example.com")
        
        # Record actions directly
        test.recorder.record_click("h1")
        test.recorder.record_type("input[type='email']", "test@example.com")
        test.recorder.record_screenshot(value="homepage.png")
        
        print(f"\n📹 Video enabled: {test.recorder._video_path}")
        print(f"📝 Actions: {len(test.actions)}")
    
    # After the context, generate test code
    code = test.generate()
    print("\n📄 Generated code:")
    print("-" * 40)
    print(code[:500] + "..." if len(code) > 500 else code)


def example_test_runner():
    """
    Example using TestRunner for batch test execution.
    
    TestRunner handles video capture, watch mode,
    and result reporting.
    """
    print("\n" + "=" * 60)
    print("Example 5: TestRunner")
    print("=" * 60)
    
    runner = TestRunner(
        RecorderConfig(
            video_dir="test-videos",
            headless=True,
        )
    )
    
    # Run specific test files
    test_files = [
        "tests/test_login.py",
        "tests/test_dashboard.py",
    ]
    
    # results = runner.run_tests(test_files, video_enabled=True)
    
    # Print results
    # for result in results:
    #     status = "✓ PASS" if result.passed else "✗ FAIL"
    #     print(f"{status} {result.test_file} ({result.duration:.2f}s)")
    #     if result.video_path:
    #         print(f"   📹 {result.video_path}")
    
    print("✓ TestRunner configured (uncomment to run tests)")


def example_watch_mode():
    """
    Example of watch mode for auto-running tests on file changes.
    
    Watch mode monitors specified directories and re-runs
    tests when files change, with debouncing to prevent
    excessive test runs.
    """
    print("\n" + "=" * 60)
    print("Example 6: Watch Mode")
    print("=" * 60)
    
    runner = TestRunner()
    
    # Watch src and tests directories, run tests on changes
    # runner.watch_mode(
    #     watch_paths=["src/", "tests/"],
    #     test_files=["tests/test_integration.py"],
    #     debounce_seconds=1.0,
    # )
    
    print("✓ Watch mode configured (uncomment to start)")
    print("  This would watch 'src/' and 'tests/' directories")
    print("  and re-run 'tests/test_integration.py' on changes")


def example_replay():
    """
    Example of replaying recorded actions.
    
    You can save recordings as JSON and replay them later.
    """
    print("\n" + "=" * 60)
    print("Example 7: Replay Recorded Actions")
    print("=" * 60)
    
    from playwright_recorder import RecordedAction
    from playwright.sync_api import sync_playwright
    
    # Define actions to replay
    actions = [
        RecordedAction(
            id="1",
            timestamp=0,
            action_type=ActionType.NAVIGATE,
            value="https://example.com",
        ),
        RecordedAction(
            id="2",
            timestamp=1,
            action_type=ActionType.CLICK,
            selector="h1",
        ),
        RecordedAction(
            id="3",
            timestamp=2,
            action_type=ActionType.TYPE,
            selector="input[type='email']",
            value="test@example.com",
        ),
    ]
    
    # Create browser and page
    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=True)
    page = browser.new_page()
    
    # Replay the actions
    replay(actions, page)
    
    print(f"✓ Replayed {len(actions)} actions")
    
    # Cleanup
    browser.close()
    pw.stop()


def example_save_and_load():
    """
    Example of saving and loading recordings.
    
    Recordings can be saved to JSON files and loaded
    later for test generation or replay.
    """
    print("\n" + "=" * 60)
    print("Example 8: Save and Load Recordings")
    print("=" * 60)
    
    with PlaywrightRecorder() as recorder:
        recorder.start_recording()
        
        # Record some actions
        recorder.record_navigate("https://example.com")
        recorder.record_click("body")
        
        # Save to file
        recorder_path = recorder.save_recording()
        print(f"💾 Recording saved to: {recorder_path}")
        
        recorder.stop_recording()
    
    # Load the recording
    with PlaywrightRecorder() as loader:
        actions = loader.load_recording(recorder_path)
        print(f"📂 Loaded {len(actions)} actions")
        
        # Generate new test code
        code = loader.generate_test_code(test_name="test_reloaded")
        print(f"\n📄 Generated code from loaded recording:")
        print("-" * 40)
        print(code[:300] + "..." if len(code) > 300 else code)


def example_custom_actions():
    """
    Example of recording custom action types.
    
    While ActionType covers common actions, you can extend
    it with custom types for your specific needs.
    """
    print("\n" + "=" * 60)
    print("Example 9: Custom Action Recording")
    print("=" * 60)
    
    with PlaywrightRecorder() as recorder:
        page = recorder.start_recording("https://example.com")
        
        # Record standard actions
        recorder.record_action(ActionType.NAVIGATE, value="https://example.com/form")
        recorder.record_action(ActionType.TYPE, selector="#name", value="John Doe")
        recorder.record_action(ActionType.TYPE, selector="#email", value="john@example.com")
        
        # Record custom wait
        recorder.record_action(ActionType.WAIT_FOR_TIMEOUT, value="500")
        
        # Record a screenshot action
        recorder.record_action(ActionType.SCREENSHOT, selector=None, value="form_filled.png")
        
        recorder.stop_recording()
        
        # Generate code
        code = recorder.generate_test_code(test_name="test_custom_form")
        print("📄 Generated custom action code:")
        print("-" * 40)
        print(code)


def demo():
    """
    Run all examples that don't require a browser.
    """
    print("\n" + "🎬" * 30)
    print("Playwright Recorder - Demo")
    print("🎬" * 30)
    
    # Run examples
    example_basic_recording()
    example_recorded_test_context()
    example_save_and_load()
    example_custom_actions()
    example_replay()
    
    # Print info about decorator examples
    example_with_decorators()
    example_video_capture()
    example_test_runner()
    example_watch_mode()
    
    print("\n" + "=" * 60)
    print("Demo complete!")
    print("=" * 60)
    print("\nTo run full examples with browser:")
    print("  1. Install dependencies: pip install playwright pytest")
    print("  2. Install browsers: playwright install chromium")
    print("  3. Run: python -c 'from example import *; demo()'")


if __name__ == "__main__":
    demo()
