"""Personal knowledge graph (§10).

Entities and relationships extracted from tasks, documents and conversation. Kept small and
explicit: the value is in answering "which file did I use in the last meeting with this
person", not in modelling the world.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from thursday_shared.ids import new_id
from thursday_shared.models import utcnow

ENTITY_KINDS = (
    "person",
    "project",
    "task",
    "document",
    "event",
    "device",
    "decision",
    "location",
    "skill",
    "object",
    "organization",
)


@dataclass
class Entity:
    id: UUID = field(default_factory=new_id)
    kind: str = "object"
    name: str = ""
    aliases: set[str] = field(default_factory=set)
    attributes: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utcnow)

    def matches(self, text: str) -> bool:
        lowered = text.lower()
        return lowered == self.name.lower() or lowered in {a.lower() for a in self.aliases}


@dataclass
class Relationship:
    id: UUID = field(default_factory=new_id)
    src: UUID = field(default_factory=new_id)
    dst: UUID = field(default_factory=new_id)
    kind: str = "related_to"
    weight: float = 1.0
    attributes: dict[str, Any] = field(default_factory=dict)
    observed_at: datetime = field(default_factory=utcnow)
    source: str = "inference"


class KnowledgeGraph:
    def __init__(self) -> None:
        self._entities: dict[UUID, Entity] = {}
        self._relationships: list[Relationship] = []
        self._out: dict[UUID, list[Relationship]] = defaultdict(list)
        self._in: dict[UUID, list[Relationship]] = defaultdict(list)

    def upsert_entity(self, *, kind: str, name: str, **attributes: Any) -> Entity:
        existing = self.find(name, kind=kind)
        if existing is not None:
            existing.attributes.update(attributes)
            return existing
        entity = Entity(kind=kind, name=name, attributes=attributes)
        self._entities[entity.id] = entity
        return entity

    def find(self, name: str, *, kind: str | None = None) -> Entity | None:
        for entity in self._entities.values():
            if (kind is None or entity.kind == kind) and entity.matches(name):
                return entity
        return None

    def get(self, entity_id: UUID) -> Entity | None:
        return self._entities.get(entity_id)

    def relate(
        self,
        src: Entity | UUID,
        dst: Entity | UUID,
        kind: str,
        *,
        source: str = "inference",
        weight: float = 1.0,
        **attributes: Any,
    ) -> Relationship:
        src_id = src.id if isinstance(src, Entity) else src
        dst_id = dst.id if isinstance(dst, Entity) else dst
        rel = Relationship(
            src=src_id, dst=dst_id, kind=kind, weight=weight, source=source, attributes=attributes
        )
        self._relationships.append(rel)
        self._out[src_id].append(rel)
        self._in[dst_id].append(rel)
        return rel

    def neighbours(
        self, entity_id: UUID, *, kind: str | None = None
    ) -> list[tuple[Relationship, Entity]]:
        out = [
            (rel, self._entities[rel.dst])
            for rel in self._out.get(entity_id, [])
            if (kind is None or rel.kind == kind) and rel.dst in self._entities
        ]
        out += [
            (rel, self._entities[rel.src])
            for rel in self._in.get(entity_id, [])
            if (kind is None or rel.kind == kind) and rel.src in self._entities
        ]
        return out

    def traverse(
        self, start: UUID, *, hops: int = 2, target_kind: str | None = None
    ) -> list[tuple[Entity, int, list[str]]]:
        """Breadth-first walk. Returns (entity, distance, relationship path)."""
        seen = {start}
        queue: deque[tuple[UUID, int, list[str]]] = deque([(start, 0, [])])
        results: list[tuple[Entity, int, list[str]]] = []
        while queue:
            node, depth, path = queue.popleft()
            if depth >= hops:
                continue
            for rel, entity in self.neighbours(node):
                if entity.id in seen:
                    continue
                seen.add(entity.id)
                trail = [*path, rel.kind]
                if target_kind is None or entity.kind == target_kind:
                    results.append((entity, depth + 1, trail))
                queue.append((entity.id, depth + 1, trail))
        results.sort(key=lambda row: row[1])
        return results

    def stats(self) -> dict[str, int]:
        return {"entities": len(self._entities), "relationships": len(self._relationships)}
