# 0081. Subtitles nobody can read are not subtitles

Date: 2026-09-13

## Status

Accepted. Extends [0060](0060-an-edit-never-writes-over-its-input.md).

## Context

[§23](../../23-release-readiness.md) has carried this since the media sprint, filed as a
deliberate limit rather than a defect:

> **subtitle burn-in depends on the machine's fonts** — libass renders what fontconfig can
> find, so a system with no Thai font produces boxes. The quality gate cannot see that, and
> this document says so rather than letting the test suite's green imply otherwise.

Both halves were assumed. Neither had been looked at, on a product whose first language is
Thai.

Measured, by burning the same line twice on this machine and **looking at the frames** — once
normally, and once with fontconfig pointed at a directory holding a single Latin font, which
is the deployment §23 describes:

* with a Thai font, the picture reads `แมวของฉันชื่อมะลิ กินปลาทูเป็นอาหารโปรด`;
* without it, a row of empty boxes;
* and `check_output` answered **`ok=True, 2 checks passed` for both files**.

The gate is not at fault. It reads the finished file, where a box is pixels like any other.
Asking it to judge glyphs would mean rendering text twice and comparing images, which is a
large machine for a question something else has already answered:

```
fontselect: failed to find any fallback with glyph 0xE41
```

libass says it, on stderr, at the moment it happens. `_must_run` has returned that stderr
since the day it was written, and `burn_subtitles` discarded the return value. ffmpeg then
exits 0 having produced a perfectly valid video of nothing readable — which is exactly the
case [ADR 0060](0060-an-edit-never-writes-over-its-input.md) exists for: *a render is reported
from the finished file, never from an exit code.*

## Decision

**Read what the renderer said, and refuse.**

**Two lines, and only one of them is a failure.** libass reports `Glyph 0x… not found` when a
font lacks a character — that is how fallback *begins*, and the next font in the chain usually
has it. Refusing on that line would refuse most correct renders. Only
`failed to find any fallback with glyph 0x…` says it gave up and drew a box. Reverting the
pattern to the first line turns seven tests red, two of them pre-existing media tests, which
is the distinction being load-bearing rather than pedantic.

**Refuse rather than warn.** Subtitles nobody can read are not a lesser version of subtitles,
and a warning in a log is not seen by the person who will show the video at a school. The
output file is left where it was written — an edit only ever writes new files, so nothing was
overwritten and the caller can look at it.

**Name every undrawable character, not the first.** An owner who installs a font for the one
character in the message, renders again, and is told about the next one has been sent round a
loop. The message carries the script and the characters themselves (`thai: กขงฉช…`) because
that is what maps to a package name; `details["missing"]` carries `U+0E41` and friends for
anything reading it as data.

## Consequences

Two facts §23 had only asserted are now observed and tested: Thai subtitles **do** burn in
correctly on a machine with a Thai font (this container has `tlwg/Loma`), and the identical
input **is** unreadable without one. The second is a real test, not a description — it stands
fontconfig up against a Latin-only directory and asserts the refusal.

| Mutation | Red |
|---|---|
| Burn-in stops reading what libass told it | 1 |
| It refuses on the fallback line too | 7 |
| Only the first undrawable character is reported | 1 |

**What this does not do.** It catches a character no font can draw. It does not catch a font
that draws the character *badly* — wrong shaping, missing tone-mark positioning, a Thai glyph
rendered without its vowel above it. libass does not report those because from its side
nothing failed, and judging them needs a human or a renderer comparison. That remains a real
limit and §23 keeps saying so.

Nor does it help a machine with no fonts at all in some *other* script the owner never
subtitles in — the check fires on the text actually being rendered, which is the only text
whose fonts matter.
