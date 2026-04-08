/**
 * RecordLoop Core — captures browser interactions from the DOM.
 *
 * Works in any framework. Records clicks, typing, navigation, form changes,
 * and scroll events. Outputs a session JSON that the Python side replays
 * with Playwright.
 *
 * Usage:
 *   import { RecordLoop } from 'recordloop'
 *   const rl = new RecordLoop({ endpoint: 'http://localhost:8787' })
 *   rl.start()
 *   // ... user interacts ...
 *   const session = rl.stop()
 */

const DEFAULT_OPTIONS = {
  // Where to POST session data (null = don't send, just collect)
  endpoint: null,

  // InstantDB config (optional — enables real-time dashboard)
  instantdb: null,  // { appId: '...', token: '...' } or null

  // Capture these event types
  clicks: true,
  input: true,
  navigation: true,
  scroll: true,
  resize: false,

  // Ignore selectors (e.g. devtools, recordloop's own UI)
  ignore: ['[data-recordloop-ignore]'],

  // Max actions before auto-flushing (0 = unlimited)
  maxActions: 10000,

  // Debounce scroll/resize events (ms)
  debounceMs: 150,

  // Include page snapshots (outerHTML of target elements)
  snapshots: false,

  // Session metadata
  meta: {},
}

/**
 * Detect auto-generated ids that are not meaningful for stable selectors.
 * Patterns: starts with "react-", is purely hex, contains "__", or is just digits.
 */
function _isAutoId(id) {
  if (!id) return true
  if (/^react-/.test(id)) return true
  if (/^[0-9a-f]{8,}$/i.test(id)) return true
  if (id.includes('__')) return true
  if (/^\d+$/.test(id)) return true
  return false
}

/**
 * Build a minimal nth-child xpath path for an element (fallback strategy).
 * Returns a string like: div[1]/span[2]/button[1]
 */
function _buildXPath(el) {
  const parts = []
  let current = el
  while (current && current !== document.body && current !== document.documentElement) {
    const tag = current.tagName.toLowerCase()
    const parent = current.parentElement
    if (parent) {
      const siblings = Array.from(parent.children).filter(c => c.tagName === current.tagName)
      if (siblings.length > 1) {
        const idx = siblings.indexOf(current) + 1
        parts.unshift(`${tag}[${idx}]`)
      } else {
        parts.unshift(tag)
      }
    } else {
      parts.unshift(tag)
    }
    current = parent
    if (parts.length >= 4) break
  }
  return '//' + parts.join('/')
}

/**
 * Return a semantic key object describing how to uniquely identify an element.
 *
 * Strategy priority: testid > id > name > aria_label > role_text > xpath
 *
 * Returns: { strategy: string, value: string, tag: string, text: string|null }
 *
 * IMPORTANT: `value` never contains Playwright pseudo-syntax like :has-text().
 * For role_text the value is the plain text content of the element.
 */
