"""
login_capture.py — capture a Playwright storage_state from a credentialed login.

Reads URL/credentials/selectors from environment variables, drives a headless
Chromium through the login form, waits for the login URL to be left behind
(or for an explicit success URL), and writes the resulting storage_state to
$GITHUB_ENV as RECORDLOOP_STORAGE_STATE (base64).

Required env vars:
    RECORDLOOP_LOGIN_USERNAME           — value to fill into the username field
    RECORDLOOP_LOGIN_PASSWORD           — value to fill into the password field

Optional env vars (smart defaults applied):
    RECORDLOOP_LOGIN_URL                — defaults to "/login"
    RECORDLOOP_LOGIN_USERNAME_SELECTOR  — defaults to common email/username selectors
    RECORDLOOP_LOGIN_PASSWORD_SELECTOR  — defaults to input[type=password]
    RECORDLOOP_LOGIN_SUBMIT_SELECTOR    — defaults to common submit-button selectors
    RECORDLOOP_LOGIN_SUCCESS_URL        — when unset, success = URL changes from login-url
    RECORDLOOP_BASE_URL                 — base URL prefix when login-url is relative

Credentials are read only from env, never logged, never echoed.
"""

from __future__ import annotations

import base64
import json
import os
import sys
from urllib.parse import urljoin, urlparse


_DEFAULT_LOGIN_URL = "/login"

# Heuristic CSS selectors. Each is a comma-separated list — Playwright's first
# match wins. Ordered most-specific to most-general so the right field is
# picked on forms that have multiple text inputs.
_DEFAULT_USERNAME_SELECTOR = ", ".join([
    'input[type="email"]',
    'input[autocomplete="username"]',
    'input[autocomplete="email"]',
    'input[name="email"]',
    'input[name="username"]',
    'input[name="user"]',
    'input[id="email"]',
    'input[id="username"]',
    'input[type="text"]',
])
_DEFAULT_PASSWORD_SELECTOR = ", ".join([
    'input[type="password"]',
    'input[name="password"]',
    'input[id="password"]',
])
_DEFAULT_SUBMIT_SELECTOR = ", ".join([
    'button[type="submit"]',
    'input[type="submit"]',
    'button[name="action"][value="default"]',
    'button:has-text("Sign in")',
    'button:has-text("Log in")',
    'button:has-text("Login")',
    'button:has-text("Continue")',
])


def _resolve_login_url(login_url: str, base_url: str) -> str:
    if login_url.startswith(("http://", "https://")):
        return login_url
    if not base_url:
        raise SystemExit(
            "login: login-url is relative ('" + login_url + "') but no preview-url "
            "or auto-detected URL is available. Either set login-url to an "
            "absolute URL or run with auto-start enabled / preview-url set."
        )
    if not base_url.endswith("/"):
        base_url = base_url + "/"
    return urljoin(base_url, login_url.lstrip("/"))


def _emit_github_env(key: str, value: str) -> None:
    path = os.environ.get("GITHUB_ENV")
    if not path:
        print(f"{key}={value}")
        return
    with open(path, "a", encoding="utf-8") as f:
        if "\n" in value:
            delim = f"EOF_{os.urandom(8).hex()}"
            f.write(f"{key}<<{delim}\n{value}\n{delim}\n")
        else:
            f.write(f"{key}={value}\n")


def main() -> int:
    username = os.environ.get("RECORDLOOP_LOGIN_USERNAME", "")
    password = os.environ.get("RECORDLOOP_LOGIN_PASSWORD", "")
    if not username or not password:
        missing = []
        if not username:
            missing.append("RECORDLOOP_LOGIN_USERNAME (login-username)")
        if not password:
            missing.append("RECORDLOOP_LOGIN_PASSWORD (login-password)")
        print(
            "login_capture: missing required input(s): " + ", ".join(missing),
            file=sys.stderr,
        )
        return 2

    login_url_raw = os.environ.get("RECORDLOOP_LOGIN_URL", "") or _DEFAULT_LOGIN_URL
    base_url = os.environ.get("RECORDLOOP_BASE_URL", "")
    login_url = _resolve_login_url(login_url_raw, base_url)

    user_sel = os.environ.get("RECORDLOOP_LOGIN_USERNAME_SELECTOR", "") or _DEFAULT_USERNAME_SELECTOR
    pass_sel = os.environ.get("RECORDLOOP_LOGIN_PASSWORD_SELECTOR", "") or _DEFAULT_PASSWORD_SELECTOR
    submit_sel = os.environ.get("RECORDLOOP_LOGIN_SUBMIT_SELECTOR", "") or _DEFAULT_SUBMIT_SELECTOR
    success_url = os.environ.get("RECORDLOOP_LOGIN_SUCCESS_URL", "")

    print(f"login: navigating to {login_url}")
    print(f"login: username selector = {user_sel}")
    print(f"login: password selector = {pass_sel}")
    print(f"login: submit selector   = {submit_sel}")
    if success_url:
        print(f"login: success url glob  = {success_url}")
    else:
        print(f"login: success           = page leaves {login_url}")

    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            ctx = browser.new_context()
            page = ctx.new_page()
            page.goto(login_url, wait_until="domcontentloaded", timeout=30_000)
            try:
                page.fill(user_sel, username, timeout=10_000)
            except PWTimeout:
                raise SystemExit(
                    f"login: could not find a username/email field on {page.url}. "
                    f"Tried selector {user_sel!r}. "
                    f"Override with login-username-selector if your form uses a custom input."
                )
            try:
                page.fill(pass_sel, password, timeout=10_000)
            except PWTimeout:
                raise SystemExit(
                    f"login: could not find a password field. Tried selector {pass_sel!r}. "
                    f"Override with login-password-selector if your form uses a custom input."
                )
            try:
                page.click(submit_sel, timeout=10_000)
            except PWTimeout:
                raise SystemExit(
                    f"login: could not find a submit button. Tried selector {submit_sel!r}. "
                    f"Override with login-submit-selector if your form uses a custom button."
                )

            if success_url:
                try:
                    page.wait_for_url(success_url, timeout=30_000)
                except PWTimeout:
                    raise SystemExit(
                        f"login: page reached {page.url!r} but never matched "
                        f"login-success-url glob {success_url!r} within 30s. "
                        f"Verify the credentials and the success-url pattern."
                    )
            else:
                # No explicit success URL: success = the page left the login URL.
                # We watch for a URL change with the same path stripped off and
                # also wait for network to settle so post-login redirects land.
                try:
                    page.wait_for_function(
                        "(loginUrl) => location.href !== loginUrl",
                        arg=login_url,
                        timeout=30_000,
                    )
                except PWTimeout:
                    raise SystemExit(
                        f"login: page is still on {page.url!r} 30s after submit — "
                        f"login probably failed. Verify the credentials, or set "
                        f"login-success-url to a glob the post-login page matches."
                    )
                try:
                    page.wait_for_load_state("networkidle", timeout=10_000)
                except PWTimeout:
                    pass

            state = ctx.storage_state()
        finally:
            browser.close()

    state_json = json.dumps(state, separators=(",", ":"))
    encoded = base64.b64encode(state_json.encode("utf-8")).decode("ascii")
    _emit_github_env("RECORDLOOP_STORAGE_STATE", encoded)
    print(f"login: storage_state captured ({len(state.get('cookies', []))} cookies)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
