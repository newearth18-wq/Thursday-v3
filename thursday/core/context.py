"""Context Engine (§13).

Assembles everything the Reasoning Engine is allowed to see for one turn — and nothing
more. Assembly is where privacy is decided: the classifier runs over the *assembled*
package, not just the utterance, because a harmless question about a screenshot of a
payslip is not a harmless payload.
"""

from __future__ import annotations

from uuid import UUID

from thursday.core.logging import get_logger
from thursday.security.privacy import PrivacyClassifier
from thursday.shared.enums import DataSensitivity, MemoryLayer
from thursday.shared.models import (
    Budget,
    ContextPackage,
    ConversationTurn,
    GestureContext,
    MemoryQuery,
    ProjectSummary,
    ScreenContext,
    SelectionContext,
)

log = get_logger(__name__)

#: Turns kept in the working window. Older turns live in episodic memory, not the prompt.
HISTORY_WINDOW = 12
#: Memories retrieved per turn, before token-budget trimming.
MEMORY_K = 6


class ContextEngine:
    def __init__(
        self,
        *,
        memory: object,
        world: object,
        hub: object,
        classifier: PrivacyClassifier | None = None,
        zones: object | None = None,
    ) -> None:
        self._memory = memory
        self._world = world
        self._hub = hub
        self._classifier = classifier or PrivacyClassifier()
        self._zones = zones
        self._sessions: dict[UUID, list[ConversationTurn]] = {}

    def record_turn(self, turn: ConversationTurn) -> None:
        history = self._sessions.setdefault(turn.session_id, [])
        history.append(turn)
        if len(history) > HISTORY_WINDOW * 3:
            del history[: len(history) - HISTORY_WINDOW * 3]

    def history(self, session_id: UUID, limit: int = HISTORY_WINDOW) -> list[ConversationTurn]:
        return self._sessions.get(session_id, [])[-limit:]

    async def build(
        self,
        turn: ConversationTurn,
        *,
        screen: ScreenContext | None = None,
        selection: SelectionContext | None = None,
        gesture: GestureContext | None = None,
        project: ProjectSummary | None = None,
        budget: Budget | None = None,
        offline: bool = False,
    ) -> ContextPackage:
        world = self._world.snapshot()  # type: ignore[attr-defined]
        devices = self._hub.online()  # type: ignore[attr-defined]
        world.online_devices = devices

        memories = await self._memory.recall(  # type: ignore[attr-defined]
            MemoryQuery(
                text=turn.text,
                layers=[
                    MemoryLayer.PREFERENCE,
                    MemoryLayer.SEMANTIC,
                    MemoryLayer.PROCEDURAL,
                    MemoryLayer.EPISODIC,
                ],
                project_id=project.id if project else None,
                k=MEMORY_K,
            )
        )

        sensitivity = self._classify(turn, screen, selection, memories)

        # A privacy zone can strip a surface out of the package entirely (§68) — the
        # cheapest way to guarantee it never reaches a model is for it not to be here.
        if self._zones is not None:
            if self._zones.forbids(
                "screen", device_id=turn.device_id, location=world.location_context
            ):
                screen = None
            if self._zones.forbids(
                "camera", device_id=turn.device_id, location=world.location_context
            ):
                gesture = None

        package = ContextPackage(
            turn=turn,
            history=self.history(turn.session_id),
            world=world,
            memories=memories,
            devices=devices,
            screen=screen,
            selection=selection,
            gesture=gesture,
            project=project,
            sensitivity=sensitivity,
            budget=budget or Budget(),
            offline=offline,
        )
        log.debug(
            "context_built",
            memories=len(memories),
            devices=len(devices),
            sensitivity=sensitivity.name,
            has_screen=screen is not None,
        )
        return package

    def _classify(
        self,
        turn: ConversationTurn,
        screen: ScreenContext | None,
        selection: SelectionContext | None,
        memories: list,
    ) -> DataSensitivity:
        parts = [turn.text]
        if screen and screen.visible_text:
            parts.append(screen.visible_text)
        if selection and selection.text:
            parts.append(selection.text)
        classification = self._classifier.classify(
            "\n".join(parts),
            hints={
                "has_screen_content": screen is not None,
                "file_paths": bool(selection and selection.file_paths),
            },
        )
        # A package is only as public as its most sensitive part, memories included.
        return max(
            [classification.level, *[m.sensitivity for m in memories]],
            default=classification.level,
        )
