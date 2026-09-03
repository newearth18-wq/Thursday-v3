"""Rate limiting for the HTTP surface (§128).

§128 names five things to limit: login, pairing, approval endpoints, public-facing APIs and
expensive model endpoints. Thursday has no login (§23.2 — single owner, no user model), and
pairing already limits itself at the service layer, where the interesting budget lives: a
guess budget counted across *all* codes, because the codes an attacker guesses are the ones
that do not exist. This module is the layer in front of that, and it exists for a different
threat.

**What this defends against.** Not a stranger on the internet: the API is not exposed, and
§23.2 says so in the same breath as saying it must not be. It defends against something on
the machine or the LAN — a misbehaving script, a runaway retry loop, a curious process —
turning an endpoint that costs a model call into an unbounded bill or an unbounded queue. The
spend ledger caps money after the fact; this caps the rate before the call is made.

**What it is keyed on, and why that is the hard part.** The peer address, from the socket.
Not a header. `X-Forwarded-For` is the obvious choice behind the reverse proxy §127
recommends, and it is exactly the trap: a header the caller writes is a bucket the caller
chooses, so a limiter keyed on it is one an attacker turns off by varying it. It is honoured
only when the peer is a configured trusted proxy, and the default list is empty — a
deployment that puts Thursday behind a proxy has to say so, and until it does every request
shares the proxy's bucket, which is a visible degradation rather than a silent hole.

**What is never limited.** The emergency stop (§134). A kill switch that can be rate-limited
is a kill switch an attacker can hold shut by making requests, and every second it is held is
a second Thursday keeps acting. Health checks are exempt too, for a duller reason: a monitor
polling `/health` must not be able to consume the budget that answers a person.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

#: Paths that are never limited, matched as prefixes. Short, and every entry is an argument.
NEVER_LIMITED: tuple[str, ...] = (
    # §134. Rate-limiting the kill switch would let an attacker hold it shut.
    "/api/v1/emergency/",
    # A monitor must not be able to spend the budget that answers a person.
    "/api/v1/health",
    "/health",
)


@dataclass(frozen=True)
class Limit:
    """`requests` allowed in any window of `per_seconds`."""

    requests: int
    per_seconds: float

    def __post_init__(self) -> None:
        if self.requests < 1 or self.per_seconds <= 0:
            raise ValueError("a limit that allows nothing is a broken endpoint, not a limit")


@dataclass(frozen=True)
class Decision:
    allowed: bool
    #: How long until this caller could succeed. Zero when allowed.
    retry_after_s: float = 0.0
    #: Which class of limit was applied, for the log and the error detail.
    klass: str = "default"


class RateLimiter:
    """Sliding-window counters, one bucket per (caller, class).

    A sliding window rather than a fixed one: a fixed window lets a caller spend the whole
    budget in the last instant of one window and the whole budget again in the first instant
    of the next, which is twice the limit at exactly the moment it matters.

    Memory is bounded in both directions. Timestamps older than the window are dropped on
    every touch, and the number of distinct buckets is capped — an unbounded map keyed on
    something a caller varies is a slow leak that an attacker can drive on purpose.
    """

    def __init__(
        self,
        limits: Mapping[str, Limit],
        *,
        max_buckets: int = 4096,
        clock: object | None = None,
    ) -> None:
        self._limits = dict(limits)
        self._max_buckets = max_buckets
        self._buckets: OrderedDict[tuple[str, str], list[float]] = OrderedDict()
        self._clock = clock or time.monotonic

    def check(self, caller: str, klass: str) -> Decision:
        """Count this request, and say whether it may proceed."""
        limit = self._limits.get(klass)
        if limit is None:
            return Decision(True)

        now = float(self._clock())  # type: ignore[operator]
        key = (caller, klass)
        window = [t for t in self._buckets.get(key, ()) if now - t < limit.per_seconds]

        if len(window) >= limit.requests:
            self._buckets[key] = window
            self._buckets.move_to_end(key)
            # The oldest request in the window is the one whose expiry frees a slot.
            return Decision(False, limit.per_seconds - (now - window[0]), klass)

        window.append(now)
        self._buckets[key] = window
        self._buckets.move_to_end(key)
        self._evict()
        return Decision(True, 0.0, klass)

    def _evict(self) -> None:
        """Drop the least recently used buckets once there are too many.

        Evicting a bucket forgives whatever it had counted, which sounds like a hole and is
        the lesser of two. The alternative — refusing new callers once the map is full —
        would let one attacker with many source addresses lock every other caller out, which
        is the denial of service the limiter is supposed to prevent.
        """
        while len(self._buckets) > self._max_buckets:
            self._buckets.popitem(last=False)

    @property
    def buckets(self) -> int:
        return len(self._buckets)


def classify(path: str, method: str, *, expensive: Iterable[str], approvals: str) -> str | None:
    """Which limit applies to this request, or None for "never limited".

    Classification is on the path the router matched against, so it cannot be steered by a
    query string or a header.
    """
    if path.startswith(NEVER_LIMITED):
        return None
    if path.startswith(approvals):
        return "approvals"
    if "/devices/pair/" in path:
        return "pairing"
    if method == "POST" and any(path.startswith(prefix) for prefix in expensive):
        return "expensive"
    return "default"


def caller_of(client_host: str | None, forwarded_for: str | None, trusted: frozenset[str]) -> str:
    """The bucket key for this request.

    `forwarded_for` is read **only** when the immediate peer is a trusted proxy. Any other
    time it is ignored entirely, header present or not — a caller who can pick their own
    bucket has no limit at all, and this is the one line where that would happen.
    """
    host = client_host or "unknown"
    if host in trusted and forwarded_for:
        # The left-most entry is the original client; the rest were added by hops that,
        # by definition, we have not vetted.
        return forwarded_for.split(",")[0].strip() or host
    return host
