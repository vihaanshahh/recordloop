"""
Test runner with video capture and watch mode.
Runs Playwright tests with automatic video recording and file change detection.
"""

import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional, Callable
try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler, FileModifiedEvent
except ImportError:
    Observer = None
    FileSystemEventHandler = object
    FileModifiedEvent = None

from .recorder import RecorderConfig, PlaywrightRecorder, AutoRecordingHandler


@dataclass
class TestResult:
    """Result of a test run."""
    test_file: str
    passed: bool
    duration: float
    video_path: Optional[Path] = None
    error_message: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.now)


class VideoRecorder:
    """Context manager for capturing video during test runs."""
    
    def __init__(self, output_dir: str = "test-videos"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.recorder: Optional[PlaywrightRecorder] = None
        self.video_path: Optional[Path] = None
        
    def __enter__(self) -> "VideoRecorder":
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.recorder:
            self.recorder.cleanup()
            
    def start(self) -> "VideoRecorder":
        """Start video recording."""
        self.recorder = PlaywrightRecorder()
        return self
    
    def get_page(self):
        """Get the page for recording."""
        if self.recorder:
            return self.recorder.start_recording()
        return None


class TestRunner:
    """
    Test runner with video capture and watch mode support.
    
    Usage:
        runner = TestRunner()
        runner.run_tests("tests/test_login.py")
        
        # Or with watch mode
        runner.watch_mode(["tests/"])
    """
    
    def __init__(self, config: Optional[RecorderConfig] = None):
        self.config = config or RecorderConfig()
        self._observer: Optional[Observer] = None
        self._running = False
        self._last_run_time: Optional[datetime] = None
        
    def run_tests(
        self,
        test_files: list[str],
        video_enabled: bool = True,
        video_output_dir: Optional[str] = None,
        browser: str = "chromium",
        headed: bool = False,
        timeout: int = 30000,
    ) -> list[TestResult]:
        """
        Run Playwright tests with optional video recording.
        
        Args:
            test_files: List of test file paths to run
            video_enabled: Whether to capture video
            video_output_dir: Directory for video output
            browser: Browser to use (chromium, firefox, webkit)
            headed: Run in headed mode (show browser)
            timeout: Test timeout in milliseconds
            
        Returns:
            List of test results
        """
        results = []
        video_dir = video_output_dir or self.config.video_dir
        
        for test_file in test_files:
            result = self._run_single_test(
                test_file,
                video_enabled=video_enabled,
                video_output_dir=video_dir,
                browser=browser,
                headed=headed,
                timeout=timeout,
            )
            results.append(result)
            
        self._last_run_time = datetime.now()
        return results
    
    def _run_single_test(
        self,
        test_file: str,
        video_enabled: bool,
        video_output_dir: str,
        browser: str,
        headed: bool,
        timeout: int,
    ) -> TestResult:
        """Run a single test file."""
        start_time = time.time()
        video_path = None
        
        # Generate video filename with timestamp
        if video_enabled:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            test_name = Path(test_file).stem
            video_path = Path(video_output_dir) / f"{test_name}_{timestamp}.webm"
        
        try:
            # Build pytest command
            cmd = [
                "pytest",
                test_file,
                "-v",
                "--tb=short",
                f"--timeout={timeout // 1000}",
            ]
            
            if not headed:
                cmd.append("-s")  # Don't capture output
            
            # Run the test
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=Path(test_file).parent,
            )
            
            passed = result.returncode == 0
            
            return TestResult(
                test_file=test_file,
                passed=passed,
                duration=time.time() - start_time,
                video_path=video_path,
                error_message=result.stderr if not passed else None,
            )
            
        except Exception as e:
            return TestResult(
                test_file=test_file,
                passed=False,
                duration=time.time() - start_time,
                video_path=video_path,
                error_message=str(e),
            )
    
    def run_with_video(
        self,
        test_file: str,
        video_output_dir: Optional[str] = None,
    ) -> TestResult:
        """
        Run a single test with video recording.
        
        Args:
            test_file: Path to test file
            video_output_dir: Directory for video output
            
        Returns:
            Test result with video path
        """
        video_dir = video_output_dir or self.config.video_dir
        
        # Start video recording
        recorder = PlaywrightRecorder(self.config)
        
        try:
            page = recorder.start_recording()
            
            # Run the test with Playwright
            from playwright.sync_api import sync_playwright
            
            # Get test function from file
            test_module = self._import_test_module(test_file)
            
            # Run test
            result = self._run_with_page(page, test_module)
            
            # Stop recording
            actions = recorder.stop_recording()
            video_path = recorder.get_video_path()
            
            return TestResult(
                test_file=test_file,
                passed=result.get("passed", False),
                duration=result.get("duration", 0),
                video_path=video_path,
                error_message=result.get("error"),
            )
            
        finally:
            recorder.cleanup()
    
    def _import_test_module(self, test_file: str):
        """Import a test module dynamically."""
        import importlib.util
        import sys
        
        spec = importlib.util.spec_from_file_location("test_module", test_file)
        module = importlib.util.module_from_spec(spec)
        sys.modules["test_module"] = module
        spec.loader.exec_module(module)
        
        return module
    
    def _run_with_page(self, page, test_module) -> dict:
        """Run test with provided page."""
        try:
            # Find test functions in module
            test_funcs = [
                getattr(test_module, name)
                for name in dir(test_module)
                if name.startswith("test_") and callable(getattr(test_module, name))
            ]
            
            for func in test_funcs:
                func(page)
                
            return {"passed": True, "duration": 0}
            
        except Exception as e:
            return {"passed": False, "duration": 0, "error": str(e)}
    
    def watch_mode(
        self,
        watch_paths: list[str],
        test_files: Optional[list[str]] = None,
        on_change: Optional[Callable] = None,
        debounce_seconds: float = 0.5,
    ) -> None:
        """
        Watch for file changes and re-run affected tests.
        
        Args:
            watch_paths: Paths to watch for changes
            test_files: Test files to run on changes
            on_change: Optional callback on file change
            debounce_seconds: Seconds to wait before re-running after changes
        """
        self._running = True
        
        class WatchHandler(FileSystemEventHandler):
            def __init__(runner_self, outer):
                runner_self.outer = outer
                runner_self.last_run = 0
                
            def on_modified(runner_self, event):
                if event.is_directory:
                    return
                    
                # Debounce
                now = time.time()
                if now - runner_self.last_run < runner_self.debounce_seconds:
                    return
                    
                event_path = Path(event.src_path)
                
                # Skip non-Python files
                if event_path.suffix not in (".py", ".jsx", ".tsx"):
                    return
                
                print(f"\n🔄 Detected change: {event_path}")
                runner_self.last_run = now
                
                if test_files:
                    print(f"Running affected tests...")
                    runner_self.outer.run_tests(test_files)
                
                if on_change:
                    on_change(event)
        
        handler = WatchHandler(self)
        self._observer = Observer()
        
        for path in watch_paths:
            self._observer.schedule(handler, path, recursive=True)
        
        self._observer.start()
        
        print(f"👀 Watching {len(watch_paths)} path(s) for changes...")
        print("Press Ctrl+C to stop.")
        
        try:
            while self._running:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nStopping watch mode...")
            self.stop_watch()
    
    def stop_watch(self) -> None:
        """Stop watch mode."""
        self._running = False
        if self._observer:
            self._observer.stop()
            self._observer.join()
            self._observer = None
    
    def run_recording_and_generate(
        self,
        test_file: str,
        navigate_url: Optional[str] = None,
    ) -> str:
        """
        Record a test, generate code, and save.
        
        Args:
            test_file: Path where generated test will be saved
            navigate_url: Initial URL to navigate to
            
        Returns:
            Path to saved test file
        """
        recorder = PlaywrightRecorder(self.config)
        
        try:
            # Start recording
            page = recorder.start_recording(navigate_to=navigate_url)
            
            # Return page for user to interact
            return recorder, page
            
        except Exception as e:
            recorder.cleanup()
            raise e


def run_test_with_video(
    test_func: Callable,
    video_output_path: str,
    **kwargs
) -> tuple[bool, str]:
    """
    Run a test function with video capture.
    
    Args:
        test_func: Test function to run (receives page as argument)
        video_output_path: Path for video output
        **kwargs: Additional arguments for Playwright
        
    Returns:
        Tuple of (passed, video_path)
    """
    recorder = PlaywrightRecorder(RecorderConfig(**kwargs))
    
    try:
        page = recorder.start_recording()
        test_func(page)
        recorder.stop_recording()
        video_path = recorder.get_video_path()
        
        return True, str(video_path) if video_path else ""
        
    except Exception as e:
        return False, str(e)
        
    finally:
        recorder.cleanup()
