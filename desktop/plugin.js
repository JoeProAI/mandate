// Mandate for Hermes Desktop: jobs, policy diff, drift timeline and the Leash Line.
// Plain ESM, loaded uncompiled. Only @hermes/plugin-sdk and react/jsx-runtime are imported.
// Read-only: data comes from this plugin's own backend (dashboard/plugin_api.py) via ctx.rest.
import { host, useQuery, ROUTES_AREA, SIDEBAR_NAV_AREA, PALETTE_AREA } from '@hermes/plugin-sdk'
import { useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'mandate'
const PATH = '/mandate'
// Signal colours carry meaning (allow, deny, pending), so they stay fixed across themes.
const ALLOW = '#12E08A'
const DENY = '#FF4D2E'
const PENDING = '#F5C542'
const INK = '#0B1426'
const PAPER = '#E8EEF8'
const MONO = "'IBM Plex Mono', ui-monospace, SFMono-Regular, Menlo, monospace"
const DISPLAY = "'Bricolage Grotesque', ui-sans-serif, system-ui, sans-serif"
const SPARK = '\u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588'
const FOCUS = 'outline-none focus-visible:ring-2 focus-visible:ring-(--ui-accent) focus-visible:ring-offset-1'

let restRef = null
const api = path => restRef(path)

function when(ts) {
  if (!ts) return ''
  const d = new Date(ts * 1000)
  return d.toISOString().replace('T', ' ').slice(0, 19)
}

function Section({ title, children }) {
  return jsxs('section', {
    'aria-label': title,
    className: 'flex flex-col gap-2',
    children: [
      jsx('h2', {
        style: { fontFamily: DISPLAY },
        className: 'text-xs font-semibold uppercase tracking-wider text-(--ui-text-tertiary)',
        children: title
      }),
      children
    ]
  })
}

function JobList({ jobs, selected, onSelect }) {
  if (!jobs.length) {
    return jsx('p', {
      className: 'text-sm text-(--ui-text-tertiary)',
      children: 'No jobs yet. Run: hermes mandate watch <job>'
    })
  }
  return jsx('ul', {
    role: 'listbox',
    'aria-label': 'Mandate jobs',
    className: 'flex flex-col gap-1',
    children: jobs.map(j =>
      jsx(
        'li',
        {
          children: jsx('button', {
            type: 'button',
            role: 'option',
            'aria-selected': j.name === selected,
            onClick: () => onSelect(j.name),
            className:
              'w-full rounded px-2 py-1.5 text-left text-sm hover:bg-(--chrome-action-hover) ' +
              FOCUS +
              (j.name === selected ? ' bg-(--chrome-action-hover)' : ''),
            children: jsxs('span', {
              className: 'flex items-center justify-between gap-2',
              children: [
                jsx('span', { style: { fontFamily: MONO }, children: j.name }),
                jsxs('span', {
                  className: 'text-[0.6875rem]',
                  style: { fontFamily: MONO, color: j.mode === 'enforce' && j.version ? DENY : PENDING },
                  children: [
                    j.mode === 'enforce' && j.version ? 'ENFORCING' : 'OBSERVING',
                    j.version ? ` v${j.version}` : '',
                    j.open_drift ? ` \u25bc${j.open_drift}` : ''
                  ]
                })
              ]
            })
          })
        },
        j.id
      )
    )
  })
}

function Track({ track, width }) {
  const peak = Math.max(1, ...track.allowed.map(r => r.evidence))
  const cells = track.allowed.map(r => SPARK[Math.min(7, Math.max(0, Math.round((7 * r.evidence) / peak)))].repeat(2))
  const used = cells.length * 2 + track.pending.length * 2
  const open = track.notches.filter(n => n.status === 'open').length
  return jsxs('div', {
    role: 'listitem',
    className: 'flex flex-wrap items-baseline gap-x-3 gap-y-1',
    style: { fontFamily: MONO },
    children: [
      jsx('span', { className: 'w-20 shrink-0', style: { color: PAPER }, children: track.name }),
      jsxs('span', {
        'aria-label': `${track.allowed.length} allowed, ${track.pending.length} pending, ${open} blocked`,
        children: [
          jsx('span', { style: { color: '#6B7A99' }, children: '\u2595' }),
          jsx('span', {
            style: { color: ALLOW },
            title: track.allowed.map(r => `${r.value}  seen ${r.evidence}x`).join('\n'),
            children: cells.join('')
          }),
          jsx('span', { style: { color: PENDING }, children: '\u25c6\u25c6'.repeat(track.pending.length) }),
          jsx('span', { style: { color: '#6B7A99' }, children: '\u2500'.repeat(Math.max(0, width - used)) + '\u2503' })
        ]
      }),
      ...track.notches.slice(-6).map(n =>
        jsx(
          'span',
          {
            tabIndex: 0,
            className: 'rounded px-0.5 ' + FOCUS,
            title: `${n.call}\n${n.reason}`,
            'aria-label': `drift ${n.id} ${n.status}: ${n.call}`,
            style: { color: n.status === 'open' ? DENY : PENDING },
            children: (n.status === 'open' ? '\u25bc#' : '\u25b3#') + n.id
          },
          n.id
        )
      ),
      jsx('span', {
        className: 'text-xs',
        style: { color: '#6B7A99' },
        children: `${track.allowed.length} allowed` + (open ? ` \u00b7 ${open} blocked` : '')
      })
    ]
  })
}

function LeashLine({ data }) {
  const width = Math.max(8, ...data.tracks.map(t => t.allowed.length * 2 + t.pending.length * 2))
  const stamp = data.enforcing ? 'ENFORCING' : 'OBSERVING'
  return jsxs('div', {
    className: 'flex flex-col gap-2 rounded-md p-4',
    style: { background: INK, color: PAPER },
    children: [
      jsxs('div', {
        className: 'flex flex-wrap items-center gap-3',
        children: [
          jsx('span', { style: { fontFamily: DISPLAY }, className: 'text-lg font-bold', children: data.job.name }),
          data.active &&
            jsx('span', {
              style: { fontFamily: MONO, color: '#6B7A99' },
              className: 'text-xs',
              children: `v${data.active.version} sha256:${data.active.sha256.slice(0, 12)}`
            }),
          jsx('span', {
            style: {
              fontFamily: MONO,
              color: data.enforcing ? DENY : PENDING,
              border: `2px solid ${data.enforcing ? DENY : PENDING}`
            },
            className: 'rounded-sm px-2 py-0.5 text-xs font-bold tracking-widest',
            children: stamp
          })
        ]
      }),
      jsx('div', {
        role: 'list',
        'aria-label': 'Leash Line',
        className: 'flex flex-col gap-1 text-sm',
        children: data.tracks.map(t => jsx(Track, { track: t, width }, t.name))
      }),
      jsx('div', {
        style: { fontFamily: MONO, color: '#6B7A99' },
        className: 'text-xs',
        children: `${data.job.runs} run(s) observed`
      })
    ]
  })
}

function Diff({ data }) {
  if (!data.pending) {
    return jsx('p', {
      className: 'text-sm text-(--ui-text-tertiary)',
      children: 'No pending version. Propose or widen from the CLI; approval stays in the terminal.'
    })
  }
  const lines = data.diff.split('\n').filter(Boolean)
  return jsxs('div', {
    className: 'flex flex-col gap-1',
    children: [
      jsx('pre', {
        tabIndex: 0,
        'aria-label': `Diff to pending v${data.pending.version}`,
        className: 'overflow-auto rounded-md border border-(--ui-stroke-secondary) p-3 text-xs ' + FOCUS,
        style: { fontFamily: MONO },
        children: lines.map((l, i) =>
          jsx(
            'div',
            {
              style: {
                color: l.startsWith('+') && !l.startsWith('+++') ? ALLOW : l.startsWith('-') && !l.startsWith('---') ? DENY : undefined
              },
              className: l.startsWith('@@') ? 'text-(--ui-text-tertiary)' : '',
              children: l
            },
            i
          )
        )
      }),
      jsx('code', {
        style: { fontFamily: MONO },
        className: 'text-xs text-(--ui-text-tertiary)',
        children: `hermes mandate approve ${data.job.name}`
      })
    ]
  })
}

function DriftTimeline({ data }) {
  if (!data.drift.length) {
    return jsx('p', { className: 'text-sm text-(--ui-text-tertiary)', children: 'No blocked calls.' })
  }
  return jsx('ol', {
    'aria-label': 'Drift timeline, newest first',
    className: 'flex flex-col gap-2',
    children: data.drift.map(d =>
      jsxs(
        'li',
        {
          tabIndex: 0,
          className: 'rounded-md border-l-4 py-1 pl-3 ' + FOCUS,
          style: { borderColor: d.status === 'open' ? DENY : PENDING, fontFamily: MONO },
          children: [
            jsxs('div', {
              className: 'flex flex-wrap gap-3 text-xs text-(--ui-text-tertiary)',
              children: [
                jsx('span', { style: { color: d.status === 'open' ? DENY : PENDING }, children: `#${d.id}` }),
                jsx('span', { children: when(d.ts) }),
                jsx('span', { children: `run ${d.run_id}` }),
                jsx('span', { children: `[${d.family}]` }),
                jsx('span', { children: d.status })
              ]
            }),
            jsx('div', { className: 'text-sm', children: d.call }),
            jsx('div', { className: 'text-xs text-(--ui-text-secondary)', children: d.reason })
          ]
        },
        d.id
      )
    )
  })
}

function JobDetail({ name }) {
  const q = useQuery({
    queryKey: [ID, 'job', name],
    queryFn: () => api(`/jobs/${encodeURIComponent(name)}`),
    refetchInterval: 5000
  })
  if (q.error) return jsx('p', { role: 'alert', className: 'text-sm', style: { color: DENY }, children: String(q.error.message || q.error) })
  if (!q.data) return jsx('p', { className: 'text-sm text-(--ui-text-tertiary)', children: 'Loading\u2026' })
  return jsxs('div', {
    className: 'flex flex-col gap-6',
    children: [
      jsx(LeashLine, { data: q.data }),
      jsx(Section, { title: 'Policy diff', children: jsx(Diff, { data: q.data }) }),
      jsx(Section, { title: 'Drift timeline', children: jsx(DriftTimeline, { data: q.data }) })
    ]
  })
}

let lastSelected = null

function MandatePage() {
  const q = useQuery({ queryKey: [ID, 'jobs'], queryFn: () => api('/jobs'), refetchInterval: 5000 })
  const jobs = (q.data && q.data.jobs) || []
  const [picked, setPicked] = useState(null)
  const selected = picked || lastSelected || (jobs[0] && jobs[0].name) || null
  const onSelect = name => {
    lastSelected = name
    setPicked(name)
  }
  return jsxs('div', {
    className: 'flex h-full min-h-0 gap-6 overflow-auto p-6',
    children: [
      jsxs('nav', {
        'aria-label': 'Jobs',
        className: 'flex w-64 shrink-0 flex-col gap-3',
        children: [
          jsx('h1', { style: { fontFamily: DISPLAY }, className: 'text-xl font-bold', children: 'Mandate' }),
          q.error
            ? jsx('p', {
                role: 'alert',
                className: 'text-sm',
                style: { color: DENY },
                children: 'Backend unavailable. Enable the mandate plugin: hermes plugins enable mandate'
              })
            : jsx(JobList, { jobs, selected, onSelect })
        ]
      }),
      jsx('main', {
        className: 'min-w-0 flex-1',
        children: selected ? jsx(JobDetail, { name: selected }) : null
      })
    ]
  })
}

export default {
  id: ID,
  name: 'Mandate',
  defaultEnabled: false,
  register(ctx) {
    restRef = ctx.rest
    ctx.onDispose(() => {
      restRef = null
      lastSelected = null
    })
    ctx.registerMany([
      { id: 'page', area: ROUTES_AREA, data: { path: PATH }, render: () => jsx(MandatePage, {}) },
      { id: 'nav', area: SIDEBAR_NAV_AREA, data: { path: PATH, label: 'Mandate', codicon: 'shield' } },
      {
        id: 'open',
        area: PALETTE_AREA,
        data: { id: 'mandate.open', label: 'Mandate: open jobs', keywords: ['mandate', 'policy', 'drift'], run: () => host.navigate(PATH) }
      }
    ])
  }
}
