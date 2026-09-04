# 54. A mood is derived, and it is Thursday's own

Date: Sprint 80 (FULL-SCREEN HUD & AVATAR)

## Status

Accepted. Applies [0012](0012-verification-before-completion.md)'s rule to a feeling, and sits
under the interface work in Sprints 81–82.

## Context

The request was for an interface that moves, shows feeling, and says what Thursday is doing —
a HUD like the reference images, and an avatar that walks around when the owner is elsewhere.
Two of those three were already answerable from real state. The third, feeling, was not: there
was no source for it anywhere in Thursday.

The tempting implementation is a `mood` field somewhere near the top of the response, set by
whichever code path last did something interesting. It is easy, it demos well, and it fails
the same way `verified` failed before ADR 0012: the code that sets it is not the code that
knows. A mood that can be assigned is a mood that reads CALM while the audit chain is broken,
because nothing in the "chain broken" path remembered to set it.

There is a second hazard specific to this one. §55 of the biometric requirement forbids
inferring emotion — along with race, religion, health, gender, politics and personality —
from anybody. A system with a face on it and a socket full of camera frames is about one
plausible refactor away from "Thursday looks concerned because *you* look tired". That is a
different product, and not one anybody asked for.

## Decision

**A mood is derived, never set.** `thursday_core.expression.express()` is the only way to
produce an `Expression`. There is no `set_mood`, no `mood=` parameter anywhere in the module,
and a test walks the module's AST to assert that no function takes one. `Expression` is frozen,
so it cannot be edited after the fact either.

**Its two off-snapshot inputs are required arguments.** `unhealthy` and `lockdown` have no
defaults. A default of zero and a default of false are how a calm face ends up on a machine
whose database died, and this project has now shipped that class of bug four times. A required
argument makes a new caller answer the question.

**It is Thursday's own state, and the guarantee is structural.** `expression.py` imports
nothing from `thursday_security`, `thursday_vision`, `thursday_voice` or `thursday_devices`,
so there is no path from a person to a mood — not a face, not a voice, not a template. The
`Turn` dataclass has five fields and all five are about Thursday's own output. A test asserts
the import list and the field list, so reaching for a camera later fails loudly rather than
quietly becoming a feature.

**Priority, not a blend.** The nine moods are an ordered table and the first match wins.
Averaging would let three cheerful signals wash out one failure — a stop, a failure or a
pending approval must never be softened by work that went well. Adding a mood means choosing
its rank; there is no scoring function to tune.

**It fades.** `WorldStateProjector.on_agent` used to leave every agent it had ever seen in
`running_agents`, marked "completed" or "failed", forever. That made the field's name untrue,
and a mood built on it would have been a permanent apology. Agents now leave the dict when
they stop and the *time* they stopped is recorded instead, which is what lets a failure stop
colouring the face after `FRESH`.

**One derivation, two exits.** The socket pushes it on change and the endpoint answers with
it; both call `express()`. A cheap mood on the socket and an honest one on the endpoint would
be two sources of truth for one feeling, and they would disagree exactly when it mattered.
Health is the one input needing an `await`, so the socket re-reads it on a timer rather than
per event — a component that has died emits nothing, and a purely event-driven refresh would
leave a calm face on a broken machine until the owner happened to type something.

## Consequences

The HUD and the avatar cannot disagree about how Thursday feels, because neither computes it.
A new mood costs a rank, a sentence and a motion floor, all declared in one file.

Something is given up: the interface cannot be more expressive than Thursday's telemetry is
informative. There is no "curious", no "amused", no reaction to what the owner said — only
what Thursday is actually doing and how that is going. That is the intended trade. An
expressive face driven by nothing is a lie told sixty times a second.

Sprint 80 also fixed what the working strip rendered. It printed the agent's class name in a
monospace font — "ResearchAgent", "SupervisorAgent" — which is the exact example Sprint 65
gives of what a normal user must never be shown. The allowlisted phrase was already in the
same event payload; only the other field needed reading.
