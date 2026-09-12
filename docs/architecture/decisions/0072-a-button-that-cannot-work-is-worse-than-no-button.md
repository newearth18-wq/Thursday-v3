# 72. A button that cannot work is worse than no button

Date: Sprint 103

## Status

Accepted. Closes §23's "Repair Thursday can currently repair nothing", and closes it with one
handler rather than the three that document asked for.

## Context

Sprint 66 built Check Thursday / Repair Thursday. The check was real, the security boundary was
real — `SelfRecovery.register` refuses a forbidden repair at *registration*, so a repair that
widens what Thursday may do cannot exist to be called — and the three handlers behind the
buttons were `lambda: None`.

The verification step (ADR 0012) is what kept that from reading as success. Because the outcome
was derived from re-running the health check rather than from the handler returning, pressing a
button answered *"ลองซ่อมแล้ว แต่ยังไม่กลับมาทำงาน"*, which was true. §23 named it and asked for
three real handlers.

Building them found that the number was wrong.

## Decision

### Two of the three could never have worked

| repair | why not |
|---|---|
| `reconnect_node` | A node **dials the core**. There is no address to dial back, and no way to start a process on somebody else's machine. The `devices` check is all-or-nothing — unhealthy only when *nothing* is connected — so there is not even a stale session to close. |
| `restart_worker` | The background worker is a separate process (`python -m apps.worker`) with its own container. Restarting it means starting a process the core does not own, which is the neighbourhood of "install a system component" — on the never-automatic list. |
| `switch_model` | **Can.** The router owns provider selection, in this process, and parking a failing provider changes what the next request reaches. |

The two that cannot are **not offered**, and the sentence saying what a person has to do is
shown in their place — in normal mode, unlike the technical detail, because that sentence is
the entire point of removing a control.

The apparent alternative was to leave the buttons and let the verification keep telling the
truth about them. It tells the truth once. After that the owner has learned that the buttons do
nothing, and there is no way to teach them that one of them does.

### Switching models is the breaker, reached by a different route

The circuit breaker already parks a provider after three consecutive failures (ADR 0028's
reasoning about a cooldown applies unchanged). It needs three because nothing but the provider
is talking. When the **owner** has read a health check and pressed Repair, that evidence is
already in — so Repair parks it now.

**It will not park the last provider that can still be chosen.** A Thursday whose only model is
failing might still answer; a Thursday with every provider parked raises `ProviderError` at
every request. A repair that leaves the system worse than the failure it was called to fix is
not a repair, so `park` refuses and the button reports that it cannot help.

### A repair is verified against what it restores, not against what broke

This is the part that generalises. Re-checking the component is the right question for a repair
that **heals** it, and the wrong question for one that **routes around** it. Switching models
leaves the failing provider exactly as failing, so asking whether `model:ollama` is healthy
would report every successful switch as a failure.

So a repair may register its own way of being observed, beside the repair itself. The component
check stays the default. Either way the answer comes from an observation and never from the
handler returning — ADR 0012 is unchanged; what changed is knowing *which* observation answers
the owner's question, which was "does Thursday work?" and never "is that provider back?".

### A button is offered only where a repair is wired

`is_self_repairable` says a repair is *permitted*. It says nothing about whether anything would
happen. `check()` asked it alone, so a permitted repair nobody had implemented was offered as a
button that, when pressed, replied that there is no automatic repair for this part — the exact
lesson this module's own docstring says it exists to avoid, taught by the module.

`SelfRecovery.can` asks both questions. It was reachable in practice only because the `queue`
health check is hard-coded `ok: True` and so never produced a problem to offer a button beside;
one honest health check away from being a live defect.

## Consequences

**One repair, and the count is the finding.** Wiring is deliberate and per-repair
(`register_repairs`), never a loop over the permitted list — looping would recreate exactly what
was removed: a repair that exists because it is allowed rather than because it works.

**A future repair earns its button by working.** Adding one to `SELF_REPAIRS` does not produce a
button; wiring it to something that does something does.

**An existing test changed its setup, and the property it asserts did not.** `test_a_repair_
that_ran_but_fixed_nothing_does_not_report_success` leaned on the container wiring
`reconnect_node` to a placeholder. That wiring is gone, so the test registers a do-nothing
repair on purpose instead. It was testing the right thing with the wrong prop — depending on a
bug to supply the thing it was asserting about.

**What this does not do.** It does not add a screen: Check and Repair are API endpoints and
there is no desktop UI for either. It does not make the `queue` health check real, so
`restart_worker`'s removal is invisible today and will stay correct when that check stops being
hard-coded.
