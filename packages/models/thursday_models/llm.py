"""LLM adapters.

Three backends behind one port:

* ``RuleBasedLLM`` — no network, no model, fully deterministic. It is the offline tier
  (§58) and the default in tests, so the whole system is exercisable with no credentials.
* ``OllamaLLM`` — a local model, for HIGHLY_PRIVATE work that must not leave the machine.
* ``AnthropicLLM`` — the cloud tier, reached through the secret vault so no API key is ever
  present in a prompt, a log line, or the process's own call sites.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from typing import Any, ClassVar

from thursday_core.logging import get_logger
from thursday_shared.enums import ModelTier
from thursday_shared.errors import ProviderError
from thursday_shared.models import HealthStatus, LLMRequest, LLMResponse

log = get_logger(__name__)


class RuleBasedLLM:
    """A small, honest, offline responder.

    It does not pretend to reason. When it cannot answer, it says so — which is exactly the
    behaviour §73 asks for, and better than a cloud model guessing while offline.
    """

    name = "rule-based"
    tier = ModelTier.LOCAL
    local = True

    async def complete(self, request: LLMRequest) -> LLMResponse:
        user_text = next((m.content for m in reversed(request.messages) if m.role == "user"), "")
        if request.json_schema:
            payload = self._structured(request, user_text)
            return LLMResponse(
                text=json.dumps(payload, ensure_ascii=False),
                model=self.name,
                structured=payload,
                tokens_in=len(user_text) // 4,
                tokens_out=32,
            )
        return LLMResponse(
            text=self._chat(user_text),
            model=self.name,
            tokens_in=len(user_text) // 4,
            tokens_out=24,
        )

    async def stream(self, request: LLMRequest) -> AsyncIterator[str]:
        response = await self.complete(request)
        for word in response.text.split(" "):
            yield word + " "

    async def health(self) -> HealthStatus:
        return HealthStatus(name=self.name, ok=True, detail="offline rule-based tier")

    # ------------------------------------------------------------------ internals

    def _structured(self, request: LLMRequest, user_text: str) -> dict:
        """Fill the requested schema conservatively rather than inventing content."""
        title = str(request.json_schema.get("title", "")) if request.json_schema else ""
        if title == "Intent":
            return {
                "kind": "UNKNOWN",
                "objective": user_text[:200],
                "entities": {},
                "target_device": None,
                "needs_plan": False,
                "confidence": 0.3,
                "rationale": "offline rule-based tier could not classify this intent",
                "direct_answer": self._chat(user_text),
            }
        if title == "Verification":
            # Never fabricate a pass: an offline verifier escalates instead.
            return {
                "verdict": "ESCALATE",
                "checks": [],
                "critique": "no reasoning model available to judge this output",
                "confidence": 0.2,
            }
        return {}

    def _chat(self, user_text: str) -> str:
        lowered = user_text.lower()
        thai = any("฀" <= ch <= "๿" for ch in user_text)
        # \b does not delimit Thai script, so the Thai greeting is matched literally.
        if "สวัสดี" in user_text or re.search(r"\b(hello|hi|hey)\b", lowered):
            return "สวัสดีครับ พร้อมทำงานแล้ว" if thai else "Ready when you are."
        if thai:
            return (
                "ตอนนี้ผมทำงานในโหมดออฟไลน์ จึงตอบคำถามเชิงวิเคราะห์ไม่ได้ "
                "แต่ยังสั่งงานอุปกรณ์ ค้นความจำ และจัดการไฟล์ได้ตามปกติ"
            )
        return (
            "I'm running offline, so I can't reason about that one. "
            "Device control, memory search and file work still function normally."
        )


class MockLLM:
    """PART 89. A deterministic LLM for tests.

    Scripted responses keyed by a substring of the prompt, a default for everything else,
    and a call log. No paid API is ever reached from the main suite — a test that costs
    money is a test that stops being run.

    Distinct from ``RuleBasedLLM``: that one is a *product* feature (the offline tier a
    real owner uses), this one is test scaffolding whose answers a test controls.
    """

    name = "mock"
    tier = ModelTier.LOCAL
    local = True

    def __init__(
        self,
        responses: dict[str, str] | None = None,
        *,
        default: str = "mock response",
        structured: dict[str, dict] | None = None,
    ) -> None:
        #: prompt substring → reply.
        self.responses = responses or {}
        #: prompt substring → structured payload, for schema-shaped requests.
        self.structured = structured or {}
        self.default = default
        self.calls: list[LLMRequest] = []

    def _match(self, table: dict[str, Any], text: str) -> Any:
        for needle, value in table.items():
            if needle.lower() in text.lower():
                return value
        return None

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.calls.append(request)
        text = " ".join(m.content for m in request.messages)

        if request.json_schema:
            payload = self._match(self.structured, text)
            if payload is None:
                title = str(request.json_schema.get("title", ""))
                # Never fabricate a verdict: an unscripted verification escalates, exactly
                # as the offline tier does.
                payload = (
                    {
                        "verdict": "ESCALATE",
                        "critique": "no scripted verification",
                        "confidence": 0.2,
                    }
                    if title == "Verification"
                    else {"kind": "UNKNOWN", "objective": text[:120], "confidence": 0.3}
                )
            return LLMResponse(
                text=json.dumps(payload, ensure_ascii=False),
                model=self.name,
                structured=payload,
                tokens_in=len(text) // 4,
                tokens_out=24,
            )

        return LLMResponse(
            text=self._match(self.responses, text) or self.default,
            model=self.name,
            tokens_in=len(text) // 4,
            tokens_out=16,
        )

    async def stream(self, request: LLMRequest) -> AsyncIterator[str]:
        response = await self.complete(request)
        for word in response.text.split(" "):
            yield word + " "

    async def health(self) -> HealthStatus:
        return HealthStatus(name=self.name, ok=True, detail=f"{len(self.calls)} calls recorded")

    def reset(self) -> None:
        self.calls.clear()


class OllamaLLM:
    """Local model over Ollama's HTTP API."""

    tier = ModelTier.LOCAL
    local = True

    def __init__(self, url: str, model: str, *, timeout: float = 120.0) -> None:
        self.url = url.rstrip("/")
        self.model = model
        self.name = f"ollama:{model}"
        self._timeout = timeout

    async def complete(self, request: LLMRequest) -> LLMResponse:
        import httpx

        payload = {
            "model": self.model,
            "messages": [m.model_dump() for m in request.messages],
            "stream": False,
            "options": {"temperature": request.temperature, "num_predict": request.max_tokens},
        }
        if request.json_schema:
            payload["format"] = "json"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(f"{self.url}/api/chat", json=payload)
                response.raise_for_status()
                body = response.json()
        except Exception as exc:
            raise ProviderError(f"ollama request failed: {exc}", provider=self.name) from exc

        text = body.get("message", {}).get("content", "")
        return LLMResponse(
            text=text,
            model=self.name,
            structured=_safe_json(text) if request.json_schema else None,
            tokens_in=body.get("prompt_eval_count", 0),
            tokens_out=body.get("eval_count", 0),
        )

    async def stream(self, request: LLMRequest) -> AsyncIterator[str]:
        import httpx

        payload = {
            "model": self.model,
            "messages": [m.model_dump() for m in request.messages],
            "stream": True,
        }
        async with (
            httpx.AsyncClient(timeout=self._timeout) as client,
            client.stream("POST", f"{self.url}/api/chat", json=payload) as response,
        ):
            async for line in response.aiter_lines():
                if not line.strip():
                    continue
                chunk = json.loads(line)
                if piece := chunk.get("message", {}).get("content"):
                    yield piece

    async def health(self) -> HealthStatus:
        import httpx

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self.url}/api/tags")
                return HealthStatus(name=self.name, ok=response.status_code == 200)
        except Exception as exc:
            return HealthStatus(name=self.name, ok=False, detail=str(exc))


