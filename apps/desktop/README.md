# Thursday Desktop

Tauri 2 + React 18 + TypeScript + Vite + Tailwind. The window is a client of the local
API like any other; nothing here decides anything (PART 64).

## What it is

A conversation and an orb — not a dashboard. The machinery is Thursday's business; the
owner sees one assistant.

```
┌──────────────────────────────────┬──────────────┐
│                ◍                 │ tasks        │  Orb: one state field, one animation
│             thinking             │ devices      │
├──────────────────────────────────┤ memory       │
│  you>  Thursday เปิด chrome       │ permissions  │  Drawers, closed by default
│  Thursday> เปิด chrome เรียบร้อย   │              │
│            (ยืนยันแล้ว)           │              │
├──────────────────────────────────┤              │
│  computer · done                 │              │  Agent strip, collapsed
├──────────────────────────────────┤              │
│  ⚠ delete thesis.docx?           │              │  Full context above the buttons
│    what happens · what if not    │              │
│    [delete] [no]                 │              │
├──────────────────────────────────┴──────────────┤
│  Say what you need…                    [send]   │  esc stops whatever is running
└─────────────────────────────────────────────────┘
```

## Rules this UI does not break

- **One voice.** Sub-agents never address the owner (§96). The agent strip is a status
  line, not a second conversation, and it is collapsed by default. If the owner has to
  watch it to understand what is happening, the design has failed.
- **Unverified looks different from done.** Every reply carries `verified`; a dispatched
  action whose effect could not be observed is rendered with its own banner, never as a
  success (§76, PART 5.1).
- **Context before buttons.** An approval shows what will happen, to what, on which
  machine, and what refusing costs — above the choices, never behind a disclosure.
- **"Always allow" is only offered when it exists.** `scopes_offered` decides; for the
  ASK_ALWAYS set the dialog shows one-time answers only, because a standing grant is not
  something the engine would honour (ADR 0008).
- **A permission control that would not stick is not shown.** `can_relax` decides. A
  setting that saves and silently reverts teaches the owner something false about their
  own machine.
- **Stop is never buried.** Escape interrupts the turn; the tray's *Stop everything* posts
  to the API from Rust, so it works when the webview does not.

## Layout

```
src/
  App.tsx                  one window: conversation + drawers
  main.tsx                 entry point
  index.css                Tailwind layers
  hooks/useRealtime.ts     the single WebSocket, with reconnect backoff
  lib/api.ts               the REST surface, typed
  lib/origin.ts            where the API lives (dev proxy vs. packaged app)
  lib/types.ts             the contracts this UI renders
  components/
    Orb.tsx                PART 63/65 — avatar state as colour and motion
    Conversation.tsx       PART 64 — the interface
    AgentStrip.tsx         PART 66 — collapsed status line
    ApprovalDialog.tsx     PART 38/70 — context, then buttons
    TaskPanel.tsx          PART 67 — progress, pause, cancel
    DevicePanel.tsx        PART 68 — machines and what each will allow
    MemoryPanel.tsx        PART 69 — search, inspect, confirm, forget
    PermissionPanel.tsx    PART 70 — approval modes, standing grants, autonomy
src-tauri/
  src/main.rs              window, tray, and the emergency stop
  tauri.conf.json          window and CSP
```

## Wiring

| Concern | Where |
|---|---|
| Conversation, streaming, avatar state | `WS /api/v1/realtime` |
| Approvals | `approval.required` in, `POST /approvals/{id}/approve` out |
| Tasks, devices, memory, policies | `GET /api/v1/…`, polled while a drawer is open |
| Emergency stop (§69) | `POST /api/v1/emergency/stop` — a plain call, bypassing the model |

`lib/origin.ts` is the one place that knows the difference between development and a
packaged build: in dev, Vite proxies `/api` (so a relative URL is correct and there is no
CORS); packaged, the page is served from `tauri://localhost`, where a relative URL would
resolve to the bundle. Override with `VITE_THURSDAY_API` or `THURSDAY_API_URL`.

## Running it

The API must be up first — this app renders it, it does not host it.

```bash
# terminal 1
./scripts/dev.sh

# terminal 2
cd apps/desktop
npm install
npm run dev          # browser, against the dev proxy on :1420
npm run tauri dev    # the real window
```

`npm run typecheck` and `npm run build` are what CI runs. Both are worth running before a
commit: `tsc` and the bundler resolve paths independently, so one can pass while the other
fails.

## Not built yet

Voice capture, the embedded device node, and the screen-context channel. The window speaks
to the API over HTTP and WebSocket only; when the node moves in-process, it will speak
TNP/1 from the Rust side without changing anything in `src/`.
