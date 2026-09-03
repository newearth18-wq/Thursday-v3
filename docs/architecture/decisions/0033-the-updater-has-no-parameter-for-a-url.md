# ADR 0033 — The updater has no parameter for a URL

**Status:** accepted · **Date:** 2026-09-02

## Context

The specification states one rule about updates outright: *never execute an arbitrary update
URL supplied by a model* (§120). Until Sprint 48 it was true by absence — there was no
updater — and Sprint 46's test proved it that way.

Absence is not a design. An updater has to exist eventually, and it is the most dangerous
component in a system like this: everything else is bounded by the permission engine, and an
update *replaces the permission engine*. Whatever it installs runs with Thursday's full
privileges on the owner's machine.

## Decision

**The rule is kept by having nowhere to break it.** A rule enforced by a check is a rule
somebody can forget to call. `UpdateService.apply` takes a `Release` obtained from the
configured source and the bytes it hashes to. There is no overload, no keyword, and no
endpoint parameter that a URL arrives in — the API's `POST /updates/apply` takes no body at
all. The test for §120 is an inspection of the signatures, not an attempted exploit.

**Where updates come from is configuration.** The channel base URL and the release signing key
are `Settings`, fixed before the process starts. `UpdateService` has no setter for either. A
model, a document, an agent or an API caller cannot change where this deployment looks.

**A manifest cannot redirect the download.** This is the part that is easy to leave out.
Pinning where the *manifest* comes from and then trusting the URLs inside it hands the download
to whoever controls the manifest, and a correct signature over "fetch this from somewhere else"
is a correct signature. Every artifact must live under the configured base, checked with a
trailing separator so `https://updates.example.test.attacker.example/` does not pass for
`https://updates.example.test/`.

**Signature before anything else, over version + digest + URL together.** A checksum alone
proves only that the file arrived intact from whoever sent it. Signing the digest alone lets a
signature be lifted onto another release; signing the version alone lets new bytes ride an old
signature. A deployment with no configured key refuses everything — guessing that an
unconfigured key means "accept whatever arrives" is how an updater becomes the attack.

**Downgrades are refused unless asked for.** A signed old release stays correctly signed for
ever, so the version with the bug that was fixed is permanently available to anyone who can
serve a manifest. Rolling back is a real thing an owner wants, and it has to be said out loud.

**Applying is never automatic.** `install_component` was already on `NEVER_AUTOMATIC`
(ADR 0028) and stays there. Through the API it goes through the Permission Engine as
`system.update`: SYSTEM level, ASK_ALWAYS, and not something an override can turn into AUTO.

**A backup is taken first, and a failed backup stops the update.** Sprint 47 exists partly for
this moment: the update that goes wrong is the one you cannot undo.

**Plain HTTP channels are refused at construction.** The signature would still be checked, but
anyone on the path could serve an older signed release, and transport security is what stops a
downgrade attack.

## Consequences

- §120 is now a property of the code's shape rather than a rule in a document.
- This build can check for and verify an update and deliberately **cannot install one**: no
  installer is wired, and `apply` says so rather than reporting success. Replacing the running
  system is a platform concern, and a half-built installer is worse than none.
- The HTTPS adapter has not been exercised against a real server — this container has no
  network. What is tested is the local adapter and every refusal path; that limitation is
  stated rather than implied.
- **Cost we accepted:** an owner who wants to move to a different update channel edits
  configuration and restarts. That is the intended amount of friction for changing where the
  code that runs their machine comes from.

## Alternatives considered

- **Accept a URL and validate it.** Rejected: the validation is the thing that gets bypassed,
  refactored around, or called with the check disabled "just for this case".
- **Trust the manifest's URLs because the manifest is signed.** Rejected — see above. It moves
  the trust from configuration to content, which is the substitution every rule in §94 exists
  to prevent.
- **Auto-apply releases marked `critical`.** Tempting, and rejected: `critical` is a field in
  a document, so auto-applying on it means whoever writes the manifest decides when Thursday
  installs code without asking. The flag is surfaced to the owner instead.
- **Verify only a checksum.** Rejected: it authenticates the transfer, not the publisher.
