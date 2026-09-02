"""Model Router (§33) with the privacy hard stop from §34 and the spending ceiling (§61).

Selection is complexity × privacy × latency × cost × availability. The privacy rule is not
a preference: a SECRET payload cannot reach a non-local provider, and the router raises
rather than degrading quietly.

Two things live here because this is the single point every model call passes through, and a
rule enforced anywhere else is a rule with a way around it:

**Metering.** Every completion is recorded — provider, tier, tokens, cost — by the router
rather than by its callers. Reporting your own spend is optional in practice, and the two
calls that turned out not to be reporting were the two every turn makes.

**The ceiling.** A cap is checked *before* a paid call and degrades to the local model, which
is free. It does not refuse the work: a spending limit that stops Thursday working is worse
than the overspend it prevents, and an outage the owner cannot tell from a broken assistant
is one they fix by deleting the limit.

**Redaction.** §90 puts a prompt transcript first on the list of places a secret may never
appear, and §194 says the same thing as a rule: no credential is placed in a model prompt.
The redactor's own docstring claimed it ran on every prompt and did not — nothing called it
on the way to a provider. It does now, here, for every call including the local one: a secret
does not become acceptable because the model is on this machine, and the prompt is logged
either way.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from thursday_shared.enums import DataSensitivity, ModelTier
from thursday_shared.errors import BudgetExceeded, PrivacyViolation, ProviderError
from thursday_shared.models import HealthStatus, LLMRequest, LLMResponse

from thursday_core.cost import CostMeter
from thursday_core.logging import get_logger

log = get_logger(__name__)

#: Consecutive failures that park a provider. Three, because one is noise and two is a bad
#: minute; a provider that has failed three times in a row is not having a bad minute.
BREAKER_TRIP = 3

#: How long a parked provider stays parked before it is tried once more. Without this the
#: breaker never opens again: a parked provider is never chosen, so it never succeeds, so the
#: counter that only resets on success never resets — three transient failures would disable
#: a good provider until somebody restarted the process. The same reasoning as ADR 0028's
#: attempt window, which this originally failed to apply.
BREAKER_COOLDOWN = timedelta(minutes=5)

_COMPLEX_MARKERS = re.compile(
    r"(?i)(analy[sz]e|วิเคราะห์|compare|เปรียบเทียบ|why|ทำไม|explain|อธิบาย|design|ออกแบบ|"
    r"plan|วางแผน|debug|refactor|prove|สรุปเหตุผล|trade[- ]?off)"
)
_TRIVIAL_MARKERS = re.compile(
    r"(?i)^(open|เปิด|close|ปิด|start|run|show|list|ดู|status|สถานะ|hi|hello|สวัสดี|"
    r"yes|no|ใช่|ไม่)\b"
)


@dataclass
class RouteDecision:
    tier: ModelTier
    provider_name: str
    reasons: tuple[str, ...] = ()
    fallback_from: str | None = None


@dataclass
class ModelRouter:
    """Holds one provider per tier; tiers may share a provider."""

    providers: dict[ModelTier, object] = field(default_factory=dict)
    allow_cloud: bool = True
    #: The spend ledger and its ceiling. Optional so a router built for a test is not
    #: obliged to have one; the container always wires it.
    meter: CostMeter | None = None
    #: The last stop before a prompt leaves for a provider (§90). Also optional, and also
    #: always wired — a router without one is a test's router, not a deployment's.
    redactor: Any = None
    #: Counts what the two above do. Pattern names and fallback reasons only, both bounded.
    metrics: Any = None
    _breaker: dict[str, int] = field(default_factory=dict)
    _tripped_at: dict[str, datetime] = field(default_factory=dict)

    def register(self, tier: ModelTier, provider: object) -> None:
        self.providers[tier] = provider

    # ------------------------------------------------------------------ selection

    def choose(
        self,
        *,
        text: str = "",
        sensitivity: DataSensitivity = DataSensitivity.PRIVATE,
        needs_vision: bool = False,
        offline: bool = False,
        prefer: ModelTier | None = None,
    ) -> RouteDecision:
        reasons: list[str] = []

        if needs_vision and ModelTier.VISION in self.providers:
            tier = ModelTier.VISION
            reasons.append("vision input requires a vision model")
        elif prefer is not None:
            tier = prefer
            reasons.append(f"caller requested the {prefer} tier")
        elif _COMPLEX_MARKERS.search(text) or len(text) > 400:
            tier = ModelTier.REASONING
            reasons.append("task looks analytical")
        elif _TRIVIAL_MARKERS.match(text.strip()) or len(text) < 40:
            tier = ModelTier.FAST
            reasons.append("short, low-ambiguity request")
        else:
            tier = ModelTier.STANDARD
            reasons.append("default tier")

        # Privacy and connectivity can only pull the choice toward local, never away.
        if sensitivity >= DataSensitivity.SECRET:
            tier = ModelTier.LOCAL
            reasons.append("SECRET payloads never leave this machine")
        elif sensitivity >= DataSensitivity.HIGHLY_PRIVATE:
            tier = ModelTier.LOCAL
            reasons.append("HIGHLY_PRIVATE prefers local inference")
        elif offline or not self.allow_cloud:
            tier = ModelTier.LOCAL
            reasons.append("offline mode" if offline else "cloud disabled by configuration")

        provider = self._resolve(tier)
        if provider is None:
            provider = self._resolve(ModelTier.LOCAL)
            reasons.append(f"no provider registered for {tier}; fell back to LOCAL")
            tier = ModelTier.LOCAL
        if provider is None:
            raise ProviderError("no LLM provider is registered", tier=str(tier))

        if sensitivity >= DataSensitivity.SECRET and not getattr(provider, "local", False):
            raise PrivacyViolation(
                "refusing to send SECRET-classified content to a non-local model",
                provider=getattr(provider, "name", "unknown"),
            )
        return RouteDecision(
            tier=tier, provider_name=getattr(provider, "name", "?"), reasons=tuple(reasons)
        )

    def _resolve(self, tier: ModelTier, *, now: datetime | None = None) -> object | None:
        provider = self.providers.get(tier)
        if provider is None:
            return None
        if self.parked(getattr(provider, "name", ""), now=now):
            return None
        return provider

    def parked(self, name: str, *, now: datetime | None = None) -> bool:
        """Whether the breaker is currently holding this provider out of selection.

        Holding, not banning. After the cooldown the provider is offered again and one call
        decides: succeed and the counter clears, fail and it is parked for another cooldown.
        """
        if self._breaker.get(name, 0) < BREAKER_TRIP:
            return False
        tripped = self._tripped_at.get(name)
        now = now or datetime.now(UTC)
        if tripped is not None and now - tripped >= BREAKER_COOLDOWN:
            # Cooldown served. Offer it one attempt rather than declaring it healthy — the
            # attempt is the only evidence either way.
            self._breaker[name] = BREAKER_TRIP - 1
            self._tripped_at.pop(name, None)
            log.info("model_breaker_half_open", provider=name)
            return False
        return True

    # ------------------------------------------------------------------ execution

    async def complete(
        self,
        request: LLMRequest,
        *,
        offline: bool = False,
        prefer: ModelTier | None = None,
        task_id: UUID | None = None,
        agent: str = "",
    ) -> tuple[LLMResponse, RouteDecision]:
        text = " ".join(m.content for m in request.messages if m.role == "user")
        decision = self.choose(
            text=text,
            sensitivity=request.sensitivity,
            offline=offline,
            prefer=prefer or request.tier,
        )
        decision = self._within_cap(decision)
        provider = self.providers[decision.tier]
        name = getattr(provider, "name", "?")
        request = self._redact(request, provider=name)
        try:
            response = await provider.complete(request)  # type: ignore[attr-defined]
            self._breaker[name] = 0
            self._tripped_at.pop(name, None)
            self._meter(response, decision, task_id=task_id, agent=agent)
            return response, decision
        except Exception as exc:
            self._trip(name, exc)
            local = self.providers.get(ModelTier.LOCAL)
            if local is None or local is provider:
                raise
            if self.metrics is not None:
                self.metrics.inc("thursday_model_fallbacks_total", reason="provider_failed")
            response = await local.complete(request)  # type: ignore[attr-defined]
            degraded = RouteDecision(
                tier=ModelTier.LOCAL,
                provider_name=getattr(local, "name", "?"),
                reasons=(*decision.reasons, f"{name} failed, degraded to local"),
                fallback_from=name,
            )
            self._meter(response, degraded, task_id=task_id, agent=agent)
            return response, degraded

    # ------------------------------------------------------------------ money

    def _within_cap(self, decision: RouteDecision) -> RouteDecision:
        """Send a paid call to the local model once the ceiling is reached.

        Degrade rather than refuse. A cap that stops Thursday working is worse than the
        overspend it prevents, and the owner cannot tell that kind of outage from a broken
        assistant — so they fix it by removing the cap, which is the opposite of the point.

        Refusing is the last resort, for a deployment with no local model to fall back to.
        Then it says so as a budget problem rather than failing as a model error, because
        "the daily cap is reached" and "the provider is down" want different responses.
        """
        if self.meter is None or decision.tier is ModelTier.LOCAL:
            return decision
        verdict = self.meter.check()
        if verdict.allowed:
            return decision

        local = self.providers.get(ModelTier.LOCAL)
        if local is None:
            raise BudgetExceeded(
                verdict.reason + ", and there is no local model to fall back to",
                period=verdict.period,
                spent=round(verdict.spent, 4),
                cap=verdict.cap,
            )
        log.warning("model_cost_capped", period=verdict.period, spent=round(verdict.spent, 2))
        if self.metrics is not None:
            self.metrics.inc("thursday_model_fallbacks_total", reason="cost_cap")
        return RouteDecision(
            tier=ModelTier.LOCAL,
            provider_name=getattr(local, "name", "?"),
            reasons=(*decision.reasons, verdict.reason + "; using the local model"),
            fallback_from=decision.provider_name,
        )

    def _redact(self, request: LLMRequest, *, provider: str) -> LLMRequest:
        """Strip credential-shaped material out of a prompt before it is sent.

        Applied to every call, local included. A secret does not stop being one because the
        model runs on this machine, and the prompt reaches a log line either way.

        The trade-off is the redactor's own and is the right way round: a false positive
        costs a redacted string in one prompt, and a false negative costs a credential that
        is now in somebody else's training pipeline. What gets logged is the *name* of the
        pattern that matched, never the value — a log line that reports what it redacted has
        not redacted it.
        """
        if self.redactor is None:
            return request

        messages = []
        hits: set[str] = set()
        for message in request.messages:
            result = self.redactor.redact(message.content)
            hits.update(result.hits)
            messages.append(
                message if result.clean else message.model_copy(update={"content": result.text})
            )
        if not hits:
            return request

        log.warning("prompt_redacted", provider=provider, patterns=sorted(hits))
        if self.metrics is not None:
            for pattern in hits:
                self.metrics.inc("thursday_prompt_redactions_total", pattern=pattern)
        return request.model_copy(update={"messages": messages})

    def _meter(
        self,
        response: LLMResponse,
        decision: RouteDecision,
        *,
        task_id: UUID | None,
        agent: str,
    ) -> None:
        """Record what the call cost. Here, not at the call sites.

        The call sites were the problem: spend was counted where an agent chose to count it,
        which missed the reasoning pass and the supervision pass — the two calls every single
        turn makes. Metering something a caller opts into measures the callers who opted in.
        """
        if self.meter is None:
            return
        self.meter.record(
            provider=decision.provider_name,
            tier=str(decision.tier),
            tokens_in=response.tokens_in,
            tokens_out=response.tokens_out,
            usd=response.cost_usd,
            task_id=task_id,
            agent=agent,
        )

    def _trip(self, name: str, exc: Exception) -> None:
        self._breaker[name] = self._breaker.get(name, 0) + 1
        if self._breaker[name] >= BREAKER_TRIP and name not in self._tripped_at:
            self._tripped_at[name] = datetime.now(UTC)
            log.warning("model_breaker_open", provider=name, cooldown_s=BREAKER_COOLDOWN.seconds)
        log.warning("model_failed", provider=name, error=str(exc), failures=self._breaker[name])

    async def health(self) -> list[HealthStatus]:
        out: list[HealthStatus] = []
        for tier, provider in self.providers.items():
            status = await provider.health()  # type: ignore[attr-defined]
            status.detail = f"[{tier}] {status.detail}".strip()
            out.append(status)
        return out
