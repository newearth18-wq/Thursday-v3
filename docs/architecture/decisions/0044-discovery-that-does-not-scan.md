# 44. Discovery that does not scan

Date: Sprint 54 (ADDENDUM — Local AI Compute)

## Status

Accepted. First of the addendum's compute sprints; the registry, the compute router and the
distributed-task sprints build on the vocabulary defined here.

## Context

The addendum asks Thursday to use AI models running on the owner's own machines, choosing
between "this PC", "the GPU box", "the home server" and the cloud. Before anything can be
chosen, Thursday has to know what exists. §41's acceptance criterion is "Thursday lists
available local models **without manual configuration where safe**".

The obvious implementation of that sentence is a network sweep: probe 11434 and 1234 across
the local subnet, collect whatever answers. It would work on the first try, on a home network,
and demo well.

§29 of the same addendum says local AI servers must not be publicly reachable and must accept
requests only from localhost or a trusted LAN. Those two paragraphs are in tension, and the
tension is the decision.

## Decision

**Discovery probes loopback and configured endpoints. It never scans.**

A sweep is the wrong tool three times over. It builds a map of every unauthenticated
inference server on the network — which is the map an attacker wants, assembled by the
owner's own assistant and stored where it is convenient to read. It is indistinguishable
from reconnaissance to anything watching the network. And it will find servers that are not
the owner's: a flatmate's Ollama, a neighbour's misconfigured box on shared infrastructure,
a colleague's laptop on an office LAN. Thursday would then be one routing decision away from
sending a HIGHLY_PRIVATE document to a machine nobody authorised, having satisfied a privacy
rule that only checks whether the endpoint is "local".

So a runtime on another machine is reached the way every other capability on another machine
is reached: by running a Thursday node on it. That node pairs, signs its HELLO with a key the
owner confirmed, reports its own inventory, and can be revoked. The security model that
already exists for "run a command on the GPU box" is exactly the one needed for "run a model
on the GPU box", and inventing a second, weaker path for the second case would undo the first.

`HttpRuntime` refuses a non-loopback endpoint **at construction**, not at request time, unless
explicitly constructed with `allow_remote`. A runtime object that exists is one something will
eventually call, and "we check the address before sending" is a promise that outlives the call
site that made it.

**Capabilities are derived, never declared.** A node advertises `ai.vision` because it holds a
vision model, not because a configuration file says so. The alternative fails at the point of
use, which is the worst place: the router picks the machine, the request goes out, and the
error arrives after the owner has been told Thursday is working on it. §42's names slot into
the existing prefix-walking `DeviceCapabilities` (ADR 0007) unchanged — `ai.embedding` matches
the same way `file.read` does, and no new matching rule was needed for any of it.

**Nothing in this path can download.** §39 requires a model, its size, its source and its disk
cost to be shown before a download, and §41 puts install and remove behind approval. Install is
therefore *absent* rather than present-and-guarded: approval in Thursday means the Permission
Engine, the Permission Engine authorises actions in the catalogue, and a manager method is not
something it can authorise. Install and remove will arrive as `ai.model.install` and
`ai.model.remove` with policies of their own. Two tests hold this: one walks the AST for a
definition or call that downloads, and one asserts the only HTTP verb in the whole discovery
path is GET — because a runtime adapter that grew a `post` could start an Ollama pull without
the word "install" appearing anywhere.

**Requirements are bytes, not adjectives.** A model's needs are stated as `required_vram_bytes`
because "large" cannot be compared against 16 GB of free VRAM. `has_gpu` is likewise keyed on
VRAM rather than on the GPU's name: an integrated chip reports a name too, and §17 specifically
wants vision work on the RTX box rather than on the laptop.

## Consequences

Thursday can answer "where can I think?" from `GET /devices/compute` with nothing configured
by hand, for every machine running a node. A machine with no runtime is *listed* as unable
rather than omitted, because "cannot run models" and "does not exist" are different answers.

The cost is a real one and worth stating: a GPU server the owner has not put a node on is
invisible to Thursday, even sitting on the same network with Ollama listening. That is the
intended outcome — reaching it requires the owner to pair it, which takes a minute and gives
them a revocation switch — but it does mean local AI is not zero-setup for a multi-machine
household. Naming an endpoint in configuration is the escape hatch, and it is explicit.

Model *purpose* is guessed from the model's name (`llava` → vision, `nomic-embed` →
embedding), because no runtime reports what a model is for. The guess is crude and the code
says so. A wrong guess fails at the point of use rather than returning something plausible,
which is the failure direction to prefer, but it is a guess and the registry sprint should let
the owner correct one.

Load rides the heartbeat rather than HELLO. Routing to a machine on the strength of what it
looked like at connect time is how work lands on the box that is already saturated.
