# 50. Setup ends when the machine proves it, not when the form does

Date: Sprints 63–64 (EASY INSTALL — recommendation and first run)

## Status

Accepted. Builds on [0049](0049-the-shipped-configuration-is-the-product.md), which removed
the reasons an installer would have had to install anything.

## Context

The easy-install requirement describes a first run of six screens and sets a budget:
*"ไม่เกินประมาณ 5–7 user decisions"*. It also says something sharper, almost in passing:

> Setup is not considered complete until a real task succeeds.

Those two constraints pull in opposite directions from the usual design. The budget says ask
less; the completion rule says prove more. Both are satisfied by moving work from the owner to
the machine — detect what is detectable, and then make the machine demonstrate that the
detection was right.

## Decision

**Anything a machine can answer is never asked.** Sprint 63's `recommend()` reads RAM, VRAM
and free disk and proposes a configuration; the AI screen presents that proposal rather than a
question about VRAM. The six screens that remain are the ones a person is uniquely qualified
for: what to call it, which language, which voice, what it may do, how private, and one command
to try. A test asserts the count and that no screen name is something a machine could have
determined.

**Model classes, not model names.** A class is described by what it needs —
`min_ram_bytes`, `min_vram_bytes`, `download_bytes` — because names change every release and
differ per runtime, while a 7B-class model's appetite does not. The registry maps a class to
whatever is installed (ADR 0045), so the recommendation layer never learns that `llama3.1:8b`
and `qwen2.5:7b` are the same decision, and the owner never sees either.

**PRIVATE may not become cloud.** Every other preset falls back to a cloud model when hardware
disappoints. This one does not, and the difference is not a preference: an owner who chose
PRIVATE and got cloud inference has had a privacy decision silently reversed, and *the answers
still arrive*, so nothing looks wrong. On a weak machine PRIVATE takes the smallest local model
and says it will be slow; when nothing local can run at all it says so. An honest refusal beats
an answer from somewhere the owner excluded.

**`COMPLETE` is unreachable by answering questions.** The wizard's last screen leads to
`VERIFYING`, and only a `DeviceActionResult` that is both `ok` **and** `verified` moves it on.
`ok` means the node did not raise; `verified` means somebody looked and Notepad was open. This
is ADR 0012's rule applied to the install, and it is worth the extra state because the failure
it prevents is specific and nasty: a wizard that congratulates itself at the end of its own
form has told the owner their assistant works, on no evidence at all. They close the window
believing it, and discover otherwise at the moment they first needed it.

**`/setup/verify` has no parameter for success.** It runs the command and judges the result;
there is no field a client could post to assert completion. Same shape as the updater having
no parameter for a URL (ADR 0033) — a completion flag a client can set is a completion flag a
client will set, eventually, from a retry handler at three in the morning.

**Completion is never restored; answers are.** A first run interrupted at step four resumes at
step four. But an install verified on a machine that has since had its device node removed is
not still verified, so `restore` brings back `VERIFYING`. That costs one command and is the
only answer that is true on every restart.

## Consequences

The requirement's target flow works: detect, propose, six questions, one real command, done —
with `GET /setup` renderable at every point and every message in the owner's language, no step
numbers to count and no jargon (a parametrised test checks each one against the requirement's
own forbidden list: Docker, Postgres, Redis, ports, tokens).

A failed test command leaves setup at `VERIFYING` rather than failing it. Retrying is the
expected path, and "no device is connected yet, so there is nothing to try the command on" is
what the owner sees when they reach the last screen before installing a node — actionable,
where a stack trace would not be.

The wizard has no route to the Permission Engine, asserted by a source check. Step four records
what the owner *chose to allow*; §95 keeps the engine as the only thing that authorises, and a
setup answer is an input to policy rather than a substitute for it.

What this does not do: nothing here is an installer, and nothing here has run on Windows. The
recommendation is arithmetic over a hardware probe that has never seen a GPU, and the download
sizes are the classes' declared figures rather than measurements of real model files. The
wizard drives an API; there is no UI, and the Tauri desktop app does not call it yet.
