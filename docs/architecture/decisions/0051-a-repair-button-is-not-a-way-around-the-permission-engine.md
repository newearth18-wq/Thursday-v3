# 51. A repair button is not a way around the Permission Engine

Date: Sprint 66 (EASY INSTALL — Check Thursday / Repair Thursday)

## Status

Accepted. Applies [0012](0012-verification-before-completion.md) to repairs and Sprint 65's allowlist rule to
component names. Constrained by §59 (`SelfRecovery`), which this deliberately does not extend.

## Context

The easy-install requirement asks for two buttons:

> Settings → Check Thursday  →  "Everything OK" / "Local AI ไม่ตอบสนอง — Repair"
>
> Button → Repair Thursday: restart services · repair configuration · reconnect local AI
> · repair database · re-register Node

and one rule over both: *"ห้ามแก้ไข security-sensitive state โดยไม่มี confirmation."*

The obvious reading of that list is a new subsystem — a repair engine that knows how to restart
services and re-register nodes. That reading is wrong twice over. Thursday already has
`Container.health()`, which is the only thing that knows whether it can work, and `SelfRecovery`
(§59, V10), which already draws the security boundary this rule is about: its allowlist refuses
a forbidden repair at *registration*, so a repair that changes what Thursday is permitted to do
cannot exist to be called.

The danger in a Repair button is specific and it is not carelessness. The repairs a person
reaches for in a crisis — re-pair the device, reset the policy, rotate the credential, grant it
access just this once — are exactly the ones on the never-automatic list. A button offering
them, labelled "fix", is a way around the Permission Engine with a friendly name on it. And the
route by which one appears is not a decision to add it; it is a row in a translation table where
somebody wrote `rotate_credential` next to a credential problem because it was the obvious fix.

## Decision

**Check and Repair are a translation layer, not a mechanism.** `checkup.check()` reads
`Container.health()` and says what it means; `checkup.repair()` calls `SelfRecovery.repair()`
and nothing else. Two health checks would be two things to keep in agreement, and the one
somebody forgets is the one with the security boundary in it.

**A Repair button appears only where `SelfRecovery` would accept the action.** The offer is
gated on `is_self_repairable()` — the same predicate `SelfRecovery.register` uses — so the
button and the boundary cannot disagree, and a test walks every row of the table asserting it.
The requirement asks for confirmation before security-sensitive state changes; what is shipped
is stronger, because there is no automatic path to that state to confirm.

**A repair reports what the machine shows, not what the handler returned.** This is
[0012](0012-verification-before-completion.md), and it is the reason the decision is worth recording. The
container wires `reconnect_node` and `switch_model` to placeholders that do nothing at all. The
first version of `repair()` ran one, saw no exception, and answered "ซ่อมเรียบร้อย" about a
machine in exactly the state it was in. `ok` now derives from re-running the component's health
check afterwards, and where nothing reports on that component the answer is
"ตรวจสอบผลไม่ได้" — §194's rule that nothing is marked success without verification, applied
to the subsystem whose entire job is fixing things.

**Component names are translated from a declared table, and so is where a model runs.** An
unrecognised component becomes "ส่วนประกอบภายใน" rather than leaking its internal name
(Sprint 65). Local and cloud runtimes are declared in both directions: the first version asked
whether `"cloud"` appeared in the component name, and no provider is called that — they are
`rule-based`, `ollama:…`, `anthropic:…` — so every model failure, a cloud outage included, told
the owner the AI on their own machine had stopped. The requirement's own example depends on
that distinction being right, and a wrong answer there is worse than no answer.

**Product names appear in exactly one place, and only where somebody chose them.**
`missing_services` says "ต้องเปิด Redis ก่อน" rather than surfacing a connection error
(Sprint 62). That is not a hole in the plain-language rule: on the desktop edition the list is
empty by construction — SQLite and an in-process cache — and a test asserts it. It is non-empty
only where somebody set a DSN by hand, and that person is the reader who needs the name.

## Consequences

Three of eleven components offer a repair. That is the honest count, and it is the point: a
screen of eleven buttons of which eight do nothing teaches the owner that the buttons do
nothing, including the three that work.

`POST /repair` takes an action name from the client. This is safe for the same reason
`/updates/apply` takes no URL is safe (ADR 0033), but by the opposite construction: the action
is not trusted, it is *checked*, and `change_permission` posted there is declined in identical
words whether it came from the owner, a model, or a page that persuaded a browser to post it.

The verification step makes the placeholder handlers visible. `POST /repair` for `devices` now
answers "ลองซ่อมแล้ว แต่ยังไม่กลับมาทำงาน" — which is true, and was true before, and was
previously reported as success. Wiring real handlers is future work that this makes legible
instead of hiding.

Adding a component to `health()` without adding a row here degrades to "ส่วนประกอบภายใน" —
safe, useless, and silent. Two tests close that: one asserts every component `health()` emits is
translated, the other that no row translates something `health()` never reports.
