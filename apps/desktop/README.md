# Thursday Desktop (scaffold)

Tauri 2 + React + TypeScript. ~10 MB binary with real OS access, so the desktop app can
**embed its own device node** rather than shipping a second process.

## What the UI must be

Per §62–64: a conversation and an orb, not a dashboard. The machinery is Thursday's business;
the owner sees one assistant.

```
┌──────────────────────────────────────────┐
│                  ◍                       │   Avatar / orb
│         (IDLE · LISTENING ·              │   §63 states drive one animation
│      THINKING · WORKING · SPEAKING ·     │
│              WARNING)                    │
├──────────────────────────────────────────┤
│  you>  Thursday เปิดไฟล์คะแนนล่าสุด        │
│  Thursday> รับทราบ กำลังตรวจไฟล์…          │   streamed over WS /realtime
│  Thursday> เสร็จแล้ว — 42 คน (ยืนยันแล้ว)  │
├──────────────────────────────────────────┤
│  Data · working    Writer · waiting      │   §64: collapsed agent strip,
│  Supervisor · checking                   │   expandable, never the main view
├──────────────────────────────────────────┤
│  ⚠ approval: send email to dean@…        │   §38: action · device · resource ·
│    [approve] [once] [always] [reject]    │   risk · outcome · cost of refusing
└──────────────────────────────────────────┘
```

Rules the UI must not break:

- Sub-agents never address the owner. One voice (§96).
- An unverified result is visually distinct from a verified one (§76) — the `verified`
  field on every reply exists for this.
- Approvals show the full context before the buttons, never after.
- The agent strip is collapsed by default. If the owner has to watch it, the design failed.

## Wiring

| Concern | Where |
|---|---|
| Conversation, streaming, state | `WS /api/v1/realtime` |
| Approvals | `approval_request` events in, `approve` messages out |
| Screen context (§30) | `context_update` messages with the active window and selection |
| Device actions | the embedded node speaks TNP/1 to `WS /api/v1/device` |
| Emergency stop (§69) | `POST /api/v1/emergency/stop` — a plain call, bypassing the model |

## Getting started

```bash
npm create tauri-app@latest -- --template react-ts
npm install && npm run tauri dev
```

The Rust side hosts the node runtime; the React side is presentation only. Business logic
stays in the core (§3 of the repository structure rules).
