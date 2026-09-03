# 8. Permission Model

## 8.1 Levels (§36)

| L | Name | Examples |
|---|---|---|
| 0 | READ | read file, list dir, screenshot, system info, search memory |
| 1 | OPEN | open app/file/URL, focus window |
| 2 | MODIFY | write/rename/move file, edit note, create folder |
| 3 | EXTERNAL | send email/message, post to an API, purchase, publish |
| 4 | SYSTEM | install/uninstall, service control, registry, shutdown |
| 5 | ADMIN | elevation, security settings, credentials, account changes |

## 8.2 Decision function

```
decide(action, ctx) -> AUTO | ASK | BLOCK
```
Evaluated in this order; first match wins:

1. **Emergency lockdown active** (§69) → `BLOCK` everything but read + stop.
2. **Blocklist** (disable antivirus, disable audit, exfiltrate secrets, mass delete,
   modify Thursday's own permission tables) → `BLOCK`, unconditionally, no override path
   through conversation.
3. **Privacy zone** forbids the surface (camera/mic/cloud/memory off here/now) → `BLOCK`.
4. **Standing grant** matching `(action, resource_scope, device)` and unexpired → `AUTO`.
5. **Level ≥ 3**, or risk `HIGH`, or irreversible, or affects >N objects → `ASK`.
6. **Level ≤ 2** inside an allowed scope → `AUTO`.
7. Otherwise → `ASK`.

Rule 5's "irreversible" is computed, not declared: an action with no registered undo
operation (§40) is treated as irreversible.

## 8.3 Action policy table (§37) — defaults

| Action | Policy |
|---|---|
| open file / app / URL | AUTO |
| create file, create folder | AUTO |
| read active window, screenshot | AUTO (unless privacy zone) |
| move / rename file | AUTO if ≤10 objects and undoable, else ASK |
| delete file | ASK |
| run shell command | ASK (AUTO only for an allowlisted read-only set) |
| send email / message | ASK |
| calendar write | ASK |
| install software | ASK |
| system/service change | ASK (L4) |
| elevate to admin | ASK + re-auth (L5) |
| disable antivirus / security tooling | **BLOCK** |
| exfiltrate vault secret to a model or network | **BLOCK** |
| modify audit log | **BLOCK** |

Policies are data (`thursday/security/policy.py` seeds `permissions`), user-overridable
per action/scope — except the BLOCK set, which is code.

## 8.4 Approval UX (§38)

An approval request always shows: **action · agent · device · resource · risk · expected
outcome · what happens if you say no**. Answers: `Approve` / `Reject` / `Approve once` /
`Always allow (this scope)`. "Always allow" writes a scoped, expiring
`permission_grants` row — never a global one.

Approvals expire (default 5 min for interactive, 24 h for queued) and expired approvals
fail closed.

## 8.5 Agent permission derivation

`effective = task_permissions ∩ agent_ceiling ∩ device_capabilities ∩ user_grants`

Intersection only. A sub-agent can never hold a permission its parent lacked (§96),
and no agent holds admin standing (§96) — L5 requires a fresh, human, per-action approval.

## 8.6 Dry run (§72)

Any bulk or destructive operation must produce a `DryRunReport` first:

```
342 files will move
 14 files will rename
  0 files deleted
  2 conflicts (name collision) — listed
```
The approval is bound to that report's hash; if the filesystem changed underneath, the
approval is void and re-computed.
