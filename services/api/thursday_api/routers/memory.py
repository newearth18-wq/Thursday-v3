"""Memory, conflict and knowledge-graph endpoints (§7, §10, §11)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from thursday_core.container import Container
from thursday_shared.enums import MemoryLayer, MemorySource
from thursday_shared.models import MemoryCandidate, MemoryQuery, MemoryWrite

from thursday_api.deps import get_container
from thursday_api.schemas import MemoryConfirmRequest, MemorySearchRequest, MemoryWriteRequest

router = APIRouter(tags=["memory"])


async def _search(request: MemorySearchRequest, c: Container) -> dict:
    records = await c.memory.recall(
        MemoryQuery(
            text=request.q,
            layers=[request.layer] if request.layer else [],
            project_id=request.project_id,
            k=request.k,
            min_confidence=request.min_confidence,
        )
    )
    return {"memories": [r.model_dump(mode="json", exclude={"embedding"}) for r in records]}


@router.post("/memory/search")
async def search(request: MemorySearchRequest, c: Container = Depends(get_container)) -> dict:
    """PART 71. POST rather than GET: a query can carry filters and a long free-text term."""
    return await _search(request, c)


@router.get("/memory/search")
async def search_via_query(
    q: str = "",
    layer: MemoryLayer | None = None,
    project_id: UUID | None = None,
    k: int = 8,
    min_confidence: float = 0.0,
    c: Container = Depends(get_container),
) -> dict:
    """The same search, for a browser or a curl one-liner."""
    return await _search(
        MemorySearchRequest(
            q=q, layer=layer, project_id=project_id, k=k, min_confidence=min_confidence
        ),
        c,
    )


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
        # An honest refusal beats a silent no-op: the write policy declined, and the caller
        # is told which of PART 39's four answers it gave.
        judgement = c.memory.judge(
            MemoryCandidate(
                layer=request.layer,
                content=request.content,
                importance=request.importance,
                source=MemorySource.USER,
            )
        )
        return {
            "written": False,
            "decision": judgement.decision.value,
            "reason": judgement.reason,
        }
    return {"written": True, "memory": record.model_dump(mode="json", exclude={"embedding"})}


@router.get("/memory/confirmations")
async def confirmations(c: Container = Depends(get_container)) -> dict:
    """PART 39/76 — candidates waiting on the owner, chiefly preferences an agent proposed."""
    return {
        "pending": [
            {
                "index": index,
                "content": candidate.content,
                "layer": str(candidate.layer),
                "source": str(candidate.source),
                "proposed_by": candidate.proposed_by,
            }
            for index, candidate in enumerate(c.memory.pending_confirmations())
        ]
    }


@router.post("/memory/confirmations")
async def confirm(request: MemoryConfirmRequest, c: Container = Depends(get_container)) -> dict:
    """The owner's yes or no. Accepting makes them the memory's source — which is exactly
    the authority an agent could not have given it."""
    try:
        record = await c.memory.confirm(request.index, accept=request.accept)
    except IndexError as exc:
        raise HTTPException(status_code=404, detail="no such pending candidate") from exc
    return {
        "accepted": request.accept,
        "memory": record.model_dump(mode="json", exclude={"embedding"}) if record else None,
    }


@router.get("/memory/links")
async def memory_links(
    memory_id: UUID | None = None, c: Container = Depends(get_container)
) -> dict:
    """PART 41 — how memories relate, kept instead of overwriting one with the other."""
    return {"links": [edge.model_dump(mode="json") for edge in c.memory.links(memory_id)]}


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
