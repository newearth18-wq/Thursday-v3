# 43. A limit the caller cannot choose, and a kill switch it cannot reach

Date: Sprint 53

## Status

Accepted. Completes the §128 half of the deployment spec; the pairing half was settled in
Sprint 36 and is untouched.

## Context

§128 names five surfaces to rate-limit: login, pairing, approval endpoints, public-facing
APIs and expensive model endpoints. `RATE_LIMITED` had been in §49's error vocabulary since
the beginning and no class raised it. `docs/23-release-readiness.md` listed the gap as "No
rate limiting on the HTTP surface. *To close before any exposure.*"

Two of the five need no work. There is no login — §23.2's single-owner position means no user
model and no credentials to guess. Pairing already limits itself where the interesting budget
lives: a start budget per caller and a guess budget counted across *all* codes, because the
codes an attacker guesses are the ones that do not exist, so a per-code counter never sees
them.

That leaves the HTTP surface, and the honest threat is narrower than "rate limiting" usually
implies. The API is not exposed to the internet, and the readiness document says so in the
same breath as saying it must not be. What this defends against is something on the machine or
the LAN — a runaway retry loop, a misconfigured script, a curious process — turning an
endpoint that costs a model call into an unbounded bill or an unbounded queue.

## Decision

**Key the bucket on the peer address, never on a header.** `X-Forwarded-For` is the obvious
choice behind the reverse proxy §127 recommends, and it is the trap: a header the caller
writes is a bucket the caller picks, and a limiter whose bucket the caller picks is not a
weaker limit, it is no limit. It is honoured only when the immediate peer is in a configured
`trusted_proxies` list, which is **empty by default**. A deployment behind a proxy therefore
starts with every request sharing one bucket — a visible degradation somebody notices and
fixes by naming their proxy, rather than a silent hole nobody notices at all.

**Four classes, not one number.** Reading the device list all day must not consume the budget
that answers a question, and the class that can reach a model is limited an order of magnitude
tighter than the rest. Expensive routes are matched by *prefix*, so a new route added under
one of them is limited by default — the safer direction for a list somebody will forget.

**The emergency stop is never limited.** §134's kill switch has to work when everything else
is refusing. A rate-limited kill switch is one an attacker holds shut by making requests, and
every second it is held is a second Thursday keeps acting. Health checks are exempt for a
duller reason: a monitor polling on a timer must not spend the budget that answers a person.

**A sliding window, not a fixed one.** A fixed window lets a caller spend the whole budget in
its last instant and the whole budget again in the first instant of the next — twice the limit
at exactly the moment somebody is trying to exceed it.

**Generous defaults, and no off switch.** 240 requests a minute by default, 30 for anything
that can reach a model. This exists to stop a runaway loop, not to ration the owner: a limit
tight enough to interrupt legitimate work is one somebody switches off, and then there is
none. The numbers are configurable and the limiter is not removable, the same position taken
for session lifetime in [0042](0042-a-device-key-that-expires-is-an-outage.md).

**An unconfigured class fails open.** Unusual for this codebase, and deliberate: a class with
no limit is a routing mistake, and answering it with 429 would take an endpoint offline over a
typo in a settings file. Fail-closed belongs where the question is "may this caller act"; this
one is "has this caller acted too often", and the safe answer to *not knowing* is different.

## Consequences

Every refusal is a §48 error body with `retry_after_s`, plus a `Retry-After` header — without
it a client backs off by guessing, and the common guess is to retry immediately, which keeps
the limit tripped.

Getting the middleware order right mattered more than expected. Registered last, the limiter
became the *outermost* middleware — Starlette builds its stack so the last registered runs
first — and a 429 came back with no `x-trace-id` header and a freshly minted trace id in its
body, an error nobody could correlate with the request that caused it. It is now registered
before the trace middleware and therefore runs inside it.

Bucket memory is capped and evicts least-recently-used. Eviction forgives what a bucket had
counted, which sounds like a hole and is the lesser of two: refusing new callers once the map
is full would let one attacker with many source addresses lock every other caller out, which
is the denial of service the limiter exists to prevent.

What this does **not** do: limit the device WebSocket (HTTP middleware never sees it, and a
node reconnecting after a session expiry must not meet a 429 it has no way to read), survive a
restart, or coordinate across processes. All three are consequences of the single-process,
single-owner deployment this is built for, and a multi-process deployment would need a shared
counter rather than a bigger number here.
