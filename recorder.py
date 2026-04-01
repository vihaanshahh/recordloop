"""
Core recorder class for Playwright test recording.
Captures actions, generates test code, and handles video recording.
"""

import json
import shutil
import subprocess
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional, Any
try:
    from playwright.sync_api import sync_playwright, Browser, Page, BrowserContext
except ImportError:
    sync_playwright = None
    Browser = None
    Page = None
    BrowserContext = None

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler, FileModifiedEvent
except ImportError:
    Observer = None
    FileSystemEventHandler = object
    FileModifiedEvent = None


class ActionType(Enum):
    """Supported action types for recording."""
    NAVIGATE = "navigate"
    CLICK = "click"
    DOUBLE_CLICK = "double_click"
    TYPE = "type"
    PRESS = "press"
    SELECT = "select"
    CHECK = "check"
    UNCHECK = "uncheck"
    HOVER = "hover"
    SCROLL = "scroll"
    WAIT_FOR_SELECTOR = "wait_for_selector"
    WAIT_FOR_NAVIGATION = "wait_for_navigation"
    SCREENSHOT = "screenshot"
    WAIT_FOR_TIMEOUT = "wait_for_timeout"
    GO_BACK = "go_back"
    GO_FORWARD = "go_forward"
    RELOAD = "reload"


@dataclass
class RecordedAction:
    """Represents a single recorded action."""
    id: str
    timestamp: float
    action_type: ActionType
    selector: Optional[str] = None
    value: Optional[str] = None
    options: dict = field(default_factory=dict)
    page_url: Optional[str] = None
    page_title: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "RecordedAction":
        return cls(
            id=data["id"],
            timestamp=data["timestamp"],
            action_type=ActionType(data["action_type"]) if isinstance(data["action_type"], str) else data["action_type"],
            selector=data.get("selector"),
            value=data.get("value"),
            options=data.get("options", {}),
            page_url=data.get("page_url"),
            page_title=data.get("page_title"),
        )


@dataclass
class RecorderConfig:
    """Configuration for the recorder."""
    base_url: str = "http://localhost:3000"
    video_dir: str = "test-videos"
    test_output_dir: str = "generated-tests"
    test_file_prefix: str = "test_recording"
    headless: bool = True
    slow_mo: int = 0
    viewport_width: int = 1280
    viewport_height: int = 720