class AnthropicLLM:
    """Cloud tier. The API key is materialised only inside ``vault.use`` (§35, T2)."""

    local = False

    #: USD per million tokens (input, output) — used by the cost side of the model router.
    PRICES: ClassVar[dict[str, tuple[float, float]]] = {
        "claude-haiku-4-5-20251001": (1.0, 5.0),
        "claude-sonnet-5": (3.0, 15.0),
        "claude-opus-5": (15.0, 75.0),
    }

    def __init__(
        self, model: str, vault: object, key_handle: str, *, tier: ModelTier = ModelTier.STANDARD
    ) -> None:
        self.model = model
        self.name = f"anthropic:{model}"
        self.tier = tier
        self._vault = vault
        self._key_handle = key_handle

    async def complete(self, request: LLMRequest) -> LLMResponse:
        import httpx

        system = "\n\n".join(m.content for m in request.messages if m.role == "system")
        messages = [m.model_dump() for m in request.messages if m.role != "system"]
        if request.json_schema:
            messages.append(
                {
                    "role": "assistant",
                    "content": "{",  # prefill nudges the model into a JSON object
                }
            )

        async def call(api_key: str) -> dict:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": api_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": self.model,
                        "max_tokens": request.max_tokens,
                        "temperature": request.temperature,
                        "system": system or None,
                        "messages": messages,
                    },
                )
                response.raise_for_status()
                return response.json()

        try:
            body = await self._vault.use(self._key_handle, call)  # type: ignore[attr-defined]
        except Exception as exc:
            raise ProviderError(f"anthropic request failed: {exc}", provider=self.name) from exc

        text = "".join(part.get("text", "") for part in body.get("content", []))
        if request.json_schema and not text.lstrip().startswith("{"):
            text = "{" + text
        usage = body.get("usage", {})
        tokens_in = int(usage.get("input_tokens", 0))
        tokens_out = int(usage.get("output_tokens", 0))
        price_in, price_out = self.PRICES.get(self.model, (0.0, 0.0))
        return LLMResponse(
            text=text,
            model=self.name,
            structured=_safe_json(text) if request.json_schema else None,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=(tokens_in * price_in + tokens_out * price_out) / 1_000_000,
        )

    async def stream(self, request: LLMRequest) -> AsyncIterator[str]:
        response = await self.complete(request)
        yield response.text

    async def health(self) -> HealthStatus:
        has_key = await self._vault.has(self._key_handle)  # type: ignore[attr-defined]
        return HealthStatus(
            name=self.name,
            ok=bool(has_key),
            detail="ok" if has_key else f"no secret registered for {self._key_handle!r}",
        )


def _safe_json(text: str) -> dict | None:
    """Extract the first JSON object in a response, tolerating prose around it."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n|\n```$", "", text)
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return None
        try:
            parsed = json.loads(match.group(0))
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None
