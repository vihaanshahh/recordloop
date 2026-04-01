#!/usr/bin/env python3
"""
Demo script that uses the Playwright Recorder to test a React app and capture video.

This script:
1. Starts recording with video capture
2. Navigates to a React test app
3. Performs various interactions (clicks, form fills, etc.)
4. Generates test code from recorded actions
5. Saves the video recording
"""

import sys
from pathlib import Path

# Add playwright-recorder to path
sys.path.insert(0, '/home/workspace/playwright-recorder')

import time
from recorder import PlaywrightRecorder, RecorderConfig, ActionType

def main():
    # Configuration
    video_dir = "/home/workspace/playwright-recorder/test-videos"
    test_output_dir = "/home/workspace/playwright-recorder/generated-tests"
    
    # Ensure directories exist
    Path(video_dir).mkdir(parents=True, exist_ok=True)
    Path(test_output_dir).mkdir(parents=True, exist_ok=True)
    
    # Create recorder config
    config = RecorderConfig(
        base_url="http://localhost:5173",
        video_dir=video_dir,
        test_output_dir=test_output_dir,
        headless=True,  # Set to False to see the browser
        viewport_width=1280,
        viewport_height=720,
    )
    
    print("=" * 60)
    print("Playwright Recorder Demo - Testing React App")
    print("=" * 60)
    print()
    
    # Using context manager for automatic cleanup
    with PlaywrightRecorder(config) as recorder:
        print("Starting recording...")
        page = recorder.start_recording("http://localhost:5173")
        
        # Wait for page to load
        page.wait_for_load_state("networkidle")
        print("✓ Page loaded: http://localhost:5173")
        
        # Record navigation action
        recorder.record_navigate("http://localhost:5173")
        
        # Test 1: Click the increment button multiple times
        print("\nTest 1: Testing counter buttons...")
        page.wait_for_selector("#increment-btn")
        
        # Record click actions
        recorder.record_click("#increment-btn")
        page.click("#increment-btn")
        time.sleep(0.3)
        
        recorder.record_click("#increment-btn")
        page.click("#increment-btn")
        time.sleep(0.3)
        
        recorder.record_click("#decrement-btn")
        page.click("#decrement-btn")
        time.sleep(0.3)
        
        # Record a screenshot
        recorder.record_action(ActionType.SCREENSHOT, value="counter_after_clicks.png")
        page.screenshot(path=f"{test_output_dir}/counter_after_clicks.png")
        print("✓ Counter buttons tested")
        
        # Test 2: Add a new todo
        print("\nTest 2: Adding a new todo...")
        page.fill("#todo-input", "Test the recorder")
        recorder.record_type("#todo-input", "Test the recorder")
        time.sleep(0.2)
        
        page.click("#add-todo-btn")
        recorder.record_click("#add-todo-btn")
        time.sleep(0.3)
        print("✓ Todo added")
        
        # Test 3: Fill out the contact form
        print("\nTest 3: Filling contact form...")
        page.fill("#name-input", "John Doe")
        recorder.record_type("#name-input", "John Doe")
        time.sleep(0.1)
        
        page.fill("#email-input", "john@example.com")
        recorder.record_type("#email-input", "john@example.com")
        time.sleep(0.1)
        
        page.select_option("#topic-select", "feedback")
        recorder.record_action(ActionType.SELECT, "#topic-select", "feedback")
        time.sleep(0.1)
        
        page.fill("#message-input", "This is a test message from the Playwright recorder demo.")
        recorder.record_type("#message-input", "This is a test message from the Playwright recorder demo.")
        time.sleep(0.3)
        print("✓ Form filled")
        
        # Submit the form
        page.click("#submit-form-btn")
        recorder.record_click("#submit-form-btn")
        page.wait_for_selector("#success-message")
        time.sleep(0.5)
        print("✓ Form submitted")
        
        # Take a screenshot of success message
        recorder.record_action(ActionType.SCREENSHOT, value="form_success.png")
        page.screenshot(path=f"{test_output_dir}/form_success.png")
        
        # Test 4: Toggle checkboxes
        print("\nTest 4: Testing toggles...")
        page.click("label:has(#toggle-darkmode)")
        recorder.record_click("label:has(#toggle-darkmode)")
        time.sleep(0.2)
        print("✓ Toggle clicked")
        
        # Record wait action
        recorder.record_action(ActionType.WAIT_FOR_TIMEOUT, value="500")
        page.wait_for_timeout(500)
        
        # Stop recording
        actions = recorder.stop_recording()
        
        # Get video path
        video_path = recorder.get_video_path()
        
        print("\n" + "=" * 60)
        print("Recording Complete!")
        print("=" * 60)
        print(f"\n📹 Video saved to: {video_path}")
        print(f"\n📝 Recorded {len(actions)} actions:")
        for i, action in enumerate(actions, 1):
            selector = action.selector or ""
            value = action.value or ""
            print(f"  {i}. {action.action_type.value} | selector='{selector}' | value='{value[:30]}...' " if len(value) > 30 else f"  {i}. {action.action_type.value} | selector='{selector}' | value='{value}'")
        
        # Generate test code
        print("\n" + "-" * 40)
        print("Generating test code...")
        code = recorder.generate_test_code(
            test_name="test_react_demo_app",
            include_imports=True,
            use_pytest=True
        )
        print("\n📄 Generated Test Code:")
        print("-" * 40)
        print(code)
        
        # Save the generated test
        test_path = recorder.save_test_code(
            filepath=f"{test_output_dir}/test_react_demo.py"
        )
        print(f"\n💾 Test saved to: {test_path}")
        
        # Save recording data
        recording_path = recorder.save_recording(
            filepath=f"{test_output_dir}/recording_demo.json"
        )
        print(f"💾 Recording data saved to: {recording_path}")
        
        return video_path

if __name__ == "__main__":
    try:
        video_path = main()
        print("\n" + "=" * 60)
        print("✅ Demo completed successfully!")
        print(f"Video file: {video_path}")
        print("=" * 60)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