class PlaywrightRecorder:
    """
    Core recorder class for capturing Playwright actions and generating test code.
    
    Usage:
        with PlaywrightRecorder() as recorder:
            recorder.start_recording()
            # ... perform actions in browser ...
            recorder.stop_recording()
            code = recorder.generate_test_code()
    """
    
    def __init__(self, config: Optional[RecorderConfig] = None):
        self.config = config or RecorderConfig()
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._is_recording = False
        self._actions: list[RecordedAction] = []
        self._start_time: Optional[float] = None
        self._video_path: Optional[Path] = None
        self._observer: Optional[Observer] = None
        self._file_handler: Optional[FileSystemEventHandler] = None
        
    def __enter__(self) -> "PlaywrightRecorder":
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.cleanup()
        
    def _generate_action_id(self) -> str:
        """Generate unique ID for an action."""
        return str(uuid.uuid4())[:8]
    
    def _setup_browser(self):
        """Initialize Playwright browser with video recording."""
        if sync_playwright is None:
            raise ImportError("Playwright is required for browser recording. Install with: pip install playwright")
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(
            headless=self.config.headless,
            slow_mo=self.config.slow_mo,
        )
        
        # Prepare video directory
        video_dir = Path(self.config.video_dir)
        video_dir.mkdir(parents=True, exist_ok=True)
        
        self._context = self._browser.new_context(
            viewport={"width": self.config.viewport_width, "height": self.config.viewport_height},
            record_video_dir=str(video_dir),
            record_video_size={"width": self.config.viewport_width, "height": self.config.viewport_height},
        )
        self._page = self._context.new_page()
        
        return self._browser, self._context, self._page
    
    def start_recording(self, navigate_to: Optional[str] = None) -> Page:
        """
        Start recording actions.
        
        Args:
            navigate_to: Optional URL to navigate to initially
            
        Returns:
            The Playwright Page object for interaction
        """
        if self._is_recording:
            raise RuntimeError("Already recording. Call stop_recording() first.")
        
        self._setup_browser()
        self._is_recording = True
        self._actions = []
        self._start_time = time.time()
        
        if navigate_to:
            self._page.goto(navigate_to)
            
        return self._page
    
    def stop_recording(self) -> list[RecordedAction]:
        """
        Stop recording actions.
        
        Returns:
            List of recorded actions
        """
        if not self._is_recording:
            raise RuntimeError("Not recording. Call start_recording() first.")
        
        self._is_recording = False
        
        # Save video path
        if self._page:
            self._video_path = Path(self._page.video.path()) if self._page.video else None
        
        # Auto-convert webm to mp4
        if self._video_path:
            self._video_path = self._ensure_mp4(self._video_path)
            
        return self._actions.copy()
    
    def _ensure_mp4(self, video_path: Path) -> Path:
        """
        Convert webm video to mp4 using ffmpeg.
        
        Args:
            video_path: Path to the webm video file
            
        Returns:
            Path to the mp4 video file (same name, .mp4 extension)
        """
        if video_path.suffix.lower() != ".webm":
            return video_path
            
        mp4_path = video_path.with_suffix(".mp4")
        
        # Skip if already mp4 or if ffmpeg fails
        try:
            subprocess.run(
                [
                    "ffmpeg", "-i", str(video_path),
                    "-c:v", "libx264", "-c:a", "aac",
                    "-y",  # Overwrite output
                    str(mp4_path)
                ],
                check=True,
                capture_output=True,
            )
            # Remove original webm
            video_path.unlink()
            return mp4_path
        except (subprocess.CalledProcessError, FileNotFoundError):
            # Return original path if conversion fails
            return video_path
    
    def record_action(
        self,
        action_type: ActionType,
        selector: Optional[str] = None,
        value: Optional[str] = None,
        **options
    ) -> RecordedAction:
        """
        Record an action manually (for programmatic recording).
        
        Args:
            action_type: Type of action performed
            selector: CSS selector or locator string
            value: Optional value (e.g., text to type)
            **options: Additional options for the action
            
        Returns:
            The recorded action
        """
        if not self._is_recording:
            raise RuntimeError("Not recording. Call start_recording() first.")
        
        action = RecordedAction(
            id=self._generate_action_id(),
            timestamp=time.time() - (self._start_time or time.time()),
            action_type=action_type,
            selector=selector,
            value=value,
            options=options,
            page_url=self._page.url if self._page else None,
            page_title=self._page.title() if self._page else None,
        )
        
        self._actions.append(action)
        return action
    
    def record_click(self, selector: str, **options) -> RecordedAction:
        """Convenience method to record a click action."""
        return self.record_action(ActionType.CLICK, selector, **options)
    
    def record_type(self, selector: str, value: str, **options) -> RecordedAction:
        """Convenience method to record a type action."""
        return self.record_action(ActionType.TYPE, selector, value, **options)
    
    def record_navigate(self, url: str, **options) -> RecordedAction:
        """Convenience method to record a navigation action."""
        return self.record_action(ActionType.NAVIGATE, value=url, **options)
    
    def get_page(self) -> Optional[Page]:
        """Get the current page object."""
        return self._page
    
    def get_video_path(self) -> Optional[Path]:
        """Get the path to the recorded video."""
        return self._video_path
    
    def save_recording(self, filepath: Optional[str] = None) -> str:
        """
        Save recorded actions to a JSON file.
        
        Args:
            filepath: Optional custom filepath
            
        Returns:
            Path to the saved file
        """
        if filepath is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filepath = f"{self.config.test_output_dir}/recording_{timestamp}.json"
        
        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(path, "w") as f:
            json.dump([a.to_dict() for a in self._actions], f, indent=2, default=str)
        
        return str(path)
    
    def load_recording(self, filepath: str) -> list[RecordedAction]:
        """
        Load recorded actions from a JSON file.
        
        Args:
            filepath: Path to the recording file
            
        Returns:
            List of recorded actions
        """
        with open(filepath, "r") as f:
            data = json.load(f)
        
        self._actions = [RecordedAction.from_dict(a) for a in data]
        return self._actions.copy()
    
    def generate_test_code(
        self,
        test_name: Optional[str] = None,
        include_imports: bool = True,
        use_pytest: bool = True,
    ) -> str:
        """
        Generate Playwright test code from recorded actions.
        
        Args:
            test_name: Name for the test function
            include_imports: Whether to include import statements
            use_pytest: Whether to use pytest-style fixtures
            
        Returns:
            Generated Python test code as string
        """
        if not self._actions:
            return "# No actions recorded"
        
        lines = []
        
        if include_imports:
            lines.extend([
                '"""Generated Playwright test from recorded actions."""',
                "",
                "import pytest",
                "from playwright.sync_api import Page, expect",
                "",
            ])
        
        # Generate helper functions for different action types
        lines.extend(self._generate_helper_functions())
        
        if use_pytest:
            lines.extend([
                "",
                "@pytest.fixture",
                "def page(browser: Browser):",
                '    """Fixture that provides a configured page."""',
                "",
                "    context = browser.new_context(",
                "        viewport={'width': 1280, 'height': 720},",
                "    )",
                "    page = context.new_page()",
                "    yield page",
                "    context.close()",
                "",
            ])
        
        # Generate test function
        test_func_name = test_name or "test_recorded_actions"
        lines.append(f"def {test_func_name}(page: Page):")
        lines.append(f'    """Test generated from recorded actions."""')
        
        for i, action in enumerate(self._actions):
            lines.append("")
            lines.append(self._generate_action_code(action, i))
        
        lines.append("")
        lines.append("    # Assertions can be added here")
        
        return "\n".join(lines)
    
    def _generate_helper_functions(self) -> list[str]:
        """Generate helper functions for common actions."""
        return [
            "def click_and_wait(page: Page, selector: str):",
            '    """Click element and wait for network idle."""',
            "    page.click(selector)",
            "    page.wait_for_load_state('networkidle')",
            "",
            "def fill_and_continue(page: Page, selector: str, value: str):",
            '    """Fill input and wait briefly."""',
            "    page.fill(selector, value)",
            "    page.wait_for_timeout(100)",
            "",
        ]
    
    def _generate_action_code(self, action: RecordedAction, index: int) -> str:
        """Generate Python code for a single action."""
        indent = "    "
        
        # Add comment with action info
        lines = [f"{indent}# Action {index + 1}: {action.action_type.value}"]
        
        if action.page_url:
            lines.append(f"{indent}# Page: {action.page_url}")
        
        selector = action.selector
        value = action.value
        options = action.options
        
        match action.action_type:
            case ActionType.NAVIGATE:
                lines.append(f"{indent}page.goto('{value}')")
                
            case ActionType.CLICK:
                opts = self._format_options(options)
                lines.append(f"{indent}page.click({repr(selector)}{opts})")
                
            case ActionType.DOUBLE_CLICK:
                opts = self._format_options(options)
                lines.append(f"{indent}page.dblclick({repr(selector)}{opts})")
                
            case ActionType.TYPE:
                lines.append(f"{indent}page.fill({repr(selector)}, {repr(value)})")
                
            case ActionType.PRESS:
                lines.append(f"{indent}page.press({repr(selector)}, {repr(value)})")
                
            case ActionType.SELECT:
                lines.append(f"{indent}page.select_option({repr(selector)}, {repr(value)})")
                
            case ActionType.CHECK:
                lines.append(f"{indent}page.check({repr(selector)})")
                
            case ActionType.UNCHECK:
                lines.append(f"{indent}page.uncheck({repr(selector)})")
                
            case ActionType.HOVER:
                opts = self._format_options(options)
                lines.append(f"{indent}page.hover({repr(selector)}{opts})")
                
            case ActionType.SCREENSHOT:
                filename = value or f"screenshot_{index + 1}.png"
                lines.append(f"{indent}page.screenshot(path={repr(filename)})")
                
            case ActionType.WAIT_FOR_SELECTOR:
                timeout = options.get("timeout", 30000)
                lines.append(f"{indent}page.wait_for_selector({repr(selector)}, timeout={timeout})")
                
            case ActionType.WAIT_FOR_NAVIGATION:
                timeout = options.get("timeout", 30000)
                lines.append(f"{indent}page.wait_for_load_state('load', timeout={timeout})")
                
            case ActionType.WAIT_FOR_TIMEOUT:
                lines.append(f"{indent}page.wait_for_timeout({value})")
                
            case ActionType.GO_BACK:
                lines.append(f"{indent}page.go_back()")
                
            case ActionType.GO_FORWARD:
                lines.append(f"{indent}page.go_forward()")
                
            case ActionType.RELOAD:
                lines.append(f"{indent}page.reload()")
                
            case _:
                lines.append(f"{indent}# Unsupported action type: {action.action_type}")
        
        return "\n".join(lines)
    
    def _format_options(self, options: dict) -> str:
        """Format options dictionary for Playwright API."""
        if not options:
            return ""
        opts_parts = [f"{k}={repr(v)}" for k, v in options.items()]
        return ", " + ", ".join(opts_parts)
    
    def save_test_code(self, filepath: Optional[str] = None, **kwargs) -> str:
        """
        Save generated test code to a file.
        
        Args:
            filepath: Optional custom filepath
            **kwargs: Arguments passed to generate_test_code
            
        Returns:
            Path to the saved file
        """
        code = self.generate_test_code(**kwargs)
        
        if filepath is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filepath = f"{self.config.test_output_dir}/{self.config.test_file_prefix}_{timestamp}.py"
        
        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(path, "w") as f:
            f.write(code)
        
        return str(path)
    
    def cleanup(self) -> None:
        """Clean up browser and observer resources."""
        if self._observer:
            self._observer.stop()
            self._observer.join()
            self._observer = None
        
        if self._context:
            self._context.close()
            self._context = None
            
        if self._browser:
            self._browser.close()
            self._browser = None
            
        if self._playwright:
            self._playwright.stop()
            self._playwright = None
        
        self._is_recording = False


