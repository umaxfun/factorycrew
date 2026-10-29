// Factorio Companion status page: is every piece of the app alive? Polls the app backend's
// /api/status (through the gateway's proxy /apps/factorycrew/api/status) every 3 seconds.
// Plain ESM with React from the host, no build step.
const React = window.__kirocrew_modules.react
const { useAppApi } = window.__kirocrew_modules['@kirocrew/app-sdk']
const icons = window.__kirocrew_modules['lucide-react'] || {}
const { useState, useEffect, useRef, createElement: h } = React

const STATUS_URL = '/apps/factorycrew/api/status'
const EVERY_MS = 3000

const STYLE = {
  ok: { dot: 'bg-ok', text: 'text-ok', word: 'OK', icon: 'CheckCircle2' },
  warn: { dot: 'bg-warn', text: 'text-warn', word: 'Check', icon: 'AlertTriangle' },
  fail: { dot: 'bg-danger', text: 'text-danger', word: 'Down', icon: 'XCircle' },
  idle: { dot: 'bg-muted', text: 'text-muted', word: 'Unknown', icon: 'Circle' },
}

function Icon({ name, size = 16, className }) {
  const C = icons[name]
  return C ? h(C, { size, className }) : h('span', { className: `inline-block w-2.5 h-2.5 rounded-full ${className || ''}` })
}

function Row({ c }) {
  const s = STYLE[c.state] || STYLE.idle
  return h('div', { className: 'flex items-start gap-3 py-3 px-4 border-b border-border last:border-b-0' },
    h('div', { className: `pt-0.5 ${s.text}` }, h(Icon, { name: s.icon, className: s.text })),
    h('div', { className: 'min-w-0 flex-1' },
      h('div', { className: 'flex items-center gap-2' },
        h('span', { className: 'text-sm font-semibold text-text-strong' }, c.label),
        h('span', { className: `text-[12px] font-medium ${s.text}` }, s.word),
      ),
      h('div', { className: 'text-[13px] text-muted mt-0.5 break-words' }, c.detail),
      c.fix ? h('div', { className: 'text-[13px] text-text mt-1' }, c.fix) : null,
    ),
  )
}

function StatusPage() {
  const api = useAppApi()
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [at, setAt] = useState(null)
  const busy = useRef(false)

  const load = () => {
    if (busy.current) return
    busy.current = true
    api.get(STATUS_URL)
      .then(d => { setData(d); setError(null); setAt(new Date()) })
      .catch(e => { setError(e && e.message ? e.message : String(e)); setAt(new Date()) })
      .finally(() => { busy.current = false })
  }

  useEffect(() => {
    load()
    const t = setInterval(load, EVERY_MS)
    return () => clearInterval(t)
  }, [])

  const overall = error ? 'fail' : (data && data.state) || 'idle'
  const s = STYLE[overall] || STYLE.idle
  const headline = error
    ? 'The app backend does not answer'
    : !data ? 'Checking…'
      : overall === 'ok' ? 'Everything is running'
        : overall === 'warn' ? 'Running, something needs a look' : 'Something is down'

  return h('div', { className: 'px-6 pt-4 pb-8 max-w-3xl' },
    h('div', { className: 'flex items-end justify-between gap-4 pb-3' },
      h('div', null,
        h('div', { className: 'text-2xl font-bold tracking-tight text-text-strong flex items-center gap-2' },
          h(Icon, { name: 'Activity', size: 22 }), 'Factorio Companion'),
        h('div', { className: `text-sm mt-1 font-medium ${s.text}` }, headline),
      ),
      h('div', { className: 'text-[12px] text-muted text-right' },
        data && data.app_version ? `app ${data.app_version}` : '',
        h('br'),
        at ? `checked ${at.toLocaleTimeString()}` : ''),
    ),
    error
      ? h('div', { className: 'border border-border bg-card rounded-lg p-4 text-sm' },
          h('div', { className: 'text-danger font-medium' }, error),
          h('div', { className: 'text-muted mt-1' },
            'The backend runs the bridge. Disable and enable the app under Apps → Library; if this stays, ' +
            'check that the app is trusted (agent.apps_trusted).'))
      : h('div', { className: 'border border-border bg-card rounded-lg shadow-sm' },
          ...(data && data.checks ? data.checks : []).map(c => h(Row, { key: c.id, c }))),
    h('div', { className: 'mt-4 text-[12px] text-muted' },
      'Refreshes every 3 seconds. Factorio offline is normal while you are not playing.'),
  )
}

export default StatusPage
