"""Generated Playwright test from recorded actions."""

import pytest
from playwright.sync_api import Page, expect

def click_and_wait(page: Page, selector: str):
    """Click element and wait for network idle."""
    page.click(selector)
    page.wait_for_load_state('networkidle')

def fill_and_continue(page: Page, selector: str, value: str):
    """Fill input and wait briefly."""
    page.fill(selector, value)
    page.wait_for_timeout(100)


@pytest.fixture
def page(browser: Browser):
    """Fixture that provides a configured page."""

    context = browser.new_context(
        viewport={'width': 1280, 'height': 720},
    )
    page = context.new_page()
    yield page
    context.close()

def test_recorded_actions(page: Page):
    """Test generated from recorded actions."""

    # Action 1: navigate
    # Page: http://localhost:5173/
    page.goto('http://localhost:5173')

    # Action 2: click
    # Page: http://localhost:5173/
    page.click('#increment-btn')

    # Action 3: click
    # Page: http://localhost:5173/
    page.click('#increment-btn')

    # Action 4: click
    # Page: http://localhost:5173/
    page.click('#decrement-btn')

    # Action 5: screenshot
    # Page: http://localhost:5173/
    page.screenshot(path='counter_after_clicks.png')

    # Action 6: type
    # Page: http://localhost:5173/
    page.fill('#todo-input', 'Test the recorder')

    # Action 7: click
    # Page: http://localhost:5173/
    page.click('#add-todo-btn')

    # Action 8: type
    # Page: http://localhost:5173/
    page.fill('#name-input', 'John Doe')

    # Action 9: type
    # Page: http://localhost:5173/
    page.fill('#email-input', 'john@example.com')

    # Action 10: select
    # Page: http://localhost:5173/
    page.select_option('#topic-select', 'feedback')

    # Action 11: type
    # Page: http://localhost:5173/
    page.fill('#message-input', 'This is a test message from the Playwright recorder demo.')

    # Action 12: click
    # Page: http://localhost:5173/
    page.click('#submit-form-btn')

    # Action 13: screenshot
    # Page: http://localhost:5173/
    page.screenshot(path='form_success.png')

    # Action 14: click
    # Page: http://localhost:5173/
    page.click('label:has(#toggle-darkmode)')

    # Action 15: wait_for_timeout
    # Page: http://localhost:5173/
    page.wait_for_timeout(500)

    # Assertions can be added here