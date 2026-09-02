"""Frame sampling (§52, V6).

**A video stream never leaves this machine.** Not a reduced one, not a compressed one — the
stream itself is never the unit that goes anywhere. What travels, when anything travels at
all, is a single frame that a *local* detector already decided was worth a second look.

The reasoning is about volume rather than any single frame. A camera at 30fps produces
108,000 images an hour; a person cannot review that, cannot meaningfully consent to it, and
cannot un-send it. One frame, chosen for a reason, is a thing the owner can be told about.

Three gates, in order, each cheaper than the next:

1. **Interval** — never more often than N seconds, whatever the source does.
2. **Change** — a frame that looks like the last one is not news.
3. **Interest** — a local detector found something worth escalating.

A frame that passes all three is a candidate. Everything else is discarded in this process
and never recorded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from thursday_core.logging import get_logger

from thursday_vision.ports import Detection, Frame

log = get_logger(__name__)


@dataclass
class SamplingPolicy:
    """Every bound in one place, so none of them is implicit."""

    #: Never sample more often than this, whatever the camera does.
    min_interval_s: float = 1.0
    #: How different a frame must be from the last kept one to count as news, 0-1.
    change_threshold: float = 0.12
    #: A local detection below this is not worth escalating.
    interest_threshold: float = 0.45
    #: Hard cap per window, so a flickering scene cannot become a stream by another name.
    max_per_minute: int = 12
    #: Labels always worth a look regardless of change — what the owner asked about.
    always_interesting: frozenset[str] = frozenset()


@dataclass
class SampleDecision:
    keep: bool
    reason: str
    detections: list[Detection] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.keep


class FrameSampler:
    """Decides which frames are worth keeping. Stateful — it remembers the last one."""

    def __init__(self, policy: SamplingPolicy | None = None) -> None:
        self.policy = policy or SamplingPolicy()
        self._last_kept_at: datetime | None = None
        self._last_signature: list[int] | None = None
        self._recent: list[datetime] = []
        self.seen = 0
        self.kept = 0

    @property
    def dropped(self) -> int:
        return self.seen - self.kept

    def _signature(self, frame: Frame, buckets: int = 32) -> list[int]:
        """A crude perceptual fingerprint: byte histogram over the encoded frame.

        Not a real perceptual hash, and it does not need to be. The question is only "is
        this materially the same picture as the last one", and a histogram answers it
        without decoding the image — which matters because decoding every frame to decide
        whether to look at it would cost more than looking.

        Its blind spot is worth naming: a histogram is invariant to arrangement, so a scene
        whose contents moved without changing the overall colour distribution reads as
        unchanged. That errs towards *not* capturing, which is the safe direction here — a
        missed frame costs a second look, and an extra frame costs privacy. The detector
        gate below catches what matters anyway, since anything the local model finds
        interesting is kept regardless of how similar the picture looked.
        """
        histogram = [0] * buckets
        step = max(1, len(frame.data) // 4096)  # sample the bytes; full scan is wasteful
        for index in range(0, len(frame.data), step):
            histogram[frame.data[index] * buckets // 256] += 1
        total = sum(histogram) or 1
        return [round(count * 1000 / total) for count in histogram]

    def _difference(self, signature: list[int]) -> float:
        if self._last_signature is None:
            return 1.0
        total = sum(abs(a - b) for a, b in zip(signature, self._last_signature, strict=False))
        return min(1.0, total / 2000)

    def _within_rate_limit(self, now: datetime) -> bool:
        cutoff = now - timedelta(seconds=60)
        self._recent = [t for t in self._recent if t > cutoff]
        return len(self._recent) < self.policy.max_per_minute

    def consider(
        self,
        frame: Frame,
        detections: list[Detection] | None = None,
        *,
        now: datetime | None = None,
    ) -> SampleDecision:
        """Should this frame be kept?

        ``detections`` come from a *local* detector. Passing None means nothing looked at
        it yet, in which case change alone decides — the sampler never sends a frame
        somewhere to find out whether it was interesting.
        """
        now = now or datetime.now(UTC)
        self.seen += 1
        policy = self.policy

        if self._last_kept_at is not None:
            since = (now - self._last_kept_at).total_seconds()
            if since < policy.min_interval_s:
                return SampleDecision(False, f"only {since:.2f}s since the last frame")

        if not self._within_rate_limit(now):
            # The hard stop. Without it, a scene that flickers past the change threshold
            # becomes a stream by another name, which is the thing this file exists to
            # prevent.
            return SampleDecision(False, f"rate limit: {policy.max_per_minute}/min reached")

        signature = self._signature(frame)
        difference = self._difference(signature)

        interesting = [
            d
            for d in (detections or [])
            if d.confidence >= policy.interest_threshold
            or d.label.lower() in policy.always_interesting
        ]

        if interesting:
            return self._keep(
                now, signature, f"{len(interesting)} object(s) of interest", interesting
            )
        if detections is not None and not interesting:
            # Something looked and found nothing worth escalating. That is an answer.
            return SampleDecision(False, "nothing of interest in the frame")
        if difference >= policy.change_threshold:
            return self._keep(now, signature, f"scene changed by {difference:.0%}")
        return SampleDecision(False, f"unchanged ({difference:.0%} difference)")

    def _keep(
        self,
        now: datetime,
        signature: list[int],
        reason: str,
        detections: list[Detection] | None = None,
    ) -> SampleDecision:
        self._last_kept_at = now
        self._last_signature = signature
        self._recent.append(now)
        self.kept += 1
        log.debug("frame_kept", reason=reason, kept=self.kept, dropped=self.dropped)
        return SampleDecision(True, reason, detections or [])

    def reset(self) -> None:
        self._last_kept_at = None
        self._last_signature = None
        self._recent.clear()

    def stats(self) -> dict:
        return {
            "seen": self.seen,
            "kept": self.kept,
            "dropped": self.dropped,
            "keep_rate": round(self.kept / self.seen, 4) if self.seen else 0.0,
        }