class AutoRecordingHandler(FileSystemEventHandler):
    """Handler for automatic recording on file changes."""
    
    def __init__(
        self,
        recorder: PlaywrightRecorder,
        test_files: list[str],
        on_change_callback=None
    ):
        self.recorder = recorder
        self.test_files = [Path(f) for f in test_files]
        self.on_change_callback = on_change_callback
        
    def on_modified(self, event):
        """Handle file modification events."""
        if event.is_directory:
            return
            
        event_path = Path(event.src_path)
        
        # Check if modified file matches any test files
        for test_file in self.test_files:
            if event_path == test_file or event_path.samefile(test_file):
                print(f"Detected change in: {event_path}")
                if self.on_change_callback:
                    self.on_change_callback(event_path)
                break


def watch_for_changes(
    paths: list[str],
    callback,
    recursive: bool = False
) -> Observer:
    """
    Watch specified paths for changes and trigger callback.
    
    Args:
        paths: List of paths to watch
        callback: Function to call on changes
        recursive: Whether to watch subdirectories
        
    Returns:
        The watchdog Observer (caller should call observer.start())
    """
    observer = Observer()
    
    for path in paths:
        handler = FileSystemEventHandler()
        handler.on_modified = lambda e, cb=callback: cb(e) if not e.is_directory else None
        observer.schedule(handler, path, recursive=recursive)
    
    observer.start()
    return observer
