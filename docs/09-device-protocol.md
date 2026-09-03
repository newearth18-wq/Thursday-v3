# 9. Device Node Protocol (TNP/1)

Thursday Node Protocol — JSON over WebSocket, node → core (outbound only, so no inbound
firewall rules and no listening port on the user's machine).

## 9.1 Connection & authentication

```
node                                   core
 │  WS connect wss://core/device                     (TLS)
 │─────────────────────────────────────────────────►│
 │  HELLO {device_id, name, os, node_version,        │
 │         capabilities, telemetry, nonce,           │
 │         sig = Ed25519(privkey, nonce||device_id)} │
 │─────────────────────────────────────────────────►│  verify against devices.public_key
 │◄─────────────────────────────────────────────────│  WELCOME {session_id, server_time,
 │                                                  │           policy, heartbeat_s}
 │  HEARTBEAT {telemetry}    every 15 s             │
 │─────────────────────────────────────────────────►│
 │◄─────────────────────────────────────────────────│  ACTION {action_id, action, args,
 │                                                  │          timeout_s, permission_token}
 │  ACTION_RESULT {action_id, ok, data, error,      │
 │                 undo, duration_ms, verified}     │
 │─────────────────────────────────────────────────►│
 │  EVENT {kind, payload}   (unsolicited)           │
 │─────────────────────────────────────────────────►│
```

Enrollment is a one-time out-of-band pairing code that binds the node's freshly generated
Ed25519 keypair to a `devices` row. The private key never leaves the machine. A revoked
device is rejected at `HELLO` and its session is killed.

### 9.1.1 Pairing, as built (Sprint 36 · ADR 0029)

```
node                                   core                          owner
 │ generate keypair (first run, 0600)    │                              │
 │ POST /devices/pair/start ────────────►│  verify the request is        │
 │   {public_key, name, os, hostname,    │  signed by the key it offers  │
 │    nonce, issued_at, signature}       │  (proof of possession)        │
 │◄──────────── {pairing_code, device_id, expires_at}                    │
 │ display the code + key fingerprint ───┼─────────────────────────────► │
 │                                       │◄─ POST /devices/pair/complete │
 │                                       │     {code}  (proof of presence)
 │                                       │  register the public key,     │
 │                                       │  trust = LIMITED              │
 │ HELLO signed with its own key ───────►│  verified against that key    │
```

* The **code is not a credential**: five minutes, one enrolment, and what is stored is the
  public key. Guesses are bounded across all codes, not per code — the codes an attacker
  guesses do not exist, so a per-code counter never sees them.
* **A paired device is judged only by its key.** The shared enrolment token
  (ADR 0013) stays open for devices with no key on file and is closed permanently for every
  device that pairs. The node enforces the same rule and never retries with the token.
* **Revocation is sticky** and is checked before the token fallback. The credential record
  is kept; the device is removed from the hub rather than marked offline, because it
  re-pairs under a new identity.
* The registry is written to `<data_dir>/device_credentials.json`, 0600, public material
  only. Without persistence a core restart would lock out every paired node.

```bash
python -m apps.node --pair --name Office-PC     # prints the code and the key fingerprint
python -m apps.node --forget-pairing            # after the owner revokes it
```

## 9.2 Frames

`HELLO · WELCOME · HEARTBEAT · ACTION · ACTION_RESULT · EVENT · CANCEL · SHUTDOWN · ERROR`

Every frame: `{"v":1,"type":...,"id":uuid,"ts":iso8601,...}`. Actions are idempotent by
`action_id`; a reconnecting node replays unacknowledged results.

## 9.3 Action catalogue (Windows node, §19)

| Action | Args | Verify by |
|---|---|---|
| `open_app` | `name \| path`, `args` | process exists + window title matches |
| `close_app` | `name \| pid`, `force` | process gone |
| `open_file` | `path` | handler process running |
| `save_file` / `write_file` | `path`, `content`, `mode` | file exists, size/hash |
| `create_folder` | `path` | dir exists |
| `rename` / `move` / `copy` | `src`, `dst` | dst exists (and src gone for move) |
| `delete` | `path`, `to_recycle_bin` | path gone; undo = restore |
| `search_files` | `root`, `pattern`, `limit` | — |
| `list_dir` | `path` | — |
| `read_active_window` | — | — |
| `screenshot` | `monitor \| window`, `region` | image bytes returned |
| `run_shell` | `command`, `shell`, `timeout` | exit code + stdout |
| `process_status` | `name \| pid` | — |
| `system_info` | — | — |
| `set_volume` / `get_volume` | `level` | readback |
| `clipboard_get` / `clipboard_set` | `text` | readback |
| `notify` | `title`, `body`, `priority` | — |
| `lock` / `sleep` / `shutdown` | `delay_s` | L4 — always ASK |

## 9.4 Control tiers (§19) — GUI clicking is last

```
1 API integration          (best: deterministic, verifiable)
2 Application automation   (COM/AppleScript/DBus/CLI of the target app)
3 Browser automation       (CDP/Playwright for web apps)
4 OS API                   (Win32/UIA, Accessibility API, X11/Wayland)
5 GUI coordinate control   (last resort; risk=HIGH; requires screenshot verification)
```
The tool registry stores the tier; the Tool Router prefers the lowest number that can do
the job. Tier 5 is never chosen when a tier ≤4 route exists.

## 9.5 SEE → THINK → ACT → VERIFY (§20)

The node never reports success because a command was dispatched. Each action declares a
`verify` predicate, executed **on the node after the action**, and the result carries
`verified: true|false` plus the observed evidence (pid, window title, file hash, exit
code). Core treats `ok=true, verified=false` as **unverified**, not success — and says so.

## 9.6 Capability advertisement (§57)

`HELLO.capabilities` is a flat map used by the Device Router and Capability Registry:

```json
{"open_app": true, "run_shell": true, "screenshot": true, "camera": false,
 "microphone": true, "gpu": true, "excel": true, "browser": true, "notify": true}
```
An action for which the node advertised no capability is rejected at core, before dispatch.

## 9.7 Device resolution (§22)

| Utterance | Resolution |
|---|---|
| "เปิดในเครื่องนี้" | `origin_device_id` of the turn |
| "ส่งไปโน้ตบุ๊ก" | fuzzy match on device name/kind, must be online |
| "ทำต่อจากคอมเมื่อกี้" | `world_state.active_device_id` at the referenced task |
| "ปิดคอมที่บ้าน" | name/location match; L4 → ASK |

Ambiguity resolves to a question, never a guess (§22). Confidence < 0.7 ⇒ ask.
