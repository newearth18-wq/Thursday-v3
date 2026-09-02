# ADR 0032 — A backup nobody has restored is a hope

**Status:** accepted · **Date:** 2026-09-02

## Context

Nothing backed up the state Thursday holds. Memories, the audit chain, the spend ledger, the
owner's policy overrides and the decision journal all lived only in the running process, and
Sprint 45 had already noted the consequence for one of them: with the ledger in memory, a
restart reset the daily total, so restarting was a way around the spending cap.

The failure mode of a backup feature is specific and worth naming before designing one. A
backup that is never restored is not a backup — it is a file that makes people feel safe. The
value is entirely in the restore path, which is exercised on exactly one day, under pressure,
by somebody who cannot debug it then.

## Decision

**Each component serialises itself.** `export_state` / `import_state` live on the memory
manager, task manager, audit log, cost meter, policy table and decision journal. The backup
module holds only the wiring. A backup module that knew how each component stored its state
would break quietly the first time one of them changed — and quietly is the whole problem.

**Restore replaces; it does not merge.** A merge of two divergent histories is neither, and
the owner asked for the one in the file.

**Restore is refused unless confirmed, and refused again unless the archive verifies.** No
code path reaches a destructive restore by default: an argument nobody passed cannot overwrite
somebody's memories. Through the API it goes through the Permission Engine like any other
destructive act — administration is exactly the kind of caller that would be handed a back
door, so it does not get one. The refusal shows what the archive contains, because "restore 6
components" is not a decision anybody can make.

**A failed verification restores nothing at all.** Half a restore leaves a system that is
neither the backup nor what it was, and nobody can tell which parts are which.

**Restored state is not re-judged.** Memories are not put back through `judge` and tasks are
not re-validated against the state machine. These decisions were already made and these states
were already reached legally; re-running the gates would let a policy change since the backup
silently discard things the owner still has, or refuse a task that is legitimately mid-flight.

The one exception is **policy overrides, which are reapplied through `override`**, because a
backup is a file and a file is external content (§94). Restoring the dict directly would let
an edited archive auto-approve an action the table says to always ask about — the exact bypass
`_may_override` exists to prevent.

**Audit entries keep their stored hashes.** Re-recording them would recompute `prev_hash` and
`hash`, and a chain recomputed on restore is a chain that verifies whatever it was handed.
Keeping the hashes means `verify_chain` still catches a backup somebody edited, even after the
archive's own checksum has been rewritten to match.

**The vault is not backed up.** Not redacted — excluded. A backup that could restore the
owner's API keys is a backup that hands them over when it is stolen, and redacted secrets
would restore as the redaction marker and quietly break every integration they belong to.
Secrets live in the OS keychain and the owner re-provides them. Everything else in the archive
goes through the redactor on the way out, because a backup is one more place data lands.

**Archives are written atomically and 0600.** A process killed mid-write leaves the previous
backup intact rather than a truncated one, because a truncated backup is worse than none: it
looks like a backup.

## Consequences

- The spending cap survives a restart, closing the gap Sprint 45 named.
- "Is my backup any good" is answerable on a quiet Tuesday: `verify` re-reads the archive and
  re-checks every component, and the API reports it per backup rather than trusting the
  manifest — a manifest that says a backup is fine is the part an editor fixes first.
- Restoring is deliberately awkward. That is the correct amount of friction for an operation
  that discards everything the system currently knows.
- **Cost we accepted:** the archive is a single JSON document held in memory while it is
  written, so a very large history will want streaming or a per-component file layout.
  `FORMAT_VERSION` exists so that change can be made without an older build half-reading it.
- **What this is not:** a database backup. On Postgres, `pg_dump` is the better tool and this
  does not pretend otherwise. What it captures is the state a fresh install cannot
  reconstruct.

## Alternatives considered

- **Snapshot the SQLite file.** Rejected: it backs up a deployment detail rather than the
  system's state, and says nothing on Postgres or about the in-memory services.
- **Sign the archive instead of checksumming it.** Rejected for now, and the docstring says so
  plainly rather than implying a guarantee that is not there: signing needs a key, and a key
  stored beside the backup signs whatever is next to it. The checksum catches corruption,
  truncation and casual editing; the audit chain catches the rest.
- **Merge on restore.** Rejected — see above.
- **Back up the vault too, encrypted.** Rejected: the passphrase would live next to the
  archive or in the owner's head, and the first is no protection while the second makes the
  backup unusable on the day it is needed.
