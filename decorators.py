"""
Decorators for Playwright test recording.
Provides @recordable, @video_capture, and @watch_changes decorators.
"""

import functools
import inspect
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional, TypeVar, ParamSpec
from dataclasses import dataclass

from .recorder import PlaywrightRecorder, RecorderConfig, ActionType, RecordedAction
from .test_runner import TestRunner, TestResult

# Type hints for decorators
P = ParamSpec("P")
T = TypeVar("T")


@dataclass
class DecoratorConfig:
    """Configuration for decorators."""
    video_dir: str = "test-videos"
    headless: bool = True
    slow_mo: int = 0
    auto_generate: bool = True
    save_recordings: bool = True


def recordable(
    test_name: Optional[str] = None,
    navigate_to: Optional[str] = None,
    config: Optional[DecoratorConfig] = None,
) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """
    Decorator that records actions during test execution.
    
    Usage:
        @recordable()
        def test_login(page):
            page.goto("https://example.com")
            page.fill("#username", "user")
            page.click("#submit")
    
    Args:
        test_name: Optional name for the generated test
        navigate_to: URL to navigate to before recording
        config: Optional decorator configuration
    """
    def decorator(func: Callable[P, T]) -> Callable[P, T]:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            recorder_config = config or DecoratorConfig()
            recorder = PlaywrightRecorder(
                RecorderConfig(
                    video_dir=recorder_config.video_dir,
                    headless=recorder_config.headless,
                    slow_mo=recorder_config.slow_mo,
                )
            )
            
            # Get page from args or start recording
            page = None
            if args and hasattr(args[0], '__class__'):
                # First arg might be 'page' from pytest fixture
                page = args[0]
            
            try:
                if page is None:
                    page = recorder.start_recording(navigate_to=navigate_to)
                    # Replace first arg with page
                    args = (page,) + args[1:]
                
                # Run the test
                result = func(*args, **kwargs)
                
                # Get recorded actions
                actions = recorder.stop_recording() if recorder._is_recording else []
                
                # Optionally generate and save test code
                if recorder_config.auto_generate and actions:
                    test_code = recorder.generate_test_code(test_name=test_name or func.__name__)
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    output_path = f"generated-tests/{func.__name__}_{timestamp}.py"
                    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                    Path(output_path).write_text(test_code)
                    print(f"Generated test saved to: {output_path}")
                
                return result
                
            finally:
                recorder.cleanup()
        
        return wrapper
    return decorator


def video_capture(
    output_dir: str = "test-videos",
    filename: Optional[str] = None,
    enabled: bool = True,
) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """
    Decorator that captures video of test execution.
    
    Usage:
        @video_capture()
        def test_checkout(page):
            page.goto("https://shop.example.com")
            # ... test steps ...
    
    Args:
        output_dir: Directory for video output
        filename: Custom filename (uses timestamp if not provided)
        enabled: Whether to enable video capture
    """
    def decorator(func: Callable[P, T]) -> Callable[P, T]:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            if not enabled:
                return func(*args, **kwargs)
            
            # Prepare video path
            video_dir = Path(output_dir)
            video_dir.mkdir(parents=True, exist_ok=True)
            
            if filename:
                video_path = video_dir / filename
            else:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                video_path = video_dir / f"{func.__name__}_{timestamp}.webm"
            
            # Setup recorder
            recorder = PlaywrightRecorder(
                RecorderConfig(video_dir=str(video_dir))
            )
            
            start_time = time.time()
            passed = True
            error = None
            
            try:
                page = recorder.start_recording()
                # Replace/add page argument
                if args and hasattr(args[0], '__class__'):
                    args = (page,) + args[1:]
                else:
                    args = (page,) + args
                    
                result = func(*args, **kwargs)
                
            except Exception as e:
                passed = False
                error = str(e)
                raise
                
            finally:
                duration = time.time() - start_time
                recorder.stop_recording()
                actual_video_path = recorder.get_video_path()
                
                # Report video capture status
                if actual_video_path:
                    print(f"\n📹 Video saved: {actual_video_path}")
                
                recorder.cleanup()
            
            return result if 'result' in dir() else None
        
        return wrapper
    return decorator


