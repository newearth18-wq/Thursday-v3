"""Ephemeral state: device presence, world state, locks, temporary context (PART 2).

Two implementations of one port. The in-memory one is what tests and a single-process
install use; the Redis one is what a multi-process deployment uses. Neither is a fallback
for the other — they are both supported, selected by whether ``redis_url`` is set (ADR 0006).

What belongs here: state that is *current* rather than *historical*, cheap to recompute, and
useless after a restart. Anything the owner would miss if it vanished belongs in Postgres.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from thursday_core.logging import get_logger

log = get_logger(__name__)


class InMemoryStateStore:
    """Process-local state with TTLs. Correct for one process, and that is the common case."""

    name = "memory"
    distributed = False

    def __init__(self) -> None:
        self._values: dict[str, tuple[Any, float | None]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._subscribers: dict[str, list[Callable[[dict], Any]]] = {}

    async def get(self, key: str) -> Any:
        entry = self._values.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if expires_at is not None and expires_at <= time.monotonic():
            self._values.pop(key, None)
            return None
        return value

    async def set(self, key: str, value: Any, *, ttl_s: float | None = None) -> None:
        self._values[key] = (value, time.monotonic() + ttl_s if ttl_s else None)

    async def delete(self, key: str) -> None:
        self._values.pop(key, None)

    async def keys(self, prefix: str) -> list[str]:
        return [
            k for k in list(self._values) if k.startswith(prefix) and await self.get(k) is not None
        ]

    async def publish(self, channel: str, message: dict) -> None:
        for handler in self._subscribers.get(channel, []):
            result = handler(message)
            if asyncio.iscoroutine(result):
                await result

    def subscribe(self, channel: str, handler: Callable[[dict], Any]) -> None:
        self._subscribers.setdefault(channel, []).append(handler)

    @asynccontextmanager
    async def lock(self, name: str, *, timeout_s: float = 30.0) -> AsyncIterator[None]:
        handle = self._locks.setdefault(name, asyncio.Lock())
        await asyncio.wait_for(handle.acquire(), timeout=timeout_s)
        try:
            yield
        finally:
            handle.release()

    async def health(self) -> tuple[bool, str]:
        return True, f"in-process, {len(self._values)} keys"


class RedisStateStore:
    """Redis-backed state, for a deployment where the API and the worker are separate
    processes and both need to see the same device presence."""

    name = "redis"
    distributed = True

    def __init__(self, url: str, *, prefix: str = "thursday:state") -> None:
        self.url = url
        self._prefix = prefix
        self._client: Any = None

    async def _redis(self) -> Any:
        if self._client is None:
            import redis.asyncio as redis

            self._client = redis.from_url(self.url, decode_responses=True)
        return self._client

    def _key(self, key: str) -> str:
        return f"{self._prefix}:{key}"

    async def get(self, key: str) -> Any:
        raw = await (await self._redis()).get(self._key(key))
        return json.loads(raw) if raw is not None else None

    async def set(self, key: str, value: Any, *, ttl_s: float | None = None) -> None:
        payload = json.dumps(value, default=str)
        client = await self._redis()
        if ttl_s:
            await client.set(self._key(key), payload, ex=int(ttl_s))
        else:
            await client.set(self._key(key), payload)

    async def delete(self, key: str) -> None:
        await (await self._redis()).delete(self._key(key))

    async def keys(self, prefix: str) -> list[str]:
        client = await self._redis()
        pattern = f"{self._key(prefix)}*"
        # SCAN rather than KEYS: KEYS blocks the server, and this runs on a live instance.
        found: list[str] = []
        cursor = 0
        while True:
            cursor, batch = await client.scan(cursor=cursor, match=pattern, count=200)
            found.extend(k.removeprefix(f"{self._prefix}:") for k in batch)
            if cursor == 0:
                return found

    async def publish(self, channel: str, message: dict) -> None:
        await (await self._redis()).publish(channel, json.dumps(message, default=str))

    @asynccontextmanager
    async def lock(self, name: str, *, timeout_s: float = 30.0) -> AsyncIterator[None]:
        client = await self._redis()
        handle = client.lock(f"{self._prefix}:lock:{name}", timeout=timeout_s)
        await handle.acquire()
        try:
            yield
        finally:
            # A lock whose timeout already expired cannot be released; that is not an error.
            try:
                await handle.release()
            except Exception as exc:
                log.debug("lock_release_noop", lock=name, error=str(exc))

    async def health(self) -> tuple[bool, str]:
        try:
            await (await self._redis()).ping()
        except Exception as exc:
            return False, f"unreachable: {exc}"
        return True, f"connected to {self.url.rsplit('@', 1)[-1]}"


def build_state_store(redis_url: str | None) -> Any:
    """One line, one decision: Redis when configured, in-process otherwise."""
    return RedisStateStore(redis_url) if redis_url else InMemoryStateStore()
