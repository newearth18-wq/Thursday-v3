"""Talk to Thursday from a terminal.

    python -m apps.cli                 # embedded core + a local node — no server needed
    python -m apps.cli --remote        # talk to a running core over HTTP

The embedded mode exists so the vertical slice can be demonstrated end to end with one
command, on any of the three desktop platforms.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import sys
from pathlib import Path
from uuid import UUID

from thursday_core.config import get_settings
from thursday_core.container import build_container
from thursday_core.logging import configure_logging
from thursday_devices.hub import LoopbackDeviceSession
from thursday_devices.node.adapters import for_current_platform
from thursday_devices.node.executor import NodeExecutor
from thursday_shared.enums import VoiceMode
from thursday_shared.ids import new_id
from thursday_shared.models import ThursdayReply
from thursday_voice.providers import TextStubTTS

RESET = "\033[0m"
DIM = "\033[2m"
BOLD = "\033[1m"
MODE_COLOUR = {
    VoiceMode.NORMAL: "\033[36m",
    VoiceMode.THINKING: "\033[90m",
    VoiceMode.SUCCESS: "\033[32m",
    VoiceMode.WARNING: "\033[33m",
    VoiceMode.URGENT: "\033[31m",
    VoiceMode.QUIET: "\033[90m",
}

BANNER = f"""{BOLD}Thursday{RESET} — personal AI operating system
{DIM}type to talk · /help for commands · /quit to exit{RESET}"""

HELP = """
  /devices              list connected devices
  /approvals            list pending approvals
  /approve <n> [always] approve the nth pending request
  /reject <n>           reject the nth pending request
  /tasks                list tasks
  /memory <query>       search memory
  /audit                show the audit trail
  /undo                 undo the last reversible action
  /world                show world state
  /health               component health
  /stop                 emergency stop (lockdown)
  /quit                 exit