function getSemanticKey(el) {
  if (!el || el === document.body || el === document.documentElement) {
    return { strategy: 'xpath', value: '//body', tag: 'body', text: null }
  }

  const tag = el.tagName.toLowerCase()
  const rawText = el.textContent ? el.textContent.trim() : null
  const text = rawText && rawText.length > 0 ? rawText.slice(0, 200) : null

  // strategy: testid
  const testId = el.getAttribute('data-testid') || el.getAttribute('data-test-id')
  if (testId) {
    return { strategy: 'testid', value: testId, tag, text }
  }

  // strategy: id (skip auto-generated ids)
  if (el.id && !_isAutoId(el.id)) {
    return { strategy: 'id', value: el.id, tag, text }
  }

  // strategy: name (forms — skip divs which sometimes have name)
  if (el.getAttribute('name') && tag !== 'div') {
    return { strategy: 'name', value: el.getAttribute('name'), tag, text }
  }

  // strategy: aria_label
  const ariaLabel = el.getAttribute('aria-label')
  if (ariaLabel) {
    return { strategy: 'aria_label', value: ariaLabel, tag, text }
  }

  // strategy: role_text
  // Applies when element has explicit role, OR is a button/anchor/heading with short text
  const role = el.getAttribute('role')
  const isSemanticTag = ['button', 'a', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'].includes(tag)
  if ((role || isSemanticTag) && text && text.length <= 50) {
    return { strategy: 'role_text', value: text, tag, text }
  }

  // strategy: xpath (fallback)
  return { strategy: 'xpath', value: _buildXPath(el), tag, text }
}

/**
 * Get a human-readable label for an element.
 */
function getLabel(el) {
  const text = el.textContent?.trim().slice(0, 60)
  const placeholder = el.getAttribute('placeholder')
  const ariaLabel = el.getAttribute('aria-label')
  const title = el.getAttribute('title')
  return ariaLabel || placeholder || title || text || el.tagName.toLowerCase()
}

/**
 * Short UUID for individual actions.
 */
function _shortUid() {
  return Math.random().toString(36).slice(2, 9)
}

/**
 * Full session UUID.
 */
function _uid() {
  return Math.random().toString(36).slice(2, 10) + Date.now().toString(36)
}

/**
 * Core recorder class.
 */
export class RecordLoop {
  constructor(options = {}) {
    this.options = { ...DEFAULT_OPTIONS, ...options }
    this.actions = []
    this._recording = false
    this.sessionId = null
    this.startedAt = null
    this._listeners = []
    this._scrollTimer = null
    this._inputTimers = new Map()
    this._navHandler = null
    this._navTarget = null
    this._origPushState = null
    this._origReplaceState = null
  }

  /**
   * Public recording state (read-only).
   */
  get recording() {
    return this._recording
  }

  /**
   * Start recording interactions.
   */
  start() {
    if (this._recording) return this
    this._recording = true
    this.actions = []
    this.sessionId = _uid()
    this.startedAt = Date.now()

    // Record initial navigation
    this._push({
      type: 'navigate',
      key: null,
      value: window.location.href,
    })

    // Attach listeners
    if (this.options.clicks) this._listen('click', this._onClick, true)
    if (this.options.input) {
      this._listen('input', this._onInput, true)
      this._listen('change', this._onChange, true)
    }
    if (this.options.navigation) {
      this._listen('popstate', this._onNav)
      this._attachNavigationListener()
    }
    if (this.options.scroll) {
      this._listen('scroll', this._onScroll, true)
    }

    return this
  }

  /**
   * Stop recording, return the session.
   */
  stop() {
    if (!this._recording) return null
    this._recording = false

    // Remove DOM listeners
    for (const { target, event, handler, capture } of this._listeners) {
      target.removeEventListener(event, handler, capture)
    }
    this._listeners = []

    // Detach navigation listener
    if (this.options.navigation) {
      this._detachNavigationListener()
    }

    const session = this.getSession()

    // Send to endpoint if configured
    if (this.options.endpoint) {
      this._send(session)
    }

    // Sync to InstantDB if configured
    if (this.options.instantdb) {
      this._syncToInstantDB(session)
    }

    return session
  }

  /**
   * Get the current session data (schema version 2).
   */
  getSession() {
    const now = Date.now()
    return {
      id: this.sessionId,
      recorded_at: new Date(this.startedAt).toISOString(),
      duration_ms: now - this.startedAt,
      base_url: window.location.origin,
      viewport: [window.innerWidth, window.innerHeight],
      user_agent: navigator.userAgent,
      schema_version: '2',
      meta: this.options.meta,
      actions: [...this.actions],
    }
  }

  /**
   * Get action count.
   */
  get length() {
    return this.actions.length
  }

  // --- Event handlers ---

  _onClick = (e) => {
    const el = e.target
    if (this._shouldIgnore(el)) return

    const key = getSemanticKey(el)

    const action = {
      type: 'click',
      key,
      value: null,
      position: { x: e.clientX, y: e.clientY },
    }

    // Detect double-click (two clicks within 300ms on same element key value)
    const prev = this.actions[this.actions.length - 1]
    if (
      prev &&
      prev.type === 'click' &&
      prev.key &&
      key &&
      prev.key.strategy === key.strategy &&
      prev.key.value === key.value
    ) {
      const gap = (Date.now() - this.startedAt) - prev.timestamp_ms
      if (gap < 300) {
        this.actions[this.actions.length - 1].type = 'double_click'
        return
      }
    }

    this._push(action)
  }

  _onInput = (e) => {
    const el = e.target
    if (this._shouldIgnore(el)) return

    const key = getSemanticKey(el)
    // Use a stable string for debounce map keying
    const keyStr = `${key.strategy}::${key.value}`

    // Debounce input events per element — we want the final value, not each keystroke
    if (this._inputTimers.has(keyStr)) {
      clearTimeout(this._inputTimers.get(keyStr))
    }

    this._inputTimers.set(keyStr, setTimeout(() => {
      this._inputTimers.delete(keyStr)

      // Remove any pending type action for this element key
      for (let i = this.actions.length - 1; i >= 0; i--) {
        const a = this.actions[i]
        if (
          a.type === 'type' &&
          a.key &&
          a.key.strategy === key.strategy &&
          a.key.value === key.value
        ) {
          this.actions.splice(i, 1)
          break
        }
      }

      this._push({
        type: 'type',
        key,
        value: el.value,
        inputType: el.type || 'text',
      })
    }, 300))
  }

  _onChange = (e) => {
    const el = e.target
    if (this._shouldIgnore(el)) return

    const tag = el.tagName.toLowerCase()
    const key = getSemanticKey(el)

    if (tag === 'select') {
      this._push({
        type: 'select',
        key,
        value: el.value,
        label: el.options[el.selectedIndex]?.text || null,
      })
    } else if (el.type === 'checkbox') {
      this._push({
        type: el.checked ? 'check' : 'uncheck',
        key,
        value: null,
      })
    } else if (el.type === 'radio') {
      this._push({
        type: 'check',
        key,
        value: el.value,
      })
    }
  }

  _onNav = () => {
    this._captureNavigation(window.location.href)
  }

  _captureNavigation(url) {
    this._push({
      type: 'navigate',
      key: null,
      value: url,
    })
  }

  _onScroll = () => {
    if (this._scrollTimer) clearTimeout(this._scrollTimer)
    this._scrollTimer = setTimeout(() => {
      this._push({
        type: 'scroll',
        key: null,
        value: null,
        position: { x: window.scrollX, y: window.scrollY },
      })
    }, this.options.debounceMs)
  }

  // --- Navigation API ---

  _attachNavigationListener() {
    if ('navigation' in window) {
      // Navigation API (Chrome 102+)
      const handler = (event) => {
        if (!this._recording) return
        this._captureNavigation(event.destination.url)
      }
      window.navigation.addEventListener('navigate', handler)
      this._navHandler = handler
      this._navTarget = window.navigation
    } else {
      // Fallback: patch history for older browsers
      this._patchHistory()
    }
  }

  _detachNavigationListener() {
    if (this._navTarget && this._navHandler) {
      this._navTarget.removeEventListener('navigate', this._navHandler)
      this._navHandler = null
      this._navTarget = null
    } else {
      this._unpatchHistory()
    }
  }

  _patchHistory() {
    this._origPushState = history.pushState
    this._origReplaceState = history.replaceState

    const self = this
    history.pushState = function (...args) {
      self._origPushState.apply(this, args)
      self._onNav()
    }
    history.replaceState = function (...args) {
      self._origReplaceState.apply(this, args)
      self._onNav()
    }
  }

  _unpatchHistory() {
    if (this._origPushState) {
      history.pushState = this._origPushState
      history.replaceState = this._origReplaceState
      this._origPushState = null
      this._origReplaceState = null
    }
  }

  // --- Internals ---

  _push(action) {
    if (!this._recording) return
    if (this.options.maxActions && this.actions.length >= this.options.maxActions) return

    const timestamp_ms = Date.now() - this.startedAt

    // Build the action record with stable schema fields first, then let
    // caller-supplied fields (position, inputType, label, etc.) augment it.
    // id and timestamp_ms are always set last so caller cannot accidentally
    // override them.
    const { type, key, value, ...rest } = action
    const full = {
      type,
      key: key !== undefined ? key : null,
      value: value !== undefined ? value : null,
      page_url: window.location.href,
      page_title: document.title || null,
      ...rest,
      id: _shortUid(),
      timestamp_ms,
    }

    this.actions.push(full)
  }

  _listen(event, handler, capture = false) {
    const target = (event === 'popstate') ? window : document
    target.addEventListener(event, handler, capture)
    this._listeners.push({ target, event, handler, capture })
  }

  _shouldIgnore(el) {
    if (!el) return true
    for (const sel of this.options.ignore) {
      if (el.closest(sel)) return true
    }
    return false
  }

  async _send(session) {
    try {
      await fetch(this.options.endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(session),
      })
    } catch (e) {
      console.warn('[RecordLoop] Failed to send session:', e.message)
    }
  }

  async _syncToInstantDB(session) {
    const { appId } = this.options.instantdb
    if (!appId) return

    try {
      await fetch('https://api.instantdb.com/admin/transact', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          'app_id': appId,
          steps: [
            ['update', 'sessions', session.id, {
              sessionId: session.id,
              baseUrl: session.base_url,
              actionCount: session.actions.length,
              durationMs: session.duration_ms,
              status: 'recorded',
              viewport: JSON.stringify(session.viewport),
              userAgent: session.user_agent,
              recordedAt: session.recorded_at,
              schemaVersion: session.schema_version,
            }],
          ],
        }),
      })
    } catch (e) {
      console.warn('[RecordLoop] Failed to sync to InstantDB:', e.message)
    }
  }

  /**
   * Download the session as a JSON file.
   * User can commit this to .recordloop/sessions/ for CI/CD processing.
   */
  download() {
    const session = this.getSession()
    const blob = new Blob([JSON.stringify(session, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${session.id}.json`
    a.click()
    URL.revokeObjectURL(url)
    return session
  }
}

export default RecordLoop
