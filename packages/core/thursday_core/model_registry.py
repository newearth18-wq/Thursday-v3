"""Which model exists on which machine (ADDENDUM §5, §48–§50) — Sprint 55.

Sprint 54 taught nodes to *report* what they hold. This remembers it, so routing has
something to consult that survives a restart and does not depend on every machine being
awake at the moment somebody asks a question.

One rule shapes the whole class, and it is not about schemas.

**A node's report is an observation. The owner's correction is a decision.**

What a node reports is derived, and part of it is derived from a *guess*: no runtime says
what a model is for, so Sprint 54 reads the name — `llava` means vision, `nomic-embed` means
embeddings. The guess is usually right and sometimes wrong, and a private build called
`house-model-v3` is unreadable. So the owner can correct it.

The correction then has to survive the next reconnect. If re-discovery overwrites it, the fix
lasts until the node restarts — which is worse than not offering the fix at all, because the
owner watched it work and has no reason to check it again. So observations and corrections
are stored in separate fields and merged on read, the same shape §110 uses for memory: what
a source *reports* can never redefine what the owner has *said*.

The registry holds no secrets and no endpoints for anything but a device it already knows.
An entry here is not authority to call anything — that is the compute router's decision, and
the router asks the permission and privacy layers, not this class.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from thursday_shared.compute import ModelDescriptor, ModelKind, RuntimeKind

from thursday_core.logging import get_logger

log = get_logger(__name__)

#: Namespace for deriving a stable id from (device, runtime, model name). Deterministic on
#: purpose: a node that reconnects must land on the row it had before, and matching by
#: lookup-then-insert races with itself when two reports arrive together.
_NAMESPACE = uuid.UUID("6f2a1d0c-9b3e-4f7a-8c21-5d4e7a9b0c13")


def model_id_for(device_id: UUID | None, runtime: RuntimeKind, name: str) -> UUID:
    """The identity of one model on one runtime on one machine.

    All three parts matter. The same model name on two machines is two entries, because
    routing chooses between them; the same name on two runtimes on one machine is also two,
    because they load, unload and fail independently.
    """
    return uuid.uuid5(_NAMESPACE, f"{device_id or 'core'}|{runtime}|{name}")


@dataclass
class RegisteredModel:
    """One model, as the registry knows it: what was observed, and what the owner said."""

    id: UUID
    device_id: UUID | None
    #: Exactly what the node reported, untouched by corrections. Kept separately so a
    #: correction can be undone, and so "what does the machine actually claim" stays
    #: answerable after somebody has overridden half of it.
    observed: ModelDescriptor
    online: bool = False
    first_seen: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_seen: datetime = field(default_factory=lambda: datetime.now(UTC))

    # ---- the owner's decisions, which survive re-discovery ----
    kind_override: ModelKind | None = None
    #: None means "no opinion, use it". False is an explicit refusal and outranks every
    #: routing preference — an owner who disables a model has given an instruction, not a
    #: hint.
    enabled_override: bool | None = None
    note: str = ""

    @property
    def name(self) -> str:
        return self.observed.name

    @property
    def kind(self) -> ModelKind:
        return self.kind_override or self.observed.kind

    @property
    def enabled(self) -> bool:
        return True if self.enabled_override is None else self.enabled_override

    @property
    def descriptor(self) -> ModelDescriptor:
        """The model as the router should see it: observation with corrections applied."""
        if self.kind_override is None:
            return self.observed
        # `model_copy`, not `dataclasses.replace`: ModelDescriptor is a Pydantic model and
        # this class is a dataclass, which is an easy pair of shapes to confuse in one file.
        return self.observed.model_copy(update={"kind": self.kind_override})

    @property
    def capability(self) -> str:
        return self.descriptor.capability

    @property
    def usable(self) -> bool:
        """Online, and not switched off. Both halves, and neither implies the other."""
        return self.online and self.enabled

    def row(self) -> dict:
        return {
            "id": self.id,
            "device_id": self.device_id,
            "model_name": self.observed.name,
            "runtime": str(self.observed.runtime),
            "model_type": str(self.observed.kind),
            "kind_override": str(self.kind_override) if self.kind_override else None,
            "enabled_override": self.enabled_override,
            "context_length": self.observed.context_length,
            "size_bytes": self.observed.size_bytes,
            "required_ram": self.observed.required_ram_bytes,
            "required_vram": self.observed.required_vram_bytes,
            "tokens_per_second": self.observed.tokens_per_second,
            "online": self.online,
            "note": self.note,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
        }

    @classmethod
    def from_row(cls, row: dict) -> RegisteredModel:
        observed = ModelDescriptor(
            name=str(row["model_name"]),
            kind=ModelKind(row.get("model_type") or ModelKind.LLM),
            runtime=RuntimeKind(row.get("runtime") or RuntimeKind.NONE),
            context_length=int(row.get("context_length") or 0),
            size_bytes=int(row.get("size_bytes") or 0),
            required_ram_bytes=int(row.get("required_ram") or 0),
            required_vram_bytes=int(row.get("required_vram") or 0),
            tokens_per_second=float(row.get("tokens_per_second") or 0.0),
        )
        override = row.get("kind_override")
        return cls(
            id=_as_uuid(row["id"]),
            device_id=_as_uuid(row["device_id"]) if row.get("device_id") else None,
            observed=observed,
            # Never restored as online. A model is online because a node said so on *this*
            # run; a registry that loaded "online: true" from last week would offer the
            # router a machine that has been switched off since.
            online=False,
            kind_override=ModelKind(override) if override else None,
            enabled_override=row.get("enabled_override"),
            note=str(row.get("note") or ""),
            first_seen=_as_datetime(row.get("first_seen")),
            last_seen=_as_datetime(row.get("last_seen")),
        )


class ModelRegistry:
    """Every model Thursday knows about, on every machine it knows about."""

    def __init__(self, *, repository: Any = None) -> None:
        self._models: dict[UUID, RegisteredModel] = {}
        from thursday_core.persistence import NullRepository

        self._repository = repository or NullRepository()

    # ------------------------------------------------------------------ observation

    async def observe(
        self, device_id: UUID | None, models: list[ModelDescriptor], *, online: bool = True
    ) -> list[RegisteredModel]:
        """Record what a machine says it holds.

        Corrections are preserved: only the observation is replaced. A node that reconnects
        with the same inventory leaves an owner's override exactly where it was, which is the
        difference between a correction and a suggestion.

        Models that were on this device and are no longer reported go **offline** rather than
        being deleted. A model that vanished because Ollama was restarted mid-scan is not a
        model the owner uninstalled, and deleting it would take its corrections with it.
        """
        now = datetime.now(UTC)
        seen: list[RegisteredModel] = []
        for descriptor in models:
            key = model_id_for(device_id, descriptor.runtime, descriptor.name)
            existing = self._models.get(key)
            if existing is None:
                entry = RegisteredModel(
                    id=key, device_id=device_id, observed=descriptor, online=online
                )
            else:
                entry = existing
                entry.observed = descriptor
                entry.online = online
                entry.last_seen = now
            self._models[key] = entry
            seen.append(entry)
            await self._save(entry)

        reported = {m.id for m in seen}
        for entry in self._models.values():
            if entry.device_id == device_id and entry.id not in reported and entry.online:
                entry.online = False
                await self._save(entry)

        log.info("models_observed", device_id=str(device_id), count=len(seen), online=online)
        return seen

    async def device_offline(self, device_id: UUID) -> int:
        """Every model on a machine that has disconnected is unreachable, not gone."""
        changed = 0
        for entry in self._models.values():
            if entry.device_id == device_id and entry.online:
                entry.online = False
                await self._save(entry)
                changed += 1
        return changed

    # ------------------------------------------------------------------ the owner's say

    async def set_kind(self, model_id: UUID, kind: ModelKind | None) -> RegisteredModel:
        """Correct what a model is for, or clear the correction and trust the guess again."""
        entry = self._require(model_id)
        entry.kind_override = kind
        await self._save(entry)
        log.info(
            "model_kind_corrected",
            model=entry.name,
            guessed=str(entry.observed.kind),
            corrected_to=str(kind) if kind else None,
        )
        return entry

    async def set_enabled(self, model_id: UUID, enabled: bool | None) -> RegisteredModel:
        entry = self._require(model_id)
        entry.enabled_override = enabled
        await self._save(entry)
        log.info("model_enabled_set", model=entry.name, enabled=enabled)
        return entry

    # ------------------------------------------------------------------ questions

    def get(self, model_id: UUID) -> RegisteredModel | None:
        return self._models.get(model_id)

    def all(self, *, include_offline: bool = True) -> list[RegisteredModel]:
        rows = list(self._models.values())
        if not include_offline:
            rows = [m for m in rows if m.usable]
        return sorted(rows, key=lambda m: (str(m.device_id or ""), m.name))

    def on_device(self, device_id: UUID | None) -> list[RegisteredModel]:
        return [m for m in self.all() if m.device_id == device_id]

    def for_capability(self, capability: str, *, usable_only: bool = True) -> list[RegisteredModel]:
        """Every model that can do this, newest information first.

        Capability rather than kind, so callers ask the same question the device layer asks
        (`ai.vision`) rather than translating between two vocabularies at each call site.
        """
        rows = [m for m in self._models.values() if m.capability == capability]
        if usable_only:
            rows = [m for m in rows if m.usable]
        return sorted(rows, key=lambda m: m.name)

    def devices_with(self, capability: str) -> set[UUID]:
        return {m.device_id for m in self.for_capability(capability) if m.device_id is not None}

    def health(self) -> dict:
        return {
            "models": len(self._models),
            "online": sum(1 for m in self._models.values() if m.online),
            "disabled": sum(1 for m in self._models.values() if m.enabled_override is False),
            "corrected": sum(1 for m in self._models.values() if m.kind_override is not None),
        }

    # ------------------------------------------------------------------ persistence

    async def restore(self) -> int:
        rows = await self._repository.load()
        restored = 0
        for row in rows:
            try:
                entry = RegisteredModel.from_row(row)
            except (KeyError, TypeError, ValueError) as exc:
                log.warning("model_row_unreadable", error=str(exc))
                continue
            self._models[entry.id] = entry
            restored += 1
        if restored:
            log.info("models_restored", models=restored)
        return restored

    async def _save(self, entry: RegisteredModel) -> None:
        await self._repository.put(entry.row())

    def _require(self, model_id: UUID) -> RegisteredModel:
        entry = self._models.get(model_id)
        if entry is None:
            from thursday_shared.errors import ThursdayError

            raise ThursdayError("unknown model", model_id=str(model_id))
        return entry

    def __len__(self) -> int:
        return len(self._models)


def _as_uuid(value: Any) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


def _as_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if value:
        parsed = datetime.fromisoformat(str(value))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return datetime.now(UTC)
