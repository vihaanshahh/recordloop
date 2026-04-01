// RecordLoop v0.1.0 — https://github.com/vihaanshahh/recordloop
// Drop this script into any page to capture interactions.
;(function(root) {
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
 * Build a CSS selector path for an element.
 * Prefers: data-testid > id > name attr > nth-child path.
 */
function getSelector(el) {
  if (!el || el === document.body || el === document.documentElement) {
    return 'body'
  }

  // data-testid (best practice in React/Vue/etc.)
  const testId = el.getAttribute('data-testid') || el.getAttribute('data-test-id')
  if (testId) return `[data-testid="${testId}"]`

  // id
  if (el.id) return `#${CSS.escape(el.id)}`

  // name attribute (forms)
  if (el.name && el.tagName !== 'DIV') {
    const tag = el.tagName.toLowerCase()
    return `${tag}[name="${el.name}"]`
  }

  // aria-label
  const ariaLabel = el.getAttribute('aria-label')
  if (ariaLabel) {
    const tag = el.tagName.toLowerCase()
    return `${tag}[aria-label="${ariaLabel}"]`
  }

  // role + text content (buttons, links)
  const role = el.getAttribute('role') || (el.tagName === 'BUTTON' ? 'button' : null)
  if (role && el.textContent && el.textContent.trim().length < 50) {
    return `${el.tagName.toLowerCase()}:has-text("${el.textContent.trim()}")`
  }

  // Build nth-child path (fallback)
  const parts = []
  let current = el
  while (current && current !== document.body) {
    const tag = current.tagName.toLowerCase()
    const parent = current.parentElement
    if (parent) {
      const siblings = Array.from(parent.children).filter(c => c.tagName === current.tagName)
      if (siblings.length > 1) {
        const idx = siblings.indexOf(current) + 1
        parts.unshift(`${tag}:nth-child(${idx})`)
      } else {
        parts.unshift(tag)
      }
    } else {
      parts.unshift(tag)
    }
    current = parent
    // Keep paths reasonable
    if (parts.length >= 4) break
  }
  return parts.join(' > ')
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
 * Core recorder class.
 */
class RecordLoop {
  constructor(options = {}) {
    this.options = { ...DEFAULT_OPTIONS, ...options }
    this.actions = []
    this.recording = false
    this.sessionId = null
    this.startedAt = null
    this._listeners = []
    this._scrollTimer = null
    this._inputTimers = new Map()
  }

  /**
   * Start recording interactions.
   */
  start() {
    if (this.recording) return this
    this.recording = true
    this.actions = []
    this.sessionId = _uid()
    this.startedAt = Date.now()

    // Record initial navigation
    this._push({
      type: 'navigate',
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
    }
    if (this.options.scroll) {
      this._listen('scroll', this._onScroll, true)
    }

    // Intercept pushState/replaceState for SPA navigation
    if (this.options.navigation) {
      this._patchHistory()
    }

    return this
  }

  /**
   * Stop recording, return the session.
   */
  stop() {
    if (!this.recording) return null
    this.recording = false

    // Remove listeners
    for (const { target, event, handler, capture } of this._listeners) {
      target.removeEventListener(event, handler, capture)
    }
    this._listeners = []

    // Restore history
    if (this._origPushState) {
      history.pushState = this._origPushState
      history.replaceState = this._origReplaceState
      this._origPushState = null
      this._origReplaceState = null
    }

    const session = this.getSession()

    // Send to endpoint if configured
    if (this.options.endpoint) {
      this._send(session)
    }

    return session
  }

  /**
   * Get the current session data.
   */
  getSession() {
    return {
      id: this.sessionId,
      url: window.location.href,
      startedAt: this.startedAt,
      duration: Date.now() - this.startedAt,
      userAgent: navigator.userAgent,
      viewport: {
        width: window.innerWidth,
        height: window.innerHeight,
      },
      actions: [...this.actions],
      meta: this.options.meta,
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

    const action = {
      type: 'click',
      selector: getSelector(el),
      label: getLabel(el),
      tag: el.tagName.toLowerCase(),
      position: { x: e.clientX, y: e.clientY },
    }

    // Detect double-click (two clicks within 300ms on same selector)
    const prev = this.actions[this.actions.length - 1]
    if (prev && prev.type === 'click' && prev.selector === action.selector) {
      const gap = Date.now() - this.startedAt - prev.timestamp * 1000
      if (gap < 300) {
        // Convert previous click to double_click
        this.actions[this.actions.length - 1].type = 'double_click'
        return
      }
    }

    this._push(action)
  }

  _onInput = (e) => {
    const el = e.target
    if (this._shouldIgnore(el)) return

    const selector = getSelector(el)

    // Debounce input events per element — we want the final value, not each keystroke
    if (this._inputTimers.has(selector)) {
      clearTimeout(this._inputTimers.get(selector))
    }

    this._inputTimers.set(selector, setTimeout(() => {
      this._inputTimers.delete(selector)

      // Remove any pending type action for this selector
      for (let i = this.actions.length - 1; i >= 0; i--) {
        if (this.actions[i].type === 'type' && this.actions[i].selector === selector) {
          this.actions.splice(i, 1)
          break
        }
      }

      this._push({
        type: 'type',
        selector,
        value: el.value,
        tag: el.tagName.toLowerCase(),
        inputType: el.type || 'text',
      })
    }, 300))
  }

  _onChange = (e) => {
    const el = e.target
    if (this._shouldIgnore(el)) return

    const tag = el.tagName.toLowerCase()
    const selector = getSelector(el)

    if (tag === 'select') {
      this._push({
        type: 'select',
        selector,
        value: el.value,
        label: el.options[el.selectedIndex]?.text,
      })
    } else if (el.type === 'checkbox') {
      this._push({
        type: el.checked ? 'check' : 'uncheck',
        selector,
      })
    } else if (el.type === 'radio') {
      this._push({
        type: 'check',
        selector,
        value: el.value,
      })
    }
  }

  _onNav = () => {
    this._push({
      type: 'navigate',
      value: window.location.href,
    })
  }

  _onScroll = () => {
    if (this._scrollTimer) clearTimeout(this._scrollTimer)
    this._scrollTimer = setTimeout(() => {
      this._push({
        type: 'scroll',
        position: { x: window.scrollX, y: window.scrollY },
      })
    }, this.options.debounceMs)
  }

  // --- Internals ---

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

  _push(action) {
    if (!this.recording) return
    if (this.options.maxActions && this.actions.length >= this.options.maxActions) return

    action.timestamp = (Date.now() - this.startedAt) / 1000
    this.actions.push(action)
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
}

function _uid() {
  return Math.random().toString(36).slice(2, 10) + Date.now().toString(36)
}



  root.RecordLoop = RecordLoop;
})(typeof window !== 'undefined' ? window : globalThis);
