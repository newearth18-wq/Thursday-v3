# ADR 0014 — The core never touches an operating system directly

**Status:** accepted · **Date:** 2026-09-02

## Context

Thursday's job is to open applications, read files and control machines. The tempting shape
is for the core to do that itself: import `psutil`, shell out, call the Win32 API. It works
immediately, on the developer's machine, for one machine.

It stops working the moment there are two. It also makes the core untestable without the
operating system it happens to be running on, which means the safety properties can only be
tested where they matter least.

## Decision

OS access lives in a **node**: a separate process, on the machine it controls, speaking one
protocol. The core holds `DeviceRouter` → `DeviceHub` → a session, and knows nothing below
that. `packages/core` imports no OS API, and `packages/devices/node/adapters` is the only
place that does.

The node — not the core — owns:

- the allowlist of what an application name may resolve to;
- the path jail, so an action cannot touch a directory outside the node's allowed roots;
- verification, because only the machine can observe its own state.

Putting confinement on the node rather than the core is the important half. The core is the
component that takes instructions from a language model and from web pages; it is the
component most likely to be talked into asking for something it shouldn't. The node refuses
regardless of who is asking or how convincingly.

## Consequences

- Multi-device is the default shape rather than a later migration. A second machine is a
  second node.
- The core is testable with no OS at all: `FakeDeviceNode` runs the *real* executor — path
  confinement, argument validation and verification all genuine — against a machine that
  exists only in memory.
- A compromised core cannot reach past what the node permits. That is a meaningful boundary
  precisely because the core is the interesting thing to compromise.
- **Cost we accepted:** a round trip and a second process to run and keep alive. In exchange
  the blast radius of the component that talks to models is bounded by something that does
  not.

## Alternatives considered

- **OS calls in the core, node added later.** Rejected: "later" means unpicking direct calls
  from every layer that grew to expect them, and until then the safety tests need a real
  desktop.
- **A thin node that shells out whatever it is told.** Rejected: it moves the process
  boundary without moving the trust boundary. The node's value is that it refuses.
- **SSH or WinRM instead of a purpose-built protocol.** Rejected: both require inbound
  access and standing credentials on the target, and neither carries the structured
  verification evidence that ADR 0012 depends on.
