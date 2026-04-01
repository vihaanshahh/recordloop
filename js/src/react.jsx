/**
 * RecordLoop React bindings.
 *
 * Usage:
 *   import { RecordLoopProvider, useRecordLoop } from 'recordloop/react'
 *
 *   function App() {
 *     return (
 *       <RecordLoopProvider endpoint="http://localhost:8787">
 *         <MyApp />
 *       </RecordLoopProvider>
 *     )
 *   }
 *
 *   function DebugPanel() {
 *     const { actions, recording, start, stop, session } = useRecordLoop()
 *     return <button onClick={recording ? stop : start}>
 *       {recording ? `Stop (${actions.length})` : 'Record'}
 *     </button>
 *   }
 */

import { createContext, useContext, useRef, useState, useEffect, useCallback } from 'react'
import { RecordLoop } from './core.js'

const RecordLoopContext = createContext(null)

/**
 * Provider that initializes RecordLoop and shares it via context.
 *
 * Props:
 *   endpoint  - URL to POST session data (optional)
 *   autoStart - Start recording on mount (default: false)
 *   options   - Additional RecordLoop options
 *   children
 */
export function RecordLoopProvider({ endpoint, autoStart = false, options = {}, children }) {
  const rlRef = useRef(null)
  const [recording, setRecording] = useState(false)
  const [actions, setActions] = useState([])
  const intervalRef = useRef(null)

  // Create instance once
  if (!rlRef.current) {
    rlRef.current = new RecordLoop({ endpoint, ...options })
  }

  const start = useCallback(() => {
    rlRef.current.start()
    setRecording(true)
    // Poll action count so components can react
    intervalRef.current = setInterval(() => {
      setActions([...rlRef.current.actions])
    }, 500)
  }, [])

  const stop = useCallback(() => {
    const session = rlRef.current.stop()
    setRecording(false)
    setActions([...rlRef.current.actions])
    if (intervalRef.current) {
      clearInterval(intervalRef.current)
      intervalRef.current = null
    }
    return session
  }, [])

  useEffect(() => {
    if (autoStart) start()
    return () => {
      if (rlRef.current.recording) rlRef.current.stop()
      if (intervalRef.current) clearInterval(intervalRef.current)
    }
  }, [autoStart, start])

  const value = {
    /** The RecordLoop instance */
    recorder: rlRef.current,
    /** Current list of recorded actions */
    actions,
    /** Whether currently recording */
    recording,
    /** Start recording */
    start,
    /** Stop recording — returns the session object */
    stop,
    /** Get the full session object */
    session: () => rlRef.current.getSession(),
  }

  return (
    <RecordLoopContext.Provider value={value}>
      {children}
    </RecordLoopContext.Provider>
  )
}

/**
 * Hook to access RecordLoop from any component.
 */
export function useRecordLoop() {
  const ctx = useContext(RecordLoopContext)
  if (!ctx) {
    throw new Error('useRecordLoop must be used within a <RecordLoopProvider>')
  }
  return ctx
}

/**
 * Standalone hook — no provider needed. Creates its own instance.
 *
 * Usage:
 *   const { recording, start, stop, actions } = useRecorder({ endpoint: '...' })
 */
export function useRecorder(options = {}) {
  const rlRef = useRef(null)
  const [recording, setRecording] = useState(false)
  const [actions, setActions] = useState([])

  if (!rlRef.current) {
    rlRef.current = new RecordLoop(options)
  }

  const start = useCallback(() => {
    rlRef.current.start()
    setRecording(true)
  }, [])

  const stop = useCallback(() => {
    const session = rlRef.current.stop()
    setRecording(false)
    setActions([...rlRef.current.actions])
    return session
  }, [])

  useEffect(() => {
    return () => {
      if (rlRef.current.recording) rlRef.current.stop()
    }
  }, [])

  return { recorder: rlRef.current, recording, actions, start, stop }
}
