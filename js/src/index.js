/**
 * RecordLoop — capture browser interactions from any frontend.
 *
 * Vanilla JS:
 *   import { RecordLoop } from 'recordloop'
 *   const rl = new RecordLoop({ endpoint: 'http://localhost:8787' })
 *   rl.start()
 *
 * Script tag:
 *   <script src="recordloop/dist/recordloop.js"></script>
 *   <script>
 *     const rl = new RecordLoop()
 *     rl.start()
 *   </script>
 *
 * React:  import { RecordLoopProvider, useRecordLoop } from 'recordloop/react'
 * Vue:    import { RecordLoopPlugin, useRecordLoop } from 'recordloop/vue'
 */

export { RecordLoop, default } from './core.js'
