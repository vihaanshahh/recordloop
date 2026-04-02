/**
 * RecordLoop Vue bindings.
 *
 * Usage (plugin):
 *   import { RecordLoopPlugin } from 'recordloop/vue'
 *   app.use(RecordLoopPlugin, { endpoint: 'http://localhost:8787' })
 *
 *   // In any component:
 *   const { recording, actions, start, stop } = useRecordLoop()
 *
 * Usage (composable, no plugin needed):
 *   import { useRecorder } from 'recordloop/vue'
 *   const { recording, actions, start, stop } = useRecorder()
 */

import { ref, inject, onUnmounted, provide } from 'vue'
import { RecordLoop } from './core.js'

const RECORDLOOP_KEY = Symbol('recordloop')

/**
 * Vue plugin — installs RecordLoop globally.
 */
export const RecordLoopPlugin = {
  install(app, options = {}) {
    const recorder = new RecordLoop(options)
    const recording = ref(false)
    const actions = ref([])
    let interval = null

    const start = () => {
      recorder.start()
      recording.value = true
      interval = setInterval(() => {
        actions.value = [...recorder.actions]
      }, 500)
    }

    const stop = () => {
      const session = recorder.stop()
      recording.value = false
      actions.value = [...recorder.actions]
      if (interval) {
        clearInterval(interval)
        interval = null
      }
      return session
    }

    const ctx = { recorder, recording, actions, start, stop }
    app.provide(RECORDLOOP_KEY, ctx)

    // Also make available as global property
    app.config.globalProperties.$recordloop = ctx
  },
}

/**
 * Composable — use inside a component when the plugin is installed.
 */
export function useRecordLoop() {
  const ctx = inject(RECORDLOOP_KEY)
  if (!ctx) {
    throw new Error(
      'useRecordLoop() requires RecordLoopPlugin. ' +
      'Use app.use(RecordLoopPlugin, options) or useRecorder() instead.'
    )
  }
  return ctx
}

/**
 * Standalone composable — no plugin needed.
 *
 * Usage:
 *   const { recording, actions, start, stop } = useRecorder({ endpoint: '...' })
 */
export function useRecorder(options = {}) {
  const recorder = new RecordLoop(options)
  const recording = ref(false)
  const actions = ref([])

  const start = () => {
    recorder.start()
    recording.value = true
  }

  const stop = () => {
    const session = recorder.stop()
    recording.value = false
    actions.value = [...recorder.actions]
    return session
  }

  onUnmounted(() => {
    if (recorder.recording) recorder.stop()
  })

  return { recorder, recording, actions, start, stop }
}
