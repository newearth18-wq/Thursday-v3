# 0079. What was blocked was the weights, not the database

Date: 2026-09-13

## Status

Accepted. Finishes [0078](0078-a-vector-that-cannot-be-compared-is-not-a-vector-that-is-unrelated.md).
Follows the live-dependency discipline of [0074](0074-the-linux-leg-is-closed-for-real-and-tests-must-not-lean-on-its-absence.md).

## Context

ADR 0078 fixed a defect and shipped with a caveat:

> pgvector is not installed in this container and there is no Postgres to run against, so the
> claim that Postgres refuses a wrong-width insert is read from the type shim and pgvector's
> documented behaviour, **not observed**. Closing that last step needs a Postgres, the same
> way the keychain and local-AI gaps need hardware.

The caveat was honest about what had been observed and wrong about why. Asked how the
remaining gaps could be closed, the first move was to check what this environment actually
allows rather than to repeat the assumption:

```
pgvector (PyPI)              : pgvector-0.5.0-py3-none-any.whl
postgresql (apt)             : Candidate: 16+257build1.1
postgresql-16-pgvector (apt) : Candidate: 0.6.0-1
fonts-thai-tlwg (apt)        : Candidate: 1:0.7.3-1
llama-cpp-python (PyPI)      : 0.3.35
piper-tts (PyPI)             : 1.8.0
huggingface.co:443           : 403, policy denial
```

The line is not *hardware*. It is **runtimes are installable; model weights are not
downloadable.** §23 had filed Postgres under the same heading as the camera and the macOS
keychain, and they are not the same kind of thing at all.

Installing it took minutes, and the observation ADR 0078 could not make came out immediately:

```
ERROR:  expected 768 dimensions, not 256
```

and then, through the real application against the real server:

| Shipped setting | Real PostgreSQL 16 + pgvector 0.6.0 |
|---|---|
| `embedding_dimensions: 768` (the fix) | `WRITE: ok` — 768 dims in `memories`, Thai content intact |
| `embedding_dimensions: 256` (what shipped) | `DataError: expected 768 dimensions, not 256` |

All 35 tables migrated clean on Postgres, `alembic check` found no drift, and all three
`vector` columns came back as `vector(768)`. None of that had ever been run before: every
test in this project's history had executed against SQLite, where `Vector` is a `Text()`
column that accepts any width and refuses nothing.

## Decision

**A real PostgreSQL with pgvector is a CI dependency, like ffmpeg, eSpeak NG and
gnome-keyring.** The suite still needs no database — every test that wants the server skips
without one, which is right on a laptop. What CI adds is the one environment where SQLite's
permissiveness stops standing in for a real column type, and it **asserts the server is
really there** before the suite runs, so a skip means the service broke rather than
disappearing into a green run. That is ADR 0074's rule applied to a second dependency.

**Three states, not two.** `postgres_live.detect()` distinguishes *no server*, *a server
without the extension*, and *a database nobody migrated*. The third produced five
missing-relation errors that read as a broken suite rather than an unprepared one.

**One test uses the shipped configuration and names no width.** Every other test here passes
an explicit `embedding_dimensions`, and reverting `settings.yaml` to 256 left all of them
green — the mutation did not bite, because none of them exercised what an owner actually
gets. The test that asks "can the configuration this project ships write a memory to the
database §2 names" is the one that would have caught the original defect, and it is the one
the mutation turns red.

## Consequences

Checked by reverting:

| Mutation | Red |
|---|---|
| `settings.yaml` goes back to 256, the pre-0078 default | 1 — the shipped-configuration test |
| `EMBEDDING_DIMENSIONS` disagrees with the migrated column | 2 |
| The vector store stops refusing a wrong-width write | 1 |

**The general lesson is about the audit, not the database.** Three sprints running found a
capability that existed with no caller, and ADR 0078 found them by scanning for the shape
rather than guessing. This one is the same move aimed at a different target: a documented
limitation is a claim like any other, and *"needs hardware"* deserves the same check as
*"this is wired up"*. Two lines of `apt-cache policy` would have retired it at any point in
the last several sprints.

What remains genuinely blocked is narrower and can now be stated exactly: **model weights.**
`huggingface.co` returns 403 by policy, so a Piper voice file, a GGUF for llama.cpp, and an
Ollama model cannot be fetched — while `piper-tts` and `llama-cpp-python` themselves install
from PyPI without trouble. The local-AI and neural-voice gaps are a *file transfer* problem,
not a hardware one. The camera, the microphone, macOS Keychain, Windows DPAPI and a second
machine on a LAN remain real hardware gaps, and `fonts-thai-tlwg` is in apt, which makes the
Thai subtitle gap the next one that is closeable here rather than described.