def watch_changes(
    paths: list[str],
    test_files: Optional[list[str]] = None,
    debounce: float = 0.5,
    on_change: Optional[Callable] = None,
) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """
    Decorator that runs a test when watched files change.
    
    Usage:
        @watch_changes(["src/", "tests/"])
        def test_integration():
            runner = TestRunner()
            runner.run_tests(["tests/test_integration.py"])
    
    Args:
        paths: Paths to watch for changes
        test_files: Test files to run on changes
        debounce: Seconds to wait before re-running after changes
        on_change: Optional callback function
    """
    def decorator(func: Callable[P, T]) -> Callable[P, T]:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            from watchdog.observers import Observer
            from watchdog.events import FileSystemEventHandler
            
            class ChangeHandler(FileSystemEventHandler):
                def __init__(handler_self):
                    handler_self.last_run = 0
                    handler_self.observer = None
                    
                def on_modified(handler_self, event):
                    if event.is_directory:
                        return
                    
                    # Debounce
                    now = time.time()
                    if now - handler_self.last_run < debounce:
                        return
                    
                    # Check file type
                    event_path = Path(event.src_path)
                    if event_path.suffix not in (".py", ".jsx", ".tsx", ".ts"):
                        return
                    
                    print(f"\n🔄 Change detected: {event_path}")
                    handler_self.last_run = now
                    
                    # Run the decorated function
                    func(*args, **kwargs)
                    
                    # Run callback if provided
                    if on_change:
                        on_change(event_path)
                    
                    # Run affected tests if specified
                    if test_files:
                        runner = TestRunner()
                        runner.run_tests(test_files)
            
            handler = ChangeHandler()
            observer = Observer()
            
            for path in paths:
                observer.schedule(handler, path, recursive=True)
            
            handler.observer = observer
            observer.start()
            
            print(f"👀 Watching {len(paths)} path(s) for changes...")
            
            try:
                # Run initially
                func(*args, **kwargs)
                
                # Keep running until interrupted
                while True:
                    time.sleep(1)
                    
            except KeyboardInterrupt:
                print("\nStopping watch mode...")
                observer.stop()
                
            observer.join()
        
        return wrapper
    return decorator


def step(
    action_type: ActionType,
    selector: Optional[str] = None,
    value: Optional[str] = None,
    **options
) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """
    Decorator that records a single action step.
    
    Usage:
        @step(ActionType.CLICK, "#submit")
        def submit_form(page):
            pass
    
    Args:
        action_type: Type of action to record
        selector: CSS selector
        value: Optional value
        **options: Additional options
    """
    def decorator(func: Callable[P, T]) -> Callable[P, T]:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # Get page from first arg
            page = args[0] if args else None
            
            if page is not None:
                # Record the action
                action = RecordedAction(
                    id="",
                    timestamp=time.time(),
                    action_type=action_type,
                    selector=selector,
                    value=value,
                    options=options,
                    page_url=page.url if hasattr(page, 'url') else None,
                )
                print(f"  📝 Recorded: {action_type.value} {selector or ''}")
            
            return func(*args, **kwargs)
        
        return wrapper
    return decorator


class RecordedTest:
    """
    Context manager for recording a test with automatic video.
    
    Usage:
        with RecordedTest("test_login") as test:
            test.page.goto("https://example.com")
            test.page.fill("#username", "user")
    """
    
    def __init__(
        self,
        name: str,
        config: Optional[RecorderConfig] = None,
        video_enabled: bool = True,
    ):
        self.name = name
        self.config = config or RecorderConfig()
        self.video_enabled = video_enabled
        self.recorder: Optional[PlaywrightRecorder] = None
        self.page = None
        self._actions: list[RecordedAction] = []
        
    def __enter__(self) -> "RecordedTest":
        self.recorder = PlaywrightRecorder(self.config)
        self.page = self.recorder.start_recording()
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.recorder:
            self._actions = self.recorder.stop_recording()
            self.recorder.cleanup()
            
    @property
    def actions(self) -> list[RecordedAction]:
        """Get recorded actions."""
        return self._actions.copy()
    
    def generate(self, test_name: Optional[str] = None) -> str:
        """Generate test code from recorded actions."""
        if self.recorder:
            return self.recorder.generate_test_code(
                test_name=test_name or self.name
            )
        return "# No actions recorded"
    
    def save(self, filepath: Optional[str] = None) -> str:
        """Save generated test to file."""
        if self.recorder:
            return self.recorder.save_test_code(
                filepath=filepath,
                test_name=self.name
            )
        return ""


def replay(
    actions: list[RecordedAction],
    page,
) -> None:
    """
    Replay a list of recorded actions on a page.
    
    Args:
        actions: List of recorded actions to replay
        page: Playwright page object
    """
    for action in actions:
        selector = action.selector
        value = action.value
        options = action.options
        
        try:
            match action.action_type:
                case ActionType.NAVIGATE:
                    page.goto(value, **options)
                case ActionType.CLICK:
                    page.click(selector, **options)
                case ActionType.DOUBLE_CLICK:
                    page.dblclick(selector, **options)
                case ActionType.TYPE:
                    page.fill(selector, value)
                case ActionType.PRESS:
                    page.press(selector, value)
                case ActionType.SELECT:
                    page.select_option(selector, value)
                case ActionType.CHECK:
                    page.check(selector)
                case ActionType.UNCHECK:
                    page.uncheck(selector)
                case ActionType.HOVER:
                    page.hover(selector, **options)
                case ActionType.SCREENSHOT:
                    page.screenshot(path=value or "screenshot.png")
                case ActionType.WAIT_FOR_SELECTOR:
                    timeout = options.get("timeout", 30000)
                    page.wait_for_selector(selector, timeout=timeout)
                case ActionType.WAIT_FOR_NAVIGATION:
                    page.wait_for_load_state("load", **options)
                case ActionType.WAIT_FOR_TIMEOUT:
                    page.wait_for_timeout(int(value or 1000))
                case ActionType.GO_BACK:
                    page.go_back()
                case ActionType.GO_FORWARD:
                    page.go_forward()
                case ActionType.RELOAD:
                    page.reload()
                    
        except Exception as e:
            print(f"Error replaying action {action.action_type.value}: {e}")
            raise
