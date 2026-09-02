"""Metrics (§128–131, Sprint 49).

Thursday had a health endpoint and no numbers. Health answers "is it working"; metrics answer
"how well, and since when", and the second question is the one that catches a system slowly
getting worse.

Two things shape this module more than the exposition format does.

**Labels are a privacy leak that nobody classifies.** Every other egress path in this system
goes through the privacy classifier or the redactor. Metrics do not: they are scraped by a
monitoring system that has none of Thursday's controls, retained far longer than anything
else, and read by whoever runs the dashboard. A ``path="/home/owner/tax/2026-divorce.pdf"``
label is a leak, and it is a leak that looks like ordinary engineering. So label *values* are
declared in advance and anything else collapses to ``other`` — which also happens to solve
the cardinality problem, but privacy is the reason.

**A metric that stopped being recorded reads as zero.** That is the same failure Sprint 45
found in cost accounting, where the two model calls every turn makes were counted nowhere and
the system reported $0. So counters are registered up front and exported even at zero, and
"never observed" is distinguishable from "observed none".

Hand-rolled rather than pulling in a client library: the text format is small and stable, this
buys one endpoint, and a system whose whole test suite runs with no infrastructure should not
add a dependency to publish four numbers.
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass, field
from typing import Any

from thursday_core.logging import get_logger

log = get_logger(__name__)

#: What an unrecognised label value becomes. Not dropped: dropping the sample loses the event,
#: and the whole point of a bounded label is that the *count* still tells you something.
OTHER = "other"

#: Latency buckets, in seconds. Chosen for what a person notices rather than for round
#: numbers: under a quarter second is instant, a second is responsive, five is a wait, and
#: past thirty somebody has gone to make tea.
DEFAULT_BUCKETS = (0.25, 1.0, 5.0, 15.0, 30.0, 120.0)


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


@dataclass
class Metric:
    """One family of series: a name, a help string, and its declared labels.

    `allowed` is the safety mechanism. A label declared without an allowed set may take any
    value, and that is refused at registration rather than at record time — an unbounded label
    that exists and is merely never given a bad value is one line away from being given one.
    """

    name: str
    help: str
    kind: str  # counter | gauge | histogram
    labels: tuple[str, ...] = ()
    allowed: dict[str, frozenset[str]] = field(default_factory=dict)
    buckets: tuple[float, ...] = DEFAULT_BUCKETS
    _values: dict[tuple[str, ...], float] = field(default_factory=dict)
    _counts: dict[tuple[str, ...], int] = field(default_factory=dict)
    _sums: dict[tuple[str, ...], float] = field(default_factory=dict)
    _buckets: dict[tuple[str, ...], list[int]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        missing = [label for label in self.labels if label not in self.allowed]
        if missing:
            raise ValueError(
                f"{self.name}: labels {missing} have no declared values. Every label needs a "
                "bounded set — an unbounded one puts the owner's filenames in a monitoring "
                "system that has none of Thursday's privacy controls."
            )

    def key(self, values: dict[str, str]) -> tuple[str, ...]:
        """Normalise a label set, collapsing anything not declared."""
        out: list[str] = []
        for label in self.labels:
            given = str(values.get(label, ""))
            out.append(given if given in self.allowed[label] else OTHER)
        return tuple(out)


class MetricsRegistry:
    """Every number Thursday publishes, and the only place they are named.

    Thread-safe because the API scrapes this while the event loop is writing to it, and a
    dict resized mid-iteration is a 500 on the endpoint that tells you the system is healthy.
    """

    def __init__(self) -> None:
        self._metrics: dict[str, Metric] = {}
        self._lock = threading.Lock()
        self._gauges: dict[str, Any] = {}

    # ------------------------------------------------------------------ declaring

    def register(
        self,
        name: str,
        *,
        help: str,
        kind: str = "counter",
        labels: tuple[str, ...] = (),
        allowed: dict[str, frozenset[str]] | None = None,
        buckets: tuple[float, ...] = DEFAULT_BUCKETS,
    ) -> Metric:
        metric = Metric(
            name=name,
            help=help,
            kind=kind,
            labels=labels,
            allowed=allowed or {},
            buckets=buckets,
        )
        with self._lock:
            self._metrics[name] = metric
        return metric

    def register_gauge_source(self, name: str, *, help: str, read: Any) -> None:
        """A gauge read at scrape time rather than pushed.

        For things that already have an owner — devices online, spend today. Mirroring them
        into a counter would create a second source of truth that can disagree with the
        first, and the disagreement would be invisible.
        """
        self.register(name, help=help, kind="gauge")
        with self._lock:
            self._gauges[name] = read

    # ------------------------------------------------------------------ recording

    def inc(self, name: str, amount: float = 1.0, **labels: str) -> None:
        metric = self._metrics.get(name)
        if metric is None:
            # Loudly, and only in the log: an unregistered metric is a bug in the caller, and
            # creating it on the fly is how an unbounded label gets in.
            log.warning("metric_not_registered", metric=name)
            return
        key = metric.key(labels)
        with self._lock:
            metric._values[key] = metric._values.get(key, 0.0) + amount

    def set(self, name: str, value: float, **labels: str) -> None:
        metric = self._metrics.get(name)
        if metric is None:
            log.warning("metric_not_registered", metric=name)
            return
        with self._lock:
            metric._values[metric.key(labels)] = value

    def observe(self, name: str, value: float, **labels: str) -> None:
        metric = self._metrics.get(name)
        if metric is None or metric.kind != "histogram":
            log.warning("metric_not_a_histogram", metric=name)
            return
        key = metric.key(labels)
        with self._lock:
            counts = metric._buckets.setdefault(key, [0] * len(metric.buckets))
            for i, edge in enumerate(metric.buckets):
                if value <= edge:
                    counts[i] += 1
            metric._counts[key] = metric._counts.get(key, 0) + 1
            metric._sums[key] = metric._sums.get(key, 0.0) + value

    # ------------------------------------------------------------------ reading

    def value(self, name: str, **labels: str) -> float:
        metric = self._metrics.get(name)
        if metric is None:
            return 0.0
        if name in self._gauges:
            return float(self._gauges[name]())
        return metric._values.get(metric.key(labels), 0.0)

    def render(self) -> str:
        """The Prometheus text exposition format.

        Every registered series is emitted even when it is zero. A counter that appears only
        after its first event makes "nothing has gone wrong" and "the instrumentation broke"
        look identical on a dashboard, and they are the two readings that matter most.
        """
        lines: list[str] = []
        with self._lock:
            metrics = list(self._metrics.values())
            gauges = dict(self._gauges)

        for metric in metrics:
            lines.append(f"# HELP {metric.name} {metric.help}")
            lines.append(f"# TYPE {metric.name} {metric.kind}")

            if metric.name in gauges:
                lines.append(f"{metric.name} {_number(float(gauges[metric.name]()))}")
                continue

            if metric.kind == "histogram":
                lines.extend(self._render_histogram(metric))
                continue

            if not metric._values:
                # An unlabelled metric with no data is a real zero and says so.
                if not metric.labels:
                    lines.append(f"{metric.name} 0")
                continue

            for key, value in sorted(metric._values.items()):
                lines.append(f"{metric.name}{_labels(metric.labels, key)} {_number(value)}")

        return "\n".join(lines) + "\n"

    def _render_histogram(self, metric: Metric) -> list[str]:
        lines: list[str] = []
        for key, counts in sorted(metric._buckets.items()):
            running = 0
            for edge, count in zip(metric.buckets, counts, strict=True):
                running += count
                labels = _labels(metric.labels, key, extra={"le": _number(edge)})
                lines.append(f"{metric.name}_bucket{labels} {running}")
            total = metric._counts.get(key, 0)
            lines.append(
                f"{metric.name}_bucket{_labels(metric.labels, key, extra={'le': '+Inf'})} {total}"
            )
            lines.append(
                f"{metric.name}_sum{_labels(metric.labels, key)} {_number(metric._sums.get(key, 0.0))}"
            )
            lines.append(f"{metric.name}_count{_labels(metric.labels, key)} {total}")
        return lines

    def names(self) -> list[str]:
        return sorted(self._metrics)


def _labels(
    names: tuple[str, ...], key: tuple[str, ...], extra: dict[str, str] | None = None
) -> str:
    pairs = [f'{name}="{_escape(value)}"' for name, value in zip(names, key, strict=True)]
    pairs += [f'{k}="{_escape(v)}"' for k, v in (extra or {}).items()]
    return "{" + ",".join(pairs) + "}" if pairs else ""


def _number(value: float) -> str:
    if math.isinf(value):
        return "+Inf" if value > 0 else "-Inf"
    return f"{value:g}"


# --------------------------------------------------------------------------- the numbers


def _allowed_actions() -> frozenset[str]:
    """The device action catalogue. A bounded set by construction, and public names.

    Read from the catalogue rather than written out here, so a new action is measurable the
    day it exists rather than reading as `other` until somebody notices.

    Deliberately not wrapped in a `try`. The first version of this caught `Exception` and fell
    back to `{"unknown"}` — which meant an `AttributeError` from a renamed accessor collapsed
    *every* action into one series, forever, with the endpoint still returning 200 and the
    dashboard still drawing a line. A metrics module that degrades quietly is worse than one
    that fails at import, because the quiet version is trusted.
    """
    from thursday_devices.actions import CATALOGUE

    return frozenset(CATALOGUE) | {"unknown"}


def build_registry() -> MetricsRegistry:
    """Everything Thursday publishes, declared in one place.

    Read this as the answer to "what would tell me Thursday is getting worse". Not request
    rates — those measure whether the web framework works. These measure whether the
    *assistant* works: is it still verifying what it claims, how often does it have to ask,
    how often does a model fall over, how often does something reach for a secret.
    """
    from thursday_shared.enums import AgentVerdict, PolicyDecision

    registry = MetricsRegistry()
    decisions = frozenset(d.value for d in PolicyDecision)
    verdicts = frozenset(v.value for v in AgentVerdict)
    outcomes = frozenset({"verified", "unverified", "failed"})

    registry.register(
        "thursday_device_actions_total",
        help="Device actions attempted, by action and outcome.",
        labels=("action", "outcome"),
        allowed={"action": _allowed_actions(), "outcome": outcomes},
    )
    registry.register(
        "thursday_permission_decisions_total",
        help="Permission decisions, by what the engine decided.",
        labels=("decision",),
        allowed={"decision": decisions},
    )
    registry.register(
        "thursday_verifications_total",
        help="Supervisor verdicts. A falling PASS rate is the earliest sign of trouble.",
        labels=("verdict",),
        allowed={"verdict": verdicts},
    )
    registry.register(
        "thursday_tasks_total",
        help="Tasks by how they ended.",
        labels=("outcome",),
        allowed={"outcome": frozenset({"completed", "failed", "cancelled"})},
    )
    registry.register(
        "thursday_agent_runs_total",
        help="Agent runs, by agent and outcome.",
        labels=("agent", "outcome"),
        allowed={
            "agent": frozenset(
                {
                    "computer",
                    "research",
                    "browser",
                    "data",
                    "document",
                    "vision",
                    "files",
                    "coding",
                    "automation",
                    "calendar",
                    "communication",
                    "design",
                    "media",
                    "dynamic",
                }
            ),
            "outcome": frozenset({"completed", "failed"}),
        },
    )
    registry.register(
        "thursday_prompt_redactions_total",
        help="Credential-shaped material stripped from a prompt, by pattern name (§90).",
        labels=("pattern",),
        allowed={
            "pattern": frozenset(
                {
                    "anthropic_key",
                    "openai_key",
                    "github_token",
                    "aws_access_key",
                    "google_key",
                    "slack_token",
                    "jwt",
                    "private_key",
                    "bearer",
                    "assignment",
                    "connection_string",
                }
            )
        },
    )
    registry.register(
        "thursday_model_fallbacks_total",
        help="Times a model call degraded to the local tier, by why.",
        labels=("reason",),
        allowed={"reason": frozenset({"provider_failed", "cost_cap", "privacy", "offline"})},
    )
    registry.register(
        "thursday_task_seconds",
        help="How long tasks take, end to end.",
        kind="histogram",
    )
    return registry


class MetricsCollector:
    """Turns events into numbers.

    Subscribed to the bus rather than sprinkled through the call sites, for the same reason
    metering moved to the router in Sprint 45: instrumentation that each caller has to
    remember is instrumentation the important callers forget. The bus already carries every
    event these numbers are made of.

    Nothing here reads a payload field that could hold content — no paths, no text, no
    resource names. Only outcomes, and only ones already in a bounded enum.
    """

    def __init__(self, registry: MetricsRegistry) -> None:
        self.registry = registry

    def attach(self, bus: Any) -> None:
        bus.subscribe("device.action_completed", self._device_action)
        bus.subscribe("task.completed", self._task_ended)
        bus.subscribe("task.failed", self._task_ended)
        bus.subscribe("task.cancelled", self._task_ended)
        bus.subscribe("agent.completed", self._agent_ran)
        bus.subscribe("agent.failed", self._agent_ran)

    def _device_action(self, event: Any) -> None:
        payload = getattr(event, "payload", {}) or {}
        ok = bool(payload.get("ok"))
        outcome = ("verified" if payload.get("verified") else "unverified") if ok else "failed"
        self.registry.inc(
            "thursday_device_actions_total",
            action=str(payload.get("action", "unknown")),
            outcome=outcome,
        )

    def _task_ended(self, event: Any) -> None:
        outcome = str(getattr(event, "kind", "")).removeprefix("task.")
        self.registry.inc("thursday_tasks_total", outcome=outcome)
        payload = getattr(event, "payload", {}) or {}
        seconds = payload.get("duration_s")
        if isinstance(seconds, int | float):
            self.registry.observe("thursday_task_seconds", float(seconds))

    def _agent_ran(self, event: Any) -> None:
        payload = getattr(event, "payload", {}) or {}
        self.registry.inc(
            "thursday_agent_runs_total",
            agent=str(payload.get("agent", "")),
            outcome="completed" if getattr(event, "kind", "") == "agent.completed" else "failed",
        )
