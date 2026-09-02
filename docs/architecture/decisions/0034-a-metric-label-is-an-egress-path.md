# ADR 0034 — A metric label is an egress path nobody classifies

**Status:** accepted · **Date:** 2026-09-02

## Context

Thursday had a health endpoint and no numbers. Health answers "is it working"; metrics answer
"how well, and since when", and the second question is the one that catches a system slowly
getting worse — a falling verification rate, an approval queue that only grows, a model
falling back to local every third call.

Adding metrics to *this* system raises a problem it does not raise elsewhere. Every other way
data leaves Thursday goes through the privacy classifier or the redactor. Metrics do not. They
are scraped by a monitoring system that has none of Thursday's controls, retained far longer
than anything else, and read by whoever runs the dashboard. A label reading
`path="/home/owner/tax/2026-divorce.pdf"` is a disclosure, and it is one that looks like
ordinary engineering while you are writing it.

## Decision

**Label values are declared in advance; anything else becomes `other`.** A `Metric` cannot be
registered with a label that has no bounded set — refused at registration rather than at
record time, because an unbounded label that exists and is merely never given a bad value is
one line away from being given one. Collapsing rather than dropping keeps the count honest:
the event still happened, and the number still says so.

Cardinality control falls out of this for free. It is not the reason.

**Only outcomes are labelled.** Decisions, verdicts, agent names, action names from the
catalogue, redaction *pattern* names. Never a resource, a path, a filename, a URL or anything
the owner typed. `MetricsCollector` reads no payload key that could hold content, and there is
a test asserting that about its source.

**Allowed action names come from the catalogue, not a list.** So a new action is measurable
the day it exists rather than reading as `other` until somebody notices. The first version
wrapped that import in `except Exception` and fell back to a one-element set — which would
have collapsed *every* action into one series for ever, with the endpoint returning 200 and
the dashboard drawing a line. A metrics module that degrades quietly is worse than one that
fails at import, because the quiet one is trusted.

**Every registered series is exported even at zero.** "Nothing has gone wrong" and "the
instrumentation broke" must not look identical, and on a dashboard a missing series and a flat
zero are exactly the two readings that matter.

**Counting happens at choke points.** The collector subscribes to the event bus rather than
each service reporting; `PermissionEngine.decide` wraps a private `_decide` with eighteen
return paths; redactions and fallbacks are counted in the router. Instrumentation each caller
has to remember is instrumentation the important callers forget — the same mistake Sprint 45
found in cost accounting and Sprint 46 found in prompt redaction, now avoided by construction.

**Gauges are read at scrape time.** Devices online, spend today, approvals pending already
have owners. Mirroring them into counters would create a second source of truth that can
disagree with the first, and the disagreement would be invisible.

**Hand-rolled exposition.** The text format is small and stable, this buys one endpoint, and a
system whose entire test suite runs with no infrastructure should not take a dependency to
publish four numbers.

## Consequences

- The metrics endpoint is safe to point a shared monitoring system at, which is the only way
  it is useful.
- Adding a metric means declaring its label values, which is friction. That friction is the
  control.
- The numbers describe the assistant rather than the web server: verification outcomes,
  permission decisions, redactions, fallbacks, spend. Request rates measure whether FastAPI
  works, which nobody was worried about.
- **Cost we accepted:** an action or agent added without updating its allowed set reads as
  `other` until somebody notices. That is the safe direction, and the catalogue-derived action
  set removes the common case.

## Alternatives considered

- **Allow free-form labels and redact at the endpoint.** Rejected: it puts the check after the
  data is already in the registry, in memory, for whatever else reads it — and a redactor
  tuned for credentials would not catch a filename anyway.
- **Hash sensitive label values.** Rejected: a stable hash of a path is a stable identifier for
  that path, so the dashboard still tracks the owner's divorce file, just under a nickname.
- **Use `prometheus_client`.** Rejected for one endpoint, and it would not have given the
  bounded-label rule, which is the part that matters here.
- **Instrument each call site.** Rejected — this repository has found the same bug from that
  pattern twice already.
