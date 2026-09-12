# 73. One owner, so one token — and loopback is not a credential

Date: Sprint 104

## Status

Accepted. Closes the sentence §23 has carried since Sprint 100: *"it is a bound on rate rather
than authentication — of which there is still none."*

## Context

The hole was measured rather than reasoned about. Against the code as it stood, an
unauthenticated `POST /api/v1/devices/{id}/actions` with `app.open`:

```
POST app.open  ->  200  {"ok": true, "verified": true, "evidence": {"pids": [1001], ...}}
```

Chrome opened on the owner's machine. No credential of any kind.

The device channel demands an Ed25519 signature from every node before it will carry a single
frame (ADR 0029). The HTTP API beside it ran the same thirty-action catalogue for anybody who
could reach the port. **The front door had a cryptographic lock and the window was open.**

The careful work above it made no difference to this. ADR 0069 decided what kind of answer a
phone may give; ADR 0070 decided what a phone may do to a machine. Both are disciplines about
*the owner's* surfaces, and neither means anything while the question "is this the owner?" has
no answer.

## Decision

### One owner, so one token

§23 is clear that single-owner, single-tenant is a design position rather than an oversight. A
login, sessions and per-user isolation would answer a question nobody asked. What was missing
is much smaller: **proof that the caller is the owner rather than anything else that can open a
socket.** One shared token, `Authorization: Bearer`, compared with `compare_digest`.

**Nothing generates it** — the same rule as the enrolment token (ADR 0066), asserted by the
same kind of test. A secret this code invents is a secret this code has to store, print or
transmit, and each of those is a place it leaks from.

### Not configured means loopback only

The honest default, and the reason this does not break the ordinary install. §23 already told
the owner not to expose the API beyond localhost; this turns that sentence into a control
instead of a hope. The owner at their own keyboard needs no ceremony, and a deployment that
wants to be reachable sets a token — the same act, said once, in the place that enforces it.

### And loopback is not a credential

A page in the owner's browser can be made to arrive on 127.0.0.1 by resolving its own hostname
there. **DNS rebinding** is the actual attack against a local API, and the browser treats the
result as same-origin: the peer is loopback and everything about the request looks local.
Everything except the `Host` header, which is the whole signal.

So loopback-only mode requires a loopback `Host` as well as a loopback peer. There is no
allowlist of extra hostnames: if you want to reach Thursday by a name, that is a deployment,
and a deployment has a token. One rule, stateable in a sentence, rather than a knob.

With a token configured the Host check is skipped, because it would then be wrong — the
deployment is deliberately on some other hostname, and a rebound page still cannot produce the
token.

### The WebSocket is not a way round it

`@app.middleware("http")` does not run for a WebSocket, and `/api/v1/realtime` takes
`type: "turn"` straight into the reasoning engine. An HTTP surface that demands the owner's
token while the channel that can *ask Thursday to do things* takes anyone is the same hole
moved sideways. This sprint nearly shipped it: every REST test was green before anybody opened
the socket.

The gateway calls the same `admit` the middleware calls — one function, two callers, no second
copy to drift. It lives in `thursday_core` rather than beside the middleware because a lower
layer must not import an upper one.

A browser cannot set a header on a WebSocket. The subprotocol list is the one thing it *can*
put in the handshake, so the token may arrive as `thursday.token.<token>`; it travels in
`Sec-WebSocket-Protocol`, exposed exactly as much as `Authorization` would be. A query string
would have put it in every access log on the way.

### Two exemptions, and each is an argument

`POST /devices/pair/start` and `GET /devices/tls-handover` answer without the owner's token,
because **a node is not the owner and cannot hold the owner's token.** Pairing proves possession
of the node's own key and yields a code a person must confirm, so possession alone enrols
nothing. The hand-over is read by a node that could not open the channel it would authenticate
on — that is the entire situation it exists for (ADR 0071) — and the document defends itself.

Exact paths, not prefixes. The rate limiter uses prefixes so that a new route is *limited* by
default; the safe direction here is the opposite one, so a new route is **authenticated** by
default and joining this list is a deliberate act.

### The kill switch is exempt from the rate limit and not from the token

Two different exemptions, and conflating them is the easy mistake. §134 exempts the emergency
stop from rate limiting so an attacker cannot hold it shut by making requests. That is not a
reason to let anybody press it: an unauthenticated kill switch is a denial-of-service tool with
a friendly name.

## Consequences

**A multi-machine deployment now requires a token.** This is a breaking change for anyone
running the API on 0.0.0.0 without one — which was, by §23's own words, already outside the
supported position. The refusal names nothing useful to the caller; the remedy goes to the log,
where the owner reads it.

**A proxy in front of a tokenless deployment fails at startup.** Loopback-only decides by the
immediate peer, and every request through a proxy on this machine arrives from 127.0.0.1 — so
the whole internet would look like the owner at the keyboard. That is worse than either setting
alone, so it is refused where somebody is looking rather than at the first request.
`require_api_token` exists for the opposite case: a deployment meant to be reachable, where
quietly falling back to loopback-only would look like a network fault for as long as anybody
cared to debug it.

**The phone carries a token now.** The connect screen collects it beside the address, because
they are one decision: a Thursday reachable from a phone is by definition reachable from off
its own machine. Optional rather than required, since the same screen is reached on desktop
when a sidecar has died, and that Thursday needs none.

**Two test-harness accommodations were removed rather than shipped.** `testclient` was briefly
in the loopback peer set and `testserver` would have had to join the loopback hosts — both are
a test double's idea of a name, living in a security check that ships. The fixtures were
changed to look like what they stand in for instead.

**What this is not.** It is not a user model, and it is not authorisation: the token says *the
owner*, and everything after it is the Permission Engine's business exactly as before. It is
one shared secret, so it cannot say *which* device asked — the device channel's per-node keys
still do that, and they are unchanged. And it is not a substitute for TLS: a token on a
plaintext network is a token anybody on that network can read (ADR 0041 pins the device
channel; the HTTP surface is the operator's to terminate).
