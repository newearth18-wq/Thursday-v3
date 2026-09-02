# ADR 0015 — The node dials out; commands ride a WebSocket, diagnostics ride HTTP

**Status:** accepted · **Date:** 2026-09-02

## Context

Given a node separate from the core (ADR 0014), something has to carry commands between
them. The obvious design is HTTP: the node listens, the core POSTs commands to it. It is
simple and every developer can picture it.

It also requires the owner's laptop to accept inbound connections, which means a listening
port on a personal machine, a firewall rule, and no answer at all when that machine is on a
café network or behind home NAT — which is where a personal assistant's devices actually
live.

## Decision

**Commands** travel over a WebSocket the node dials *outbound* to the core, and holds open.
The core never initiates a connection to a node.

**Diagnostics** are a separate, tiny HTTP surface on the node itself — `GET /health` and
`GET /capabilities`, bound to loopback, read-only, executing nothing.

The split is the point. The diagnostics endpoints exist for the moment commands are *not*
arriving, and someone standing at the machine needs to know whether the node is running,
what it believes it can do, and why it cannot reach the core. Answering that over the
channel that is broken would be no use, which is why it is a second one. Because it never
executes anything, giving it a local listener costs nothing: the worst it discloses to
someone already on the machine is what the process list would have told them.

`GET /capabilities` reads the executor's own dispatch table rather than a hand-written list.
A capabilities endpoint that has drifted from what the node implements is worse than none,
because it is believed.

## Consequences

- Works behind NAT and on hostile networks with no configuration. The node retries with
  exponential backoff and reconnects on its own.
- The connection is also the liveness signal: a device is reachable exactly while it holds a
  socket. `POST /devices/register` therefore enrols a device without marking it online — a
  device listed as reachable that cannot receive a command would be selected by the router
  and fail three steps into a task.
- One socket per device is cheap; a home has a handful of machines, not thousands.
- **Cost we accepted:** long-lived connection state to manage, and correlating results to
  requests by id rather than getting them as an HTTP response. Both are contained in
  `DeviceHub` and are a small price for a node that needs no network configuration.

## Alternatives considered

- **Core POSTs to a listening node.** Rejected for the reason above: it does not work where
  the devices are.
- **Node polls for work.** Rejected: latency and idle load are both wrong, and "open Chrome"
  should not wait for the next poll.
- **A message broker between them.** Rejected as premature — it adds a component to install
  and operate for a benefit (durability across a core restart) that a personal system does
  not yet need. The `QueueProvider` port means it can be added without touching callers.
