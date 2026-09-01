"""Model Router (§33) with the privacy hard stop from §34.

Selection is complexity × privacy × latency × cost × availability. The privacy rule is not
a preference: a SECRET payload cannot reach a non-local provider, and the router raises
rather than degrading quietly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from thursday.core.logging import get_logger
from thursday.shared.enums import DataSensitivity, ModelTier
from thursday.shared.errors import PrivacyViolation, ProviderError
from thursday.shared.models import HealthStatus, LLMRequest, LLMResponse

log = get_logger(__name__)

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
    _breaker: dict[str, int] = field(default_factory=dict)

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

    def _resolve(self, tier: ModelTier) -> object | None:
        provider = self.providers.get(tier)
        if provider is None:
            return None
        # Circuit breaker: three consecutive failures parks a provider for this process.
        if self._breaker.get(getattr(provider, "name", ""), 0) >= 3:
            return None
        return provider

    # ------------------------------------------------------------------ execution

    async def complete(
        self, request: LLMRequest, *, offline: bool = False, prefer: ModelTier | None = None
    ) -> tuple[LLMResponse, RouteDecision]:
        text = " ".join(m.content for m in request.messages if m.role == "user")
        decision = self.choose(
            text=text,
            sensitivity=request.sensitivity,
            offline=offline,
            prefer=prefer or request.tier,
        )
        provider = self.providers[decision.tier]
        name = getattr(provider, "name", "?")
        try:
            response = await provider.complete(request)  # type: ignore[attr-defined]
            self._breaker[name] = 0
            return response, decision
        except Exception as exc:
            self._breaker[name] = self._breaker.get(name, 0) + 1
            log.warning("model_failed", provider=name, error=str(exc), failures=self._breaker[name])
            local = self.providers.get(ModelTier.LOCAL)
            if local is None or local is provider:
                raise
            response = await local.complete(request)  # type: ignore[attr-defined]
            return response, RouteDecision(
                tier=ModelTier.LOCAL,
                provider_name=getattr(local, "name", "?"),
                reasons=(*decision.reasons, f"{name} failed, degraded to local"),
                fallback_from=name,
            )

    async def health(self) -> list[HealthStatus]:
        out: list[HealthStatus] = []
        for tier, provider in self.providers.items():
            status = await provider.health()  # type: ignore[attr-defined]
            status.detail = f"[{tier}] {status.detail}".strip()
            out.append(status)
        return out
