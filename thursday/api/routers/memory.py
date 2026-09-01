"""Memory, conflict and knowledge-graph endpoints (§7, §10, §11)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from thursday.api.deps import get_container
from thursday.api.schemas import MemoryWriteRequest
from thursday.core.container import Container
from thursday.shared.enums import MemoryLayer, MemorySource
from thursday.shared.models import MemoryQuery, MemoryWrite

router = APIRouter(tags=["memory"])


@router.get("/memory/search")
async def search(
    q: str = "",
    layer: MemoryLayer | None = None,
    project_id: UUID | None = None,
    k: int = 8,
    min_confidence: float = 0.0,
    c: Container = Depends(get_container),
) -> dict:
    records = await c.memory.recall(
        MemoryQuery(
            text=q,
            layers=[layer] if layer else [],
            project_id=project_id,
            k=k,
            min_confidence=min_confidence,
        )
    )
    return {"memories": [r.model_dump(mode="json", exclude={"embedding"}) for r in records]}


@router.post("/memory")
async def write(request: MemoryWriteRequest, c: Container = Depends(get_container)) -> dict:
    record = await c.memory.write(
        MemoryWrite(
            layer=request.layer,
            content=request.content,
            key=request.key,
            importance=request.importance,
            project_id=request.project_id,
            structured=request.structured,
            source=MemorySource.USER,
            confidence=0.95,
        )
    )
    if record is None:
        # An honest refusal beats a silent no-op: the write policy declined it (§7.3).
        _allowed, reason = c.memory.should_write(
            MemoryWrite(layer=request.layer, content=request.content, importance=request.importance)
        )
        return {"written": False, "reason": reason}
    return {"written": True, "memory": record.model_dump(mode="json", exclude={"embedding"})}


@router.delete("/memory/{memory_id}")
async def forget(memory_id: UUID, c: Container = Depends(get_container)) -> dict:
    await c.memory.forget(memory_id)
    return {"forgotten": str(memory_id)}


@router.get("/memory/conflicts")
async def conflicts(pending_only: bool = True, c: Container = Depends(get_container)) -> dict:
    rows = c.memory.conflicts(pending_only=pending_only)
    return {
        "conflicts": [
            {**row.model_dump(mode="json"), "description": row.describe()} for row in rows
        ]
    }


@router.post("/memory/conflicts/{conflict_id}")
async def resolve(
    conflict_id: UUID, resolution: str, c: Container = Depends(get_container)
) -> dict:
    if resolution not in ("kept_old", "kept_new", "both_valid", "user_decided"):
        raise HTTPException(status_code=400, detail="unknown resolution")
    try:
        row = await c.memory.resolve_conflict(conflict_id, resolution)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="unknown conflict") from exc
    return row.model_dump(mode="json")


@router.get("/graph/query")
async def graph_query(
    entity: str, hops: int = 2, kind: str | None = None, c: Container = Depends(get_container)
) -> dict:
    node = c.graph.find(entity)
    if node is None:
        raise HTTPException(status_code=404, detail=f"no entity named {entity!r}")
    results = c.graph.traverse(node.id, hops=hops, target_kind=kind)
    return {
        "entity": {"id": str(node.id), "kind": node.kind, "name": node.name},
        "results": [
            {"name": e.name, "kind": e.kind, "distance": d, "path": path} for e, d, path in results
        ],
    }
