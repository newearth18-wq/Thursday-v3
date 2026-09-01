# Thursday Mobile (planned — Phase 4)

Flutter. The phone is a **remote and an approval surface**, not a second brain: state lives
in the core, so a phone can pick up a task a PC started (§23, §65).

## Scope

| Capability | Why the phone |
|---|---|
| Voice remote | the fastest way to reach Thursday when away from a desk |
| Conversation | same session, continued across devices |
| Approvals (§38) | the owner is reachable; the desk is not |
| Device control (§22) | "is the home PC still on?" · "shut it down" |
| Task status (§64) | glanceable progress on long work |
| Camera input (§24) | point at a thing and ask |
| Notifications (§67) | priority-bundled, never read aloud with company present |

## Not in scope

Running agents on the phone, storing memory locally beyond a cache, or duplicating desktop
device control. The phone is an interface; the core is the system.

## Wiring

`WS /api/v1/realtime` for conversation, approvals and push, plus `GET /api/v1/devices` and
`POST /api/v1/devices/{id}/action` for device control. Background audio for wake-word
listening is the main platform-specific work.
