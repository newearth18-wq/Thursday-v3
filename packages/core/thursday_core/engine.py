"""ThursdayCore — the request pipeline from PART 7.

    INPUT → NORMALIZE → AUTHENTICATE → LOAD WORLD STATE → LOAD MEMORY
          → LOAD DEVICE CONTEXT → BUILD CONTEXT → INTENT → PLANNING
          → PERMISSION CHECK → EXECUTION → VERIFICATION → MEMORY DECISION
          → WORLD STATE UPDATE → RESPONSE

One method owns the whole path, so the order cannot drift and the four steps that make this
an operating system rather than a chat loop — classification, authorisation, verification,
and the memory-write decision — cannot be skipped by a new code path.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from thursday_shared.enums import (
    DataSensitivity,
    IntentKind,
    MemoryLayer,
    MemorySource,
    TaskState,
)
from thursday_shared.errors import (
    ApprovalRequired,
    BudgetExceeded,
    DeviceUnavailable,
    PermissionDenied,
    PrivacyViolation,
)
from thursday_shared.ids import bind_trace_id
from thursday_shared.models import (
    Budget,
    Citation,
    ContextPackage,
    ConversationTurn,
    Event,
    GestureContext,
    MemoryWrite,
    ScreenContext,
    SelectionContext,
    ThursdayReply,
    ThursdayResponse,
    UserRequest,
)

from thursday_core.logging import get_logger

log = get_logger(__name__)


class ThursdayCore:
    """The one entry point. Everything the owner says arrives through ``handle_request``."""

    def __init__(self, container: Any) -> None:
        self.c = container

    # ------------------------------------------------------------------ PART 6 entry point

    async def handle_request(self, request: UserRequest) -> ThursdayResponse:
        """PART 6/7. One request in, one response out, whatever the modality.

        NORMALIZE happens here: audio becomes a transcript before anything else looks at
        the request, so the rest of the pipeline never has to care how the words arrived.
        """
        text = request.text
        if not text and request.audio is not None:
            text = await self._transcribe(request.audio)

        if not text.strip():
            reply = self.c.composer.clarify(
                _clarify_question(self.c.composer.language_of(request.text)),
                language=self.c.composer.language_of(request.text),
            )
            return ThursdayResponse.from_reply(reply, conversation_id=request.conversation_id)

        reply = await self.handle_turn(
            session_id=request.conversation_id,
            text=text,
            device_id=request.device_id,
            modality=request.modality,
            screen=request.screen_context,
            selection=request.selection_context,
            gesture=request.gesture_context,
            wait_for_approval=request.wait_for_approval,
        )
        task = self.c.tasks.get(reply.task_id) if reply.task_id else None
        return ThursdayResponse.from_reply(
            reply,
            conversation_id=request.conversation_id,
            status=task.status if task else None,
            voice=self.c.settings.voice_name,
            device_id=request.device_id,
        )

    async def _transcribe(self, audio: bytes) -> str:
        """Speech in, text out. The STT provider is a port; the core never knows which."""
        stt = getattr(self.c, "stt", None)
        if stt is None:
            log.warning("no_stt_provider_configured")
            return ""
        return await stt.transcribe(audio)

    # ------------------------------------------------------------------ the pipeline

    async def handle_turn(
        self,
        *,
        session_id: UUID,
        text: str,
        device_id: UUID | None = None,
        modality: str = "text",
        screen: ScreenContext | None = None,
        selection: SelectionContext | None = None,
        gesture: GestureContext | None = None,
        wait_for_approval: bool = False,
    ) -> ThursdayReply:
        trace_id = bind_trace_id()
        turn = ConversationTurn(
            session_id=session_id,
            role="user",
            text=text,
            device_id=device_id,
            modality=modality,  # type: ignore[arg-type]
        )
        self.c.context_engine.record_turn(turn)
        await self.c.bus.publish(
            Event(
                kind="conversation.turn.received",
                session_id=session_id,
                device_id=device_id,
                payload={"length": len(text), "modality": modality},
            )
        )

        language = self.c.composer.language_of(text)

        # 2–3. Context and privacy classification.
        context = await self.c.context_engine.build(
            turn,
            screen=screen,
            selection=selection,
            gesture=gesture,
            budget=Budget(
                usd=self.c.settings.default_task_budget_usd,
                seconds=self.c.settings.default_task_budget_seconds,
            ),
            offline=self.c.settings.offline,
        )

        # 4–5. Model routing happens inside the reasoning engine; understand the request.
        try:
            intent = await self.c.reasoning.understand(context)
        except PrivacyViolation as exc:
            return self._finish(
                session_id, self.c.composer.blocked(reason=exc.message, language=language)
            )

        log.info("intent", kind=str(intent.kind), confidence=intent.confidence, trace_id=trace_id)

        if intent.kind is IntentKind.STOP:
            return self._finish(session_id, await self._handle_stop(language))
        if intent.kind is IntentKind.CLARIFY or intent.confidence < 0.35:
            # A conversational answer is harmless even when the classifier was unsure;
            # only a low-confidence *action* has to become a question.
            if intent.direct_answer and intent.kind in (IntentKind.ANSWER, IntentKind.UNKNOWN):
                return self._finish(
                    session_id,
                    self.c.composer.answer(
                        intent.direct_answer,
                        language=language,
                        confidence=intent.confidence,
                        people_present=context.world.people_present,
                    ),
                )
            reply = self.c.composer.clarify(_clarify_question(language), language=language)
            reply.detail = intent.rationale or None
            return self._finish(session_id, reply)
        if intent.kind is IntentKind.STATUS and intent.entities.get("subject") == "device":
            return self._finish(session_id, self._device_status_reply(intent, context, language))

        # 6. Plan.
        plan = self.c.planner.plan(intent, context)

        if not plan.steps:
            reply = await self._answer_directly(intent, context, language)
            await self._remember(turn, intent, context, reply)
            return self._finish(session_id, reply)

        # 7–9. Authorise and execute; the orchestrator verifies each step.
        task = await self.c.tasks.create(
            title=intent.objective[:80],
            objective=intent.objective,
            session_id=session_id,
            origin_device_id=device_id,
            budget=context.budget,
        )
        await self.c.tasks.transition(task.id, TaskState.PLANNING)

        try:
            outcome = await self.c.orchestrator.run(
                task, plan, context, wait_for_approval=wait_for_approval
            )
        except ApprovalRequired as exc:
            approval = self.c.approvals.get(UUID(exc.details["approval_id"]))
            return self._finish(
                session_id, self.c.composer.needs_approval(approval, language=language)
            )
        except PermissionDenied as exc:
            await self.c.tasks.fail(task.id, exc.message)
            return self._finish(
                session_id, self.c.composer.blocked(reason=exc.message, language=language)
            )
        except DeviceUnavailable as exc:
            await self.c.tasks.fail(task.id, exc.message)
            question = exc.details.get("question") or exc.message
            return self._finish(session_id, self.c.composer.clarify(question, language=language))
        except BudgetExceeded as exc:
            await self.c.tasks.fail(task.id, exc.message)
            return self._finish(
                session_id, self.c.composer.failure(reason=exc.message, language=language)
            )

        if outcome.approval_required is not None:
            approval = self.c.approvals.get(UUID(outcome.approval_required.details["approval_id"]))
            return self._finish(
                session_id, self.c.composer.needs_approval(approval, language=language)
            )

        reply = await self._report(task, outcome, context, intent, language)
        await self._remember(turn, intent, context, reply, outcome=outcome)
        return self._finish(session_id, reply)

    # ------------------------------------------------------------------ reporting

    async def _report(
        self, task, outcome, context: ContextPackage, intent, language: str
    ) -> ThursdayReply:
        people = context.world.people_present
        summary = outcome.summary()

        if outcome.ok:
            verification = outcome.outcomes[-1].verification
            await self.c.tasks.transition(task.id, TaskState.VERIFYING)
            await self.c.tasks.complete(
                task.id,
                result={"summary": summary, "steps": [o.step.name for o in outcome.outcomes]},
                verification=verification,
            )
            answer = outcome.outcomes[-1].result.output.get("answer") if outcome.outcomes else None
            if answer:
                return self.c.composer.answer(
                    answer,
                    language=language,
                    confidence=verification.confidence,
                    citations=_citations(outcome),
                    people_present=people,
                )
            return self.c.composer.success(
                summary=summary,
                verification=verification,
                language=language,
                intent=intent,
                citations=_citations(outcome),
                people_present=people,
            )

        failure = outcome.first_failure()
        error = _explain_failure(failure)
        await self.c.tasks.fail(
            task.id, error, verification=failure.verification if failure else None
        )

        # A step that ran but could not be confirmed is reported as unverified, not failed —
        # the difference matters to the person deciding what to do next (§76).
        if failure and failure.verification and failure.result and failure.result.ok:
            return self.c.composer.unverified(
                summary=failure.result.summary or summary,
                verification=failure.verification,
                language=language,
                intent=intent,
                people_present=people,
            )
        if outcome.partial:
            return self.c.composer.partial_failure(
                done=summary,
                failed=failure.step.name if failure else "the remaining work",
                preserved="ผมเก็บผลลัพธ์ส่วนที่สำเร็จไว้แล้ว"
                if language == "th"
                else "I kept what did complete",
                language=language,
                people_present=people,
            )
        return self.c.composer.failure(reason=error, language=language, people_present=people)

    async def _answer_directly(
        self, intent, context: ContextPackage, language: str
    ) -> ThursdayReply:
        if intent.direct_answer:
            return self.c.composer.answer(
                intent.direct_answer,
                language=language,
                confidence=intent.confidence,
                people_present=context.world.people_present,
            )
        if intent.kind is IntentKind.STATUS:
            return self.c.composer.answer(
                self._status_text(context, language),
                language=language,
                confidence=0.95,
                people_present=context.world.people_present,
            )
        text = await self.c.reasoning.answer(context)
        citations = [
            Citation(source=m.source, ref=m.source_ref or str(m.layer), confidence=m.confidence)
            for m in context.memories[:3]
        ]
        return self.c.composer.answer(
            text,
            language=language,
            confidence=0.7,
            citations=citations,
            people_present=context.world.people_present,
        )

    def _device_status_reply(self, intent, context: ContextPackage, language: str) -> ThursdayReply:
        hint = intent.entities.get("device_name") or intent.target_device
        resolution = self.c.device_router.resolve(
            hint, world=context.world, origin_device_id=context.turn.device_id
        )
        if resolution.device is None:
            known = self.c.hub.find_by_name(str(hint or ""))
            if known is not None:
                text = (
                    f"{known.name} ออฟไลน์อยู่ ครั้งล่าสุดที่เชื่อมต่อคือ "
                    f"{known.last_seen_at:%Y-%m-%d %H:%M} UTC"
                    if language == "th"
                    else f"{known.name} is offline. Last seen {known.last_seen_at:%Y-%m-%d %H:%M} UTC."
                )
                return self.c.composer.answer(text, language=language, confidence=0.9)
            return self.c.composer.clarify(resolution.question(), language=language)

        device = resolution.device
        telemetry = device.telemetry
        bits = [f"{device.name} ออนไลน์" if language == "th" else f"{device.name} is online"]
        if telemetry and telemetry.battery_percent is not None:
            bits.append(f"battery {telemetry.battery_percent:.0f}%")
        if telemetry and telemetry.active_window:
            bits.append(f"active window: {telemetry.active_window}")
        return self.c.composer.answer(" · ".join(bits), language=language, confidence=0.95)

    def _status_text(self, context: ContextPackage, language: str) -> str:
        running = self.c.tasks.list(status=TaskState.RUNNING)
        waiting = self.c.tasks.list(status=TaskState.WAITING_APPROVAL)
        if not running and not waiting:
            return "ตอนนี้ไม่มีงานที่กำลังทำอยู่" if language == "th" else "Nothing is running right now."
        lines = []
        if running:
            lines.append(
                ("กำลังทำ: " if language == "th" else "Running: ")
                + ", ".join(f"{t.title} ({t.progress:.0%})" for t in running[:5])
            )
        if waiting:
            lines.append(
                ("รออนุมัติ: " if language == "th" else "Waiting for approval: ")
                + ", ".join(t.title for t in waiting[:5])
            )
        return "\n".join(lines)

    # ------------------------------------------------------------------ side paths

    async def _handle_stop(self, language: str) -> ThursdayReply:
        """§44 — stop speaking, pause agents, cancel what is safe to cancel."""
        cancelled = 0
        for task in self.c.tasks.list(status=TaskState.RUNNING):
            self.c.queue.cancel(task.id)
            await self.c.tasks.cancel(task.id, reason="owner said stop")
            cancelled += 1
        await self.c.bus.publish(
            Event(kind="conversation.interrupted", payload={"cancelled": cancelled})
        )
        state = (
            f"ยกเลิกงานที่กำลังทำ {cancelled} รายการ"
            if language == "th" and cancelled
            else (f"cancelled {cancelled} running task(s)" if cancelled else "")
        )
        return self.c.composer.stopped(language=language, state=state)

    async def _remember(self, turn, intent, context: ContextPackage, reply, outcome=None) -> None:
        """§7.3 — the write policy decides; this only proposes."""
        if context.sensitivity >= DataSensitivity.SECRET:
            return
        if outcome is not None and outcome.ok:
            await self.c.memory.write(
                MemoryWrite(
                    layer=MemoryLayer.EPISODIC,
                    content=f"{intent.objective} → {outcome.summary()}",
                    structured={
                        "outcome": "success",
                        "steps": [o.step.name for o in outcome.outcomes],
                        "verified": True,
                    },
                    importance=0.55,
                    confidence=0.9,
                    source=MemorySource.AGENT,
                    task_id=outcome.task.id,
                    sensitivity=context.sensitivity,
                )
            )
            # A verified multi-step success is a procedure worth reusing (§93).
            if len(outcome.outcomes) > 1:
                await self.c.memory.write(
                    MemoryWrite(
                        layer=MemoryLayer.PROCEDURAL,
                        key=intent.objective[:60],
                        content="; ".join(
                            f"{o.step.name}: {o.step.objective}" for o in outcome.outcomes
                        ),
                        structured={
                            "steps": [o.step.model_dump(mode="json") for o in outcome.outcomes]
                        },
                        importance=0.7,
                        confidence=0.85,
                        source=MemorySource.AGENT,
                        sensitivity=context.sensitivity,
                    )
                )
            return

        await self.c.memory.write(
            MemoryWrite(
                layer=MemoryLayer.SEMANTIC,
                content=turn.text,
                importance=0.4,
                confidence=0.8,
                source=MemorySource.USER,
                sensitivity=context.sensitivity,
            )
        )

    def _finish(self, session_id: UUID, reply: ThursdayReply) -> ThursdayReply:
        self.c.context_engine.record_turn(
            ConversationTurn(session_id=session_id, role="thursday", text=reply.text)
        )
        return reply


def _clarify_question(language: str) -> str:
    """The owner gets a question, not the classifier's internal rationale."""
    return (
        "ผมยังไม่แน่ใจว่าต้องการให้ทำอะไร ช่วยบอกให้ชัดขึ้นอีกนิดได้ไหม"
        if language == "th"
        else "I'm not sure what you'd like me to do — could you put it another way?"
    )


def _explain_failure(failure) -> str:
    """Say what actually went wrong, not that something did."""
    if failure is None:
        return "the work did not complete"
    if failure.error:
        return failure.error
    if failure.result is not None and failure.result.error:
        return failure.result.error
    if failure.verification is not None and failure.verification.critique:
        return failure.verification.critique
    return f"step {failure.step.name!r} did not complete"


def _citations(outcome) -> list[Citation]:
    citations: list[Citation] = []
    for step in outcome.outcomes:
        if step.result is None:
            continue
        for source in step.result.output.get("sources", [])[:5]:
            citations.append(Citation(source=MemorySource.AGENT, ref=str(source)))
    return citations
