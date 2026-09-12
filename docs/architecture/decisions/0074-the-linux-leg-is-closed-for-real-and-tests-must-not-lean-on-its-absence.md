# 74. The Linux leg is closed for real, and tests must not lean on its absence

Date: Sprint 105

## Status

Accepted. Closes the Linux third of the gap §23 named since ADR 0040: *"the OS keychain
adapters have never run against a real keychain."*

## Context

ADR 0040 built three adapters — macOS Keychain, Windows DPAPI, Linux Secret Service — behind
one port, with the container's own read: *"this container is headless Linux with no Secret
Service, no macOS and no Windows,"* so selection, availability detection, migration ordering
and the exact commands were tested, and the platform calls themselves were not. §23's own
remedy was *"one run on each of macOS, Windows and a Linux desktop."*

Two of those three need hardware and an OS this environment does not have and cannot get.
The third does not. A Linux Secret Service is a session D-Bus bus with a daemon registered on
`org.freedesktop.secrets` and an unlocked collection — infrastructure, not hardware — and a
headless container can have exactly that.

## Decision

**Install a real Secret Service and run the real adapter against it, unmocked.** Not
`secret-tool` shelled out from a throwaway script — the production `SecretServiceKeychain`
class, `KeychainVault`, `build_container(vault_backend="keychain")`, and `NodeIdentity`'s
file-to-keychain migration, exercised exactly as a deployment would reach them.

`gnome-keyring` plus `libsecret-tools` from the standard Ubuntu archive, a session bus from
`dbus-launch`, and `gnome-keyring-daemon --unlock` fed a newline on stdin — which both creates
and unlocks the login collection on first use, since there is nobody at a CI runner to type a
password and a freshly created collection with a known (empty) one is the honest equivalent.

**The password has to reach the daemon's own stdin, not the step's.** The first attempt piped
it to the outer shell invocation and separately redirected that invocation's stdin to
`/dev/null` for detachment — the second redirect wins, the daemon received nothing, and
`secret-tool` failed with *"Object does not exist at path .../collection/login"* because the
collection was never unlocked into existence. Piping into an inner `bash -c '...'` whose own
pipe is self-contained fixes it; the CI step carries a comment naming the failure mode so
nobody re-simplifies it back.

**A skip is silent unless something asserts otherwise.** The same discipline this project
already applies to ffmpeg and eSpeak NG in CI: a step asserts `detect()` actually returns an
available `SecretServiceKeychain` before the suite runs, so a broken install fails loudly
there instead of the keychain test file quietly reverting to skipped and nobody noticing from
a green run.

**Every test in the new file skips, not xfails, when no live Secret Service is reachable.**
A contributor's laptop without a session bus should see the rest of the suite pass, not a wall
of failures for infrastructure the file itself is asking for — the same posture the media
suites take toward a machine with no ffmpeg.

### What closing it found

Turning a live keychain on is not a neutral act on the rest of the suite. Every `NodeIdentity`
this project constructs uses one fixed account name, `"node-identity"`, for the device's key.
Four other test files — `test_node_identity_v36.py`, `test_rotation_v52.py`,
`test_tls_handover_node_v22.py`, `test_tls_rotation_live_v23.py`, plus two tests in
`test_keychain.py` itself — built a `NodeIdentity` with no `keychain=` argument, which reaches
`detect()`. On the machine these were written on, that always returned `NoKeychain`, so the
tests passed. They were not testing what they claimed to: they were testing *file storage*,
proven only by the accident that this container had no keychain to migrate into.

Running the old versions of those files against the real daemon this ADR adds turned two of
them red immediately — `key_path.stat()` on a file that migration had just deleted. The other
files' assertions happened to still hold by luck (a pin stored in JSON, not the keychain), but
the same coupling was there, one refactor away from the same failure. All of them, plus the
two `test_keychain.py` tests that relied on the *container* having no keychain to prove a
refusal, now pass `keychain=NoKeychain()` — or, for the refusal tests, patch `detect` —
explicitly. What they test is unchanged; what makes it true no longer depends on what happens
to be running on the machine.

## Consequences

**§23 goes from three untested platforms to two, honestly.** macOS Keychain and Windows DPAPI
remain exactly as unverified as before this — there is no macOS and no Windows here, and nothing
in this ADR pretends otherwise.

**CI now genuinely proves the Linux leg on every push**, not once, in a terminal, never to run
again. `test_keychain_live_v26.py` is the record of what was checked; the two-part CI step
(install + start, then assert) is what keeps that record honest run over run.

**A pattern worth naming for the next environment-gated test:** a fixture or bare construction
that reaches a real `detect()`, `discover()`, or similar is coupled to whatever the machine
running it happens to have, whether or not the test says so. The fix is the same each time —
pass the fake explicitly, or patch the ambient default — and the cost of not doing it is a
test that is right for the wrong reason until the day the environment changes under it.
