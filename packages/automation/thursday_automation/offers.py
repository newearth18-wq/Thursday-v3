"""Offers: the things Thursday has suggested and is waiting on (§46, V10).

An `Observation` says something is true. An **offer** is what happens when Thursday puts it
to the owner as a question and holds the answer open. The two are separate because the
lifetimes are different: noticing happens continuously in a worker loop, and the answer
arrives whenever the owner next says something.

Three properties, and each is there because of a specific way this goes wrong:

**Offers expire.** "Shall I prepare for tomorrow's meeting?" is a dead question the day
after. An offer the owner answers a week late, whose acceptance then creates a task about a
meeting that already happened, is worse than one that quietly lapsed.

**One "yes" answers one question.** `accept()` takes the most recent offer, not all of them.
An owner saying "ทำเลย" to a list of three suggestions has agreed to something, and nobody —
including them — could say which; so the answer is applied to the one just asked, and the
others stay open.

**An approval is not an offer.** Both are answered with the same word, and they are not the
same thing: an approval gates work already under way and was *asked for*; an offer is a
suggestion nobody requested. When both are outstanding the approval wins, because the owner
is far more likely to be answering the thing that interrupted them. That precedence lives in
the engine, where both are visible; this module just makes offers answerable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from thursday_core.logging import get_logger
from thursday_shared.ids import new_id

log = get_logger(__name__)

#: How long an unanswered offer stays answerable. Long enough to survive being ignored for
#: an afternoon, short enough that "yes" tomorrow cannot mean yesterday's question.
OFFER_TTL = timedelta(hours=12)


@dataclass
class Offer:
    """A suggestion put to the owner, waiting on an answer."""

    id: UUID = field(default_factory=new_id)
    text: str = ""
    #: What accepting turns into. Read by whatever executes it — kept as data rather than a
    #: callable so an offer can be listed, inspected and audited before it is taken up.
    action: dict[str, Any] = field(default_factory=dict)
    #: Ties the offer back to what prompted it, so accepting can un-suppress the observation.
    fingerprint: str = ""
    session_id: UUID | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    answered_at: datetime | None = None
    accepted: bool | None = None

    @property
    def open(self) -> bool:
        return self.answered_at is None

    def expired(self, *, now: datetime | None = None, ttl: timedelta = OFFER_TTL) -> bool:
        return (now or datetime.now(UTC)) - self.created_at > ttl


class OfferBook:
    """The offers Thursday has made and not yet had an answer to."""

    def __init__(self, *, ttl: timedelta = OFFER_TTL) -> None:
        self._offers: dict[UUID, Offer] = {}
        self._ttl = ttl

    def make(
        self,
        text: str,
        *,
        action: dict[str, Any] | None = None,
        fingerprint: str = "",
        session_id: UUID | None = None,
        now: datetime | None = None,
    ) -> Offer:
        offer = Offer(
            text=text,
            action=dict(action or {}),
            fingerprint=fingerprint,
            session_id=session_id,
            created_at=now or datetime.now(UTC),
        )
        self._offers[offer.id] = offer
        log.debug("offer_made", text=text[:60])
        return offer

    def pending(self, *, now: datetime | None = None) -> list[Offer]:
        """Open, unexpired offers, most recent first."""
        now = now or datetime.now(UTC)
        return sorted(
            (o for o in self._offers.values() if o.open and not o.expired(now=now, ttl=self._ttl)),
            key=lambda o: o.created_at,
            reverse=True,
        )

    def latest(self, *, now: datetime | None = None) -> Offer | None:
        """The question most recently asked — the one a bare "yes" is answering."""
        return next(iter(self.pending(now=now)), None)

    def accept(self, offer_id: UUID | None = None, *, now: datetime | None = None) -> Offer | None:
        """Take up one offer. With no id, the most recent.

        Deliberately not "accept everything outstanding". An owner saying yes to a list has
        agreed to *something*, and nobody could say which — so one answer settles one
        question and the rest stay open to be asked about again.
        """
        return self._answer(offer_id, accepted=True, now=now)

    def decline(self, offer_id: UUID | None = None, *, now: datetime | None = None) -> Offer | None:
        return self._answer(offer_id, accepted=False, now=now)

    def _answer(
        self, offer_id: UUID | None, *, accepted: bool, now: datetime | None
    ) -> Offer | None:
        now = now or datetime.now(UTC)
        offer = self._offers.get(offer_id) if offer_id else self.latest(now=now)
        if offer is None or not offer.open:
            return None
        offer.answered_at = now
        offer.accepted = accepted
        log.info("offer_answered", accepted=accepted, text=offer.text[:60])
        return offer

    def get(self, offer_id: UUID) -> Offer | None:
        return self._offers.get(offer_id)

    def __len__(self) -> int:
        return len(self.pending())
