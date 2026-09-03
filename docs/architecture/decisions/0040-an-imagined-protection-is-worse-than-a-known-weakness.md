# ADR 0040 — An imagined protection is worse than a known weakness

**Status:** accepted · **Date:** 2026-09-03

## Context

Two things in Thursday were on disk in a 0600 file and are worth more than the files around
them: the secrets the owner entrusted it with, and the private key that *is* a device's
identity. A 0600 file is not nothing — it stops another user on the same machine — but it
stops nothing once a laptop is taken, an unencrypted backup is restored, or a process running
as the owner goes looking. `docs/23-release-readiness.md` had named this since Sprint 36.

Auditing it turned up something worse than the known gap. `Settings.vault_backend` offered
`"keychain"`, and choosing it returned `ChainVault(EnvVault())` — the environment vault, with
a comment saying the real adapter would land later and no word to the operator. A deployment
that configured the OS keychain got the environment and believed otherwise.

That is the shape of the whole decision below. **A known weakness gets compensated for; an
imagined strength does not.** Somebody who knows their secrets are in environment variables
locks down the process environment. Somebody who thinks they are in the Keychain does nothing,
because there is nothing left to do.

## Decision

**One port, three real adapters, no new dependencies.** macOS Keychain through the `security`
CLI, Windows DPAPI through `ctypes`, Linux Secret Service through `secret-tool`. A keychain
library that has to be installed is a keychain that is absent on the machine which skipped the
extra — and this is the component that must not be absent by accident.

**Availability is asked, never assumed.** Every adapter answers `available` by doing something
real: looking for the binary, checking for a session bus, importing crypt32. A Mac without the
CLI, a headless Linux box and a Windows container without DPAPI all look like their platform
and none of them can store a secret.

**`vault_backend="keychain"` fails closed.** If no keychain is available the container refuses
to build, and the message names both ways out: install a keyring, or set `vault_backend='env'`
and store secrets in the environment *deliberately*. This is the same posture
`DeviceAuthenticator` already takes for a missing device token, and for the same reason:
guessing that a misconfiguration meant "do the weaker thing" is how the weaker thing becomes
production.

The chain in front of the environment stays, but its purpose is narrowed to what it was always
for — reading secrets that have not been moved yet. Migration, not a fallback for a missing
keychain.

**`NoKeychain` refuses every operation.** It is not a null object that quietly does nothing.
Choosing to accept file storage is a decision for whoever configures the deployment, made once
and visibly, not one this module makes silently on every write.

**The node's key migrates in a safe order: write, read back, then delete.** A delete that
happened first would lose the node's identity to a keychain write that failed — and a device
that loses its key must be re-paired by a person standing at it. If the read-back does not
match, the file is left exactly where it was and the migration says why.

**A node whose keychain exists but is locked refuses rather than falling back to a file.**
Silently downgrading its own key storage would leave the owner believing the keychain protects
an identity it never held. `NodeIdentity.storage` reports where the key actually is, so nobody
has to infer it.

**The file fallback stays, named as a fallback.** It is honest about what it buys — another
user on the same machine, and nothing after that.

## Consequences

- A deployment can no longer believe it has keychain protection that it does not have.
- The Windows adapter is a different shape from the other two, and the docstring says so
  rather than papering over it: DPAPI is an encrypt/decrypt pair bound to the user account,
  not a store, so the protected blob still lives in a file. What that buys is real — the file
  alone is useless on another machine or to another user — and it is not the same guarantee as
  Keychain or Secret Service.
- On macOS the secret passes through this process's own argv, because `security
  add-generic-password` has no stdin form. That is a narrow but real exposure, noted in the
  code, and the reason the Linux adapter deliberately uses stdin instead.
- **Verification, stated rather than implied:** this container is headless Linux with no
  Secret Service, no macOS and no Windows. **None of the three platform adapters has ever run
  against a real keychain.** What is tested is selection, availability detection, the refusal
  to downgrade, migration ordering, and the exact commands each adapter would run. The
  adapters need a run on each real platform before anyone should rely on them.

## Alternatives considered

- **Depend on `keyring`.** Rejected: one dependency for three platforms is tempting, but it
  pulls a stack of transitive packages into a security-critical path, and the failure mode of
  a missing optional dependency is the silent downgrade this ADR exists to eliminate.
- **Fall back to the file automatically when the keychain is missing.** Rejected — that is
  exactly what was happening, and it is what made the setting a lie.
- **Refuse to run at all without a keychain.** Rejected as too strict for a personal assistant
  on a machine that may genuinely have none. The choice is offered; what is removed is the
  ability to make it accidentally.
- **Keep the key in both the keychain and the file during migration.** Rejected: a key
  protected in two places is protected by the weaker one.