"""


def render(reply: ThursdayReply, *, speak: bool) -> None:
    colour = MODE_COLOUR.get(reply.voice_mode, "")
    print(f"{colour}Thursday>{RESET} {reply.text}")
    flags = [str(reply.voice_mode), f"confidence {reply.confidence:.2f}"]
    if not reply.verified:
        flags.append("unverified")
    if speak:
        flags.append("spoken")
    print(f"{DIM}          [{' · '.join(flags)}]{RESET}")
    if reply.detail and reply.detail not in reply.text:
        print(f"{DIM}          {reply.detail}{RESET}")
    for approval in reply.approvals:
        print(f"{DIM}          approval {approval.id} — /approve 1 or /reject 1{RESET}")


async def run_embedded(args: argparse.Namespace) -> None:
    settings = get_settings().model_copy(update={"log_level": args.log_level})
    if args.vault:
        settings = settings.model_copy(update={"obsidian_vault": Path(args.vault)})
    container = build_container(settings)
    tts = TextStubTTS()

    device_id = new_id()
    roots = [Path(p) for p in (args.allow_root or [str(Path.home())])]
    await container.hub.register(
        LoopbackDeviceSession(
            device_id=device_id,
            name=args.device_name,
            executor=NodeExecutor(for_current_platform(), allowed_roots=roots),
        ),
        location_context=args.location,
    )
    container.world.update(active_device_id=device_id, active_device_name=args.device_name)

    print(BANNER)
    print(
        f"{DIM}device: {args.device_name} · model: {settings.llm_backend} · "
        f"offline: {settings.offline} · roots: {', '.join(str(r) for r in roots)}{RESET}\n"
    )

    session_id = new_id()
    loop = asyncio.get_running_loop()
    while True:
        try:
            line = (await loop.run_in_executor(None, input, "you> ")).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line.startswith("/"):
            if await handle_command(container, line, session_id):
                break
            continue

        reply = await container.engine.handle_turn(
            session_id=session_id, text=line, device_id=device_id
        )
        if args.speak:
            await tts.synthesize(reply.text, mode=reply.voice_mode.value)
        render(reply, speak=args.speak)


async def handle_command(container, line: str, session_id: UUID) -> bool:
    """Returns True when the CLI should exit."""
    parts = line.split()
    command, rest = parts[0], parts[1:]

    if command in ("/quit", "/exit"):
        return True
    if command == "/help":
        print(HELP)
    elif command == "/devices":
        for device in container.hub.all():
            caps = [k for k, v in device.capabilities.model_dump().items() if v is True]
            print(f"  {device.name:16} {device.status:8} {device.os:10} {len(caps)} capabilities")
    elif command == "/approvals":
        pending = container.approvals.pending()
        if not pending:
            print("  nothing pending")
        for index, approval in enumerate(pending, start=1):
            print(
                f"  {index}. {approval.action} on {approval.resource or '—'} "
                f"(risk {approval.risk}, reversible={approval.reversible})"
            )
    elif command in ("/approve", "/reject"):
        pending = container.approvals.pending()
        index = int(rest[0]) - 1 if rest and rest[0].isdigit() else 0
        if not (0 <= index < len(pending)):
            print("  no such pending approval")
            return False
        from thursday_shared.enums import ApprovalScope

        scope = ApprovalScope.ALWAYS if "always" in rest else ApprovalScope.ONCE
        decided = await container.approvals.decide(
            pending[index].id, approve=command == "/approve", scope=scope
        )
        print(f"  {decided.action}: {decided.state}")
    elif command == "/tasks":
        for task in container.tasks.list(limit=10):
            print(f"  {task.status:16} {task.progress:>5.0%}  {task.title}")
    elif command == "/memory":
        from thursday_shared.models import MemoryQuery

        records = await container.memory.recall(MemoryQuery(text=" ".join(rest), k=8))
        for record in records:
            print(
                f"  [{record.layer:10} {record.source:9} conf {record.confidence:.2f} "
                f"score {record.score or 0:.2f}] {record.content[:90]}"
            )
        if not records:
            print("  nothing recalled")
    elif command == "/audit":
        for entry in container.audit.entries(limit=20):
            print(
                f"  {entry.ts:%H:%M:%S} {entry.action:18} {entry.result:10} "
                f"perm={entry.permission_decision or '—'}"
            )
        print(f"  chain intact: {container.audit.verify_chain()}")
    elif command == "/undo":
        record = container.undo.last()
        if record is None:
            print("  nothing to undo")
        else:
            ok = await container.undo.undo(record.action_id)
            print(f"  {record.description}: {'undone' if ok else 'could not undo'}")
    elif command == "/world":
        snapshot = container.world.snapshot()
        for key, value in snapshot.model_dump(mode="json").items():
            if value not in (None, [], {}, ""):
                print(f"  {key}: {value}")
    elif command == "/health":
        for check in await container.health():
            mark = "ok " if check["ok"] else "!! "
            print(f"  {mark} {check['component']:24} {check['detail']}")
    elif command == "/stop":
        print(f"  {await container.emergency_stop('all')}")
    else:
        print(f"  unknown command {command!r} — try /help")
    return False


async def run_remote(args: argparse.Namespace) -> None:
    import httpx

    session_id: str | None = None
    print(BANNER)
    print(f"{DIM}core: {args.core}{RESET}\n")
    async with httpx.AsyncClient(base_url=args.core, timeout=120) as client:
        loop = asyncio.get_running_loop()
        while True:
            try:
                line = (await loop.run_in_executor(None, input, "you> ")).strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not line or line in ("/quit", "/exit"):
                break
            response = await client.post(
                "/api/v1/conversations", json={"text": line, "session_id": session_id}
            )
            if response.status_code >= 400:
                print(f"  error: {response.text[:300]}")
                continue
            body = response.json()
            session_id = body["session_id"]
            print(f"Thursday> {body['text']}")
            print(
                f"{DIM}          [{body['voice_mode']} · confidence {body['confidence']:.2f}"
                f"{'' if body['verified'] else ' · unverified'}]{RESET}"
            )


def main() -> None:
    parser = argparse.ArgumentParser(prog="thursday", description="Talk to Thursday")
    parser.add_argument("--remote", action="store_true", help="talk to a running core over HTTP")
    parser.add_argument("--core", default="http://127.0.0.1:8000")
    parser.add_argument("--device-name", default="This-PC")
    parser.add_argument("--location", default="office")
    parser.add_argument("--allow-root", action="append", default=None)
    parser.add_argument("--vault", default=None, help="path to the Obsidian vault")
    parser.add_argument("--speak", action="store_true", help="run replies through TTS")
    parser.add_argument("--log-level", default="WARNING")
    args = parser.parse_args()

    configure_logging(level=args.log_level)
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run_remote(args) if args.remote else run_embedded(args))
    print("bye.")
    sys.exit(0)


if __name__ == "__main__":
    main()
