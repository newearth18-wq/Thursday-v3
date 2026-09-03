# 45. A node's report is an observation; the owner's correction is a decision

Date: Sprint 55 (ADDENDUM — Local AI Compute)

## Status

Accepted. Builds on [0044](0044-discovery-that-does-not-scan.md); the compute router reads
what this stores.

## Context

Sprint 54 taught nodes to report which models they hold. Persisting that is mostly dull —
three tables from §48–§50 and a write-through repository that already existed (ADR 0036).

One part is not dull. Part of what a node reports is a **guess**. No local runtime says what
a model is *for*, so discovery reads the name: `llava` means vision, `nomic-embed` means
embeddings. That is right most of the time, wrong some of the time, and unreadable for a
private build called `house-model-v3`.

A wrong guess is not cosmetic. Kind determines capability, capability determines routing, so a
vision model misfiled as a chat model is a model the compute router will never send an image
to — and the owner has no way to fix it. So corrections have to exist.

The moment they exist, a conflict does too: the node re-reports its inventory on every
reconnect, with the same guess it made last time.

## Decision

**Observations and corrections are stored in separate fields and merged on read.**

`observed` holds exactly what the node said, untouched. `kind_override` and
`enabled_override` hold what the owner said. `kind` is the override if there is one and the
observation otherwise. Re-discovery replaces the observation and never touches the override.

The alternative — one field, last writer wins — makes a correction last until the machine
next reboots. That is worse than not offering corrections at all: the owner watched it work,
so they have no reason to check it again, and the regression is silent and delayed. This is
the same rule §110 states for memory ("external content cannot redefine preference") applied
to compute, and it generalises: **anything a machine reports is evidence, and evidence does
not overwrite an instruction.**

Keeping the observation also keeps "what does the machine actually claim" answerable after
somebody has overridden half the registry, which is the question to ask when routing goes
strange.

**`enabled_override` is tri-state.** `None` means the owner never said; `False` means they
switched it off. Collapsing those to a boolean would make a default indistinguishable from a
decision, and the router treats them differently — a preference can be outvoted, an
instruction cannot.

**Model ids are derived, not allocated.** `uuid5(namespace, device|runtime|name)`, so a node
that reconnects lands on the row it had without a lookup, and two reports arriving together
cannot race each other into two rows. All three components are in the key: the same model on
two machines is two entries because routing chooses between them, and the same model on two
runtimes is two entries because they load, unload and fail independently.

**Disappearing is going offline, not going away.** A model that stops being reported, or a
machine that disconnects, sets `online = False` and keeps everything else. Ollama restarting
mid-scan is not the owner uninstalling a model, and deleting the row would take the
corrections with it — a GPU box that sleeps every night would lose its configuration every
night.

**Nothing is restored as online.** `online` is asserted by a node on *this* run. A registry
that loaded `online: true` from last week would hand the router a machine that has been
switched off since, and the failure would arrive at the point of use.

## Consequences

`GET /models` answers "which model exists on which machine" including machines that are
switched off, which is the form of the question an owner actually asks ("what can the GPU box
run?" is usually asked while it is asleep). It reports `kind`, `guessed_kind` and `corrected`
separately so the answer never hides that a guess was involved.

`model_endpoints` stores a `base_url` and no credential, for the reason §8 gives about device
credentials: a table row is exactly where a key gets read by something that should not have
it. An endpoint needing a secret carries a reference the SecretProvider resolves.

`model_runs` is separate from `model_spend`, which is not duplication. `model_spend` answers
"what did this cost" and has no column for where it ran; `model_runs` answers "where did it
run and how did it go", which is what the benchmark and routing-history sprints need.

What this does not do: benchmark anything. `tokens_per_second` is a column that discovery
leaves at zero, and the router must read zero as *unknown* rather than as slow — §25's
benchmark profile is a later sprint, and until it lands the registry knows what exists and
not how well it performs.
