"""Seed data (PART 93).

Run with ``python -m database.seeds``. Idempotent: it inserts what is missing and leaves
what exists alone, so it is safe on every deploy rather than only on a fresh database.

What gets seeded is the *catalogue* — the agents, tools and permission defaults Thursday
needs in order to start — not the owner's content. A seed that invents a user's data is a
seed that has to be cleaned up.
"""

from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import select
from thursday_core.config import Settings, get_settings
from thursday_core.logging import configure_logging, get_logger
from thursday_devices import actions as device_catalogue
from thursday_security.policy import PolicyTable
from thursday_shared.db.models import Agent, Tool, User
from thursday_shared.db.session import dispose_engine, init_engine, session_scope

log = get_logger("thursday.seeds")

#: The agents Thursday starts with. Capabilities are what the router matches on.
SEED_AGENTS: tuple[dict[str, Any], ...] = (
    {
        "name": "thursday",
        "agent_type": "orchestrator",
        "description": "The assistant itself. Plans, delegates, verifies, and reports.",
        "capabilities": ["orchestration", "conversation", "planning"],
        "allowed_tools": ["*"],
        "permission_ceiling": 2,
    },
    {
        "name": "computer",
        "agent_type": "computer",
        "description": "Operates the owner's machines: applications, files, processes, screen.",
        "capabilities": ["app_control", "file", "os", "diagnostics", "screen"],
        "allowed_tools": [
            "app.open",
            "app.close",
            "file.open",
            "file.read",
            "file.write",
            "file.list",
            "file.search",
            "file.move",
            "system.process.list",
            "system.info",
            "window.active",
            "screen.capture",
            "browser.open",
        ],
        "permission_ceiling": 2,
    },
    {
        "name": "file",
        "agent_type": "file",
        "description": "Finds, reads, moves and copies files.",
        "capabilities": ["file", "search", "read", "write"],
        "allowed_tools": ["file.search", "file.read", "file.list", "file.move", "file.copy"],
        "permission_ceiling": 2,
    },
    {
        "name": "research",
        "agent_type": "research",
        "description": "Finds and cross-checks information, with sources attached.",
        "capabilities": ["research", "search", "recall", "fact_check", "summarize"],
        "allowed_tools": ["memory.search", "obsidian.search", "web.search"],
        "permission_ceiling": 0,
    },
    {
        "name": "browser",
        "agent_type": "browser",
        "description": "Navigates websites and web apps by role and name, never coordinates.",
        "capabilities": ["browser", "web", "form", "download"],
        "allowed_tools": [
            "browser.navigate",
            "browser.click",
            "browser.type",
            "browser.read",
            "browser.submit",
            "browser.screenshot",
        ],
        "permission_ceiling": 2,
    },
    {
        "name": "supervisor",
        "agent_type": "supervisor",
        "description": "Validates other agents' output. Read-only by construction.",
        "capabilities": ["verification", "critique"],
        "allowed_tools": ["memory.search", "file.read"],
        # A verifier that can edit the work is not a verifier.
        "permission_ceiling": 0,
    },
)

#: Tools whose definitions the database should know about, so the permission panel can list
#: them before any of them has ever run.
SEED_TOOL_NAMES: tuple[str, ...] = (
    "app.open",
    "app.close",
    "file.read",
    "file.write",
    "file.list",
    "file.search",
    "file.move",
    "file.copy",
    "file.delete",
    "screen.capture",
    "window.active",
    "system.info",
    "system.process.list",
    "system.process.stop",
    "shell.run",
    "browser.open",
    "notify.show",
    "memory.search",
    "memory.write",
    "obsidian.write",
    "obsidian.search",
    "web.search",
    "clock.now",
    "email.send",
)


async def seed(settings: Settings | None = None) -> dict[str, int]:
    settings = settings or get_settings()
    init_engine(settings)
    counts = {"users": 0, "agents": 0, "tools": 0}

    try:
        async with session_scope() as session:
            owner = (
                await session.execute(select(User).where(User.email == "owner@localhost"))
            ).scalar_one_or_none()
            if owner is None:
                owner = User(
                    email="owner@localhost",
                    display_name=settings.owner_name,
                    locale=settings.locale,
                    timezone=settings.timezone,
                    proactivity_level=int(settings.proactivity),
                    settings={"autonomy": int(settings.autonomy)},
                )
                session.add(owner)
                await session.flush()
                counts["users"] += 1

            existing_agents = {
                row.name
                for row in (
                    await session.execute(select(Agent).where(Agent.user_id == owner.id))
                ).scalars()
            }
            for spec in SEED_AGENTS:
                if spec["name"] in existing_agents:
                    continue
                session.add(
                    Agent(
                        user_id=owner.id,
                        name=spec["name"],
                        agent_type=spec["agent_type"],
                        description=spec["description"],
                        capabilities={"list": spec["capabilities"]},
                        allowed_tools={"list": spec["allowed_tools"]},
                        permission_ceiling=spec["permission_ceiling"],
                        enabled=True,
                    )
                )
                counts["agents"] += 1

            policy = PolicyTable()
            existing_tools = {
                row.name
                for row in (
                    await session.execute(select(Tool).where(Tool.user_id == owner.id))
                ).scalars()
            }
            for name in SEED_TOOL_NAMES:
                if name in existing_tools:
                    continue
                rules = policy.get(name)
                spec = device_catalogue.get(name)
                session.add(
                    Tool(
                        user_id=owner.id,
                        name=name,
                        description=spec.description if spec else "",
                        capabilities={"namespace": name.split(".")[0]},
                        permission_level=int(rules.level),
                        risk_level=rules.risk.value,
                        approval_policy=rules.default.value,
                        supports_undo=rules.reversible,
                        supports_dry_run=name
                        in ("file.write", "file.move", "file.delete", "shell.run"),
                        enabled=True,
                    )
                )
                counts["tools"] += 1

        log.info("seeded", **counts)
        return counts
    finally:
        await dispose_engine()


def main() -> None:
    configure_logging(level="INFO")
    counts = asyncio.run(seed())
    print(
        f"seeded {counts['users']} user(s), {counts['agents']} agent(s), "
        f"{counts['tools']} tool(s) — existing rows left alone"
    )


if __name__ == "__main__":
    main()
