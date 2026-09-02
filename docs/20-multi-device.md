# 20. Multi-device (V8)

    one Thursday · many machines · the core owns the task, the device is a worker

## The sentence that names no machine

```
(on the phone)  "Thursday คอมที่บ้านเปิดอยู่ไหม"     → Home-PC is online
(on the phone)  "เปิด Chrome ให้หน่อย"                → Chrome opens on Home-PC
```

The second sentence carries no device. Before V8 it opened Chrome on the phone — a command
that *succeeded* on the wrong machine and was reported as success, which is worse than
failing, because failing is visible.

A device that gets named becomes the subject of the conversation
([ADR 0023](architecture/decisions/0023-the-conversation-knows-which-machine.md)):

| Property | Why |
|---|---|
| Expires after 3 minutes | A device named twenty minutes ago is not what "it" means now |
| Scoped to one conversation | A chat on the laptop must not steer one on the phone |
| An explicit word always wins | Naming another machine moves it; "เครื่องนี้" clears it |
| **The reply says which machine** | Acting somewhere the owner did not name, silently, is the failure being prevented |

The last row is the one that makes this safe rather than merely convenient. The device is
named exactly when it was not obvious — naming it on every reply is noise, and noise is what
stops people reading the one reply where it mattered.

`ReasoningEngine._anchor` used to fill in `target_device = "this"` whenever an utterance
named no device. "No device named" and "the owner said *this machine*" are different facts
that the router treats differently, and manufacturing the second from the first destroyed
the distinction before the router could use it. It no longer does that.

## Resolution order

`DeviceRouter.resolve`, strongest first:

| Signal | Confidence |
|---|---|
| Exact name match | 1.00 |
| Unique name prefix | 0.90 |
| The only device of that kind, or at that location | 0.82–0.85 |
| **The device this conversation is about** | 0.85 · announced |
| The device the owner is speaking from | 0.85–0.95 |
| Closest fuzzy name match | ≤ 0.85 |

Below `CONFIDENCE_FLOOR`, Thursday asks. An explicit location narrows and never widens: "the
PC at home" must not resolve to the office PC because no home PC is online.

## Where the answer comes out

`DeviceRouter.follow_me` asks a different question from `resolve` — where to *speak*, not
where to act. The owner can ask their phone to do something on the office PC and still want
the answer on the phone.

Presence is inferred from the last thing the owner actually did, because that is the only
evidence there is. It is a heuristic, and it is wrong sometimes, which is why it only ever
chooses where to speak and never what to do.

## A command that crosses machines

Making cross-device commands easy is exactly why they are gated
([ADR 0024](architecture/decisions/0024-a-command-that-crosses-machines.md)). An instruction
now carries its origin — `ToolCall.origin_device_id` → `ActionRequest` → `DeviceAction` —
stamped once from the turn that started the work. An agent cannot choose its own origin.

**Trust levels** answer one question: may an instruction *from* this machine reach another?

| | |
|---|---|
| `UNTRUSTED` | Receives nothing. Quarantine works in both directions or it is not quarantine |
| `LIMITED` | May be commanded, may not command. **The default at enrolment** |
| `TRUSTED` | May drive other devices |
| `PRIMARY` | The owner's own machine |

Never read from a node's own HELLO: a device asserting its own trust level is a device
granting itself permission.

**Two components, two questions.** `RemoteCommandGate` can only refuse, on facts no approval
would fix — unrecognised origin, untrusted origin, untrusted target, unencrypted link.
`PermissionEngine.decide` rule 4b decides whether an allowed remote action still needs the
owner's word, and sits *before* the standing-grant check: "I approved deleting files at my
PC" is not "anything holding my phone may delete files on my PC".

What counts as consequential is **derived from the level each action declares**, not from a
list. A remote action at or above `MODIFY` is confirmed. The first attempt was a hand-written
list and was wrong within the hour — it named `file.delete` and `file.move` and missed
`file.copy`, `file.rename`, `file.create`, `clipboard.write` and `app.close`. A short list
survives for things *below* MODIFY that still deserve a question, like `system.process.start`.

Reads are deliberately never confirmed. "Is my home PC on?" raising a prompt every time
would be caution in appearance only.

```
GET  /api/v1/devices                    every device, with trust, presence and capabilities
POST /api/v1/devices/{id}/trust         ?level=2 — the owner's decision, never the node's
GET  /api/v1/devices/output/follow-me   where an answer would be spoken right now
POST /api/v1/devices/{id}/actions       one action, gated like any other
```

## Handoff

```
(at the PC)     "Thursday วิเคราะห์ไฟล์นี้"
(on the phone)  "ผลเมื่อกี้เป็นยังไง"     → the task, its outcome, and which machine ran it
                "ทำต่อจากเครื่องเมื่อกี้"   → and the next command goes there
```

The lookup is deliberately not scoped to the conversation. A new device means a new session,
and a task scoped to the session that started it would be invisible the moment the owner
picked up a different device — which would make the core's ownership of tasks pointless.

The answer always names the machine, because the owner is asking from somewhere else and "it
worked" without "on the PC" is ambiguous in exactly the situation this exists for.

## Not built yet

Per-device Ed25519 keys — signatures are verified today but on one shared enrolment token,
which authenticates *a* node rather than *this* node (ADR 0013). Until that lands, trust
levels rest on the same shared secret as everything else in the device protocol.

Nothing here has run over a real network. Every device in the tests is a
`LoopbackDeviceSession`, and TLS detection reads the connection's scheme, which has been
exercised against a fake and not against a socket. The Android node does not exist; "phone"
in this document is a `kind`, not an implementation.
