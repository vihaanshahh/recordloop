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

def test_recordloop_demo(page: Page):
    """Test generated from recorded actions."""

    # Action 1: click
    # Page: http://localhost:7777/app.html
    page.click('#increment-btn')

    # Action 2: click
    # Page: http://localhost:7777/app.html
    page.click('#increment-btn')

    # Action 3: click
    # Page: http://localhost:7777/app.html
    page.click('#increment-btn')

    # Action 4: click
    # Page: http://localhost:7777/app.html
    page.click('#decrement-btn')

    # Action 5: type
    # Page: http://localhost:7777/app.html
    page.fill('#todo-input', 'Write more tests')

    # Action 6: click
    # Page: http://localhost:7777/app.html
    page.click('#add-todo-btn')

    # Action 7: type
    # Page: http://localhost:7777/app.html
    page.fill('#todo-input', 'Ship the demo')

    # Action 8: click
    # Page: http://localhost:7777/app.html
    page.click('#add-todo-btn')

    # Action 9: type
    # Page: http://localhost:7777/app.html
    page.fill('#name-input', 'Ada Lovelace')

    # Action 10: type
    # Page: http://localhost:7777/app.html
    page.fill('#email-input', 'ada@recordloop.dev')

    # Action 11: select
    # Page: http://localhost:7777/app.html
    page.select_option('#topic-select', 'feature')

    # Action 12: type
    # Page: http://localhost:7777/app.html
    page.fill('#message-input', 'RecordLoop is exactly what we needed for PR reviews.')

    # Action 13: click
    # Page: http://localhost:7777/app.html
    page.click('#submit-form-btn')

    # Assertions can be added here