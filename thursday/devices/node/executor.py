"""Node executor — the ACT and VERIFY halves of §20, running on the user's machine.

The contract with the core is strict: an action reports ``verified=True`` only when the
node *observed* the effect afterwards. "The command didn't raise" is not evidence, and a
node that cannot check says so, which lets Thursday phrase the answer honestly (§76).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import shutil
import time
from pathlib import Path
from typing import Any

from thursday.core.logging import get_logger
from thursday.devices import actions as catalogue
from thursday.devices.node.adapters.base import OSAdapter
from thursday.shared.models import DeviceAction, DeviceActionResult, UndoRecord

log = get_logger(__name__)

#: How long to wait for a launched application to appear before giving up on verification.
LAUNCH_SETTLE_S = 0.4
LAUNCH_VERIFY_ATTEMPTS = 6


class NodeExecutor:
    """Dispatches an action to the OS adapter, then verifies it."""

    def __init__(self, adapter: OSAdapter, *, allowed_roots: list[Path] | None = None) -> None:
        self.adapter = adapter
        #: A second line of defence behind the core's path scopes — the node refuses
        #: to touch anything outside these roots even if the core asked it to.
        self.allowed_roots = [Path(p).expanduser().resolve() for p in (allowed_roots or [Path.home()])]

    async def execute(self, action: DeviceAction) -> DeviceActionResult:
        started = time.perf_counter()
        spec = catalogue.get(action.action)
        if spec is None:
            return self._failure(action, f"unknown action {action.action!r}", started)
        if missing := catalogue.missing_args(action.action, action.args):
            return self._failure(action, f"missing required args: {', '.join(missing)}", started)
        if not self.adapter.capabilities().supports(spec.capability):
            return self._failure(
                action, f"this device does not support {spec.capability!r}", started
            )

        handler = getattr(self, f"_do_{action.action}", None)
        if handler is None:
            return self._failure(action, f"no handler for {action.action!r} on this node", started)

        try:
            data, evidence, verified, undo = await asyncio.wait_for(
                handler(action.args), timeout=action.timeout_s
            )
        except TimeoutError:
            return self._failure(action, f"timed out after {action.timeout_s:g}s", started)
        except Exception as exc:
            log.warning("device_action_failed", action=action.action, error=str(exc))
            return self._failure(action, f"{type(exc).__name__}: {exc}", started)

        if not spec.verify:
            # The catalogue says this action's effect is not observable; be explicit.
            verified = True
            evidence = {**evidence, "verification": "not applicable for this action"}

        return DeviceActionResult(
            action_id=action.id,
            ok=True,
            verified=verified,
            evidence=evidence,
            data=data,
            duration_ms=(time.perf_counter() - started) * 1000,
            undo=undo,
        )

    # ------------------------------------------------------------------ handlers
    # Each returns (data, evidence, verified, undo).

    async def _do_open_app(self, args: dict[str, Any]) -> tuple[dict, dict, bool, UndoRecord | None]:
        name = str(args["name"])
        launch = await self.adapter.launch(name, args.get("args"))

        # VERIFY: poll for the process, then read the window title as corroboration.
        processes: list[dict[str, Any]] = []
        for _ in range(LAUNCH_VERIFY_ATTEMPTS):
            await asyncio.sleep(LAUNCH_SETTLE_S)
            processes = await self.adapter.find_processes(name)
            if processes:
                break
        window = await self.adapter.active_window()
        verified = bool(processes)
        evidence = {
            "pids": [p["pid"] for p in processes][:5],
            "process_count": len(processes),
            "active_window": window,
            "launched_pid": launch.get("pid"),
        }
        undo = (
            UndoRecord(
                action_id=args.get("_action_id") or __import__("uuid").uuid4(),
                operation="close_app",
                args={"name": name},
                description=f"close {name}",
            )
            if verified
            else None
        )
        return {"app": name, **launch}, evidence, verified, undo

    async def _do_close_app(self, args: dict[str, Any]) -> tuple[dict, dict, bool, UndoRecord | None]:
        name = str(args["name"])
        before = await self.adapter.find_processes(name)
        result = await self.adapter.terminate(name, force=bool(args.get("force")))
        await asyncio.sleep(LAUNCH_SETTLE_S)
        after = await self.adapter.find_processes(name)
        return (
            {"app": name, **result},
            {"before": len(before), "after": len(after)},
            len(after) < len(before) or not after,
            UndoRecord(
                action_id=__import__("uuid").uuid4(),
                operation="open_app",
                args={"name": name},
                description=f"reopen {name}",
            ),
        )

    async def _do_open_file(self, args: dict[str, Any]) -> tuple[dict, dict, bool, UndoRecord | None]:
        path = self._resolve(args["path"])
        result = await self.adapter.open_path(str(path))
        await asyncio.sleep(LAUNCH_SETTLE_S)
        window = await self.adapter.active_window()
        # A handler process is the strongest signal available without app integration.
        verified = bool(result.get("pid")) or bool(window)
        return {"path": str(path)}, {"active_window": window, **result}, verified, None

    async def _do_read_file(self, args: dict[str, Any]) -> tuple[dict, dict, bool, UndoRecord | None]:
        path = self._resolve(args["path"])
        limit = int(args.get("max_bytes", 200_000))

        def read() -> tuple[str, int, str]:
            return (
                path.read_text(encoding="utf-8", errors="replace")[:limit],
                path.stat().st_size,
                _hash(path),
            )

        # Actions run concurrently on the node, so blocking I/O goes to a worker thread —
        # a slow read must not stall the socket or the other actions in flight.
        text, size, digest = await asyncio.to_thread(read)
        return {"path": str(path), "content": text}, {"size": size, "sha256": digest}, True, None

    async def _do_write_file(self, args: dict[str, Any]) -> tuple[dict, dict, bool, UndoRecord | None]:
        path = self._resolve(args["path"])
        content = str(args["content"])
        def write() -> tuple[str | None, str, str, int]:
            before = path.read_text(encoding="utf-8") if path.exists() else None
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            # VERIFY: read it back and compare, rather than trusting the write call.
            return before, path.read_text(encoding="utf-8"), _hash(path), path.stat().st_size

        previous, written, digest, size = await asyncio.to_thread(write)
        return (
            {"path": str(path), "bytes": len(content.encode())},
            {"sha256": digest, "size": size},
            written == content,
            UndoRecord(
                action_id=__import__("uuid").uuid4(),
                operation="restore_file",
                args={"path": str(path)},
                previous_state={"content": previous} if previous is not None else {"absent": True},
                description=f"restore {path.name}",
            ),
        )

    async def _do_create_folder(self, args: dict[str, Any]) -> tuple[dict, dict, bool, UndoRecord | None]:
        path = self._resolve(args["path"])
        existed = path.exists()
        path.mkdir(parents=True, exist_ok=True)
        return (
            {"path": str(path)},
            {"existed_before": existed, "is_dir": path.is_dir()},
            path.is_dir(),
            None
            if existed
            else UndoRecord(
                action_id=__import__("uuid").uuid4(),
                operation="delete_folder",
                args={"path": str(path)},
                description=f"remove {path.name}",
            ),
        )

    async def _do_move(self, args: dict[str, Any]) -> tuple[dict, dict, bool, UndoRecord | None]:
        src, dst = self._resolve(args["src"]), self._resolve(args["dst"])
        if not src.exists():
            raise FileNotFoundError(str(src))
        shutil.move(str(src), str(dst))
        return (
            {"src": str(src), "dst": str(dst)},
            {"dst_exists": dst.exists(), "src_gone": not src.exists()},
            dst.exists() and not src.exists(),
            UndoRecord(
                action_id=__import__("uuid").uuid4(),
                operation="move",
                args={"src": str(dst), "dst": str(src)},
                description=f"move {dst.name} back",
            ),
        )

    async def _do_copy(self, args: dict[str, Any]) -> tuple[dict, dict, bool, UndoRecord | None]:
        src, dst = self._resolve(args["src"]), self._resolve(args["dst"])
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        return (
            {"src": str(src), "dst": str(dst)},
            {"dst_exists": dst.exists()},
            dst.exists(),
            UndoRecord(
                action_id=__import__("uuid").uuid4(),
                operation="delete",
                args={"path": str(dst)},
                description=f"remove the copy at {dst.name}",
            ),
        )

    async def _do_delete(self, args: dict[str, Any]) -> tuple[dict, dict, bool, UndoRecord | None]:
        path = self._resolve(args["path"])
        if not path.exists():
            raise FileNotFoundError(str(path))
        # Move to a quarantine directory rather than unlinking, so "delete" stays undoable.
        trash = Path.home() / ".thursday-trash"
        trash.mkdir(parents=True, exist_ok=True)
        destination = trash / f"{int(time.time())}-{path.name}"
        shutil.move(str(path), str(destination))
        return (
            {"path": str(path), "moved_to": str(destination)},
            {"gone": not path.exists(), "recoverable": destination.exists()},
            not path.exists(),
            UndoRecord(
                action_id=__import__("uuid").uuid4(),
                operation="restore_from_trash",
                args={"src": str(destination), "dst": str(path)},
                description=f"restore {path.name}",
            ),
        )

    async def _do_list_dir(self, args: dict[str, Any]) -> tuple[dict, dict, bool, UndoRecord | None]:
        path = self._resolve(args["path"])
        limit = int(args.get("limit", 200))

        def listing() -> list[dict[str, Any]]:
            return [
                {"name": child.name, "is_dir": child.is_dir(),
                 "size": child.stat().st_size if child.is_file() else None}
                for child in sorted(path.iterdir())[:limit]
            ]

        entries = await asyncio.to_thread(listing)
        return {"path": str(path), "entries": entries}, {"count": len(entries)}, True, None

    async def _do_search_files(self, args: dict[str, Any]) -> tuple[dict, dict, bool, UndoRecord | None]:
        root = self._resolve(args["root"])
        pattern = str(args["pattern"])
        limit = int(args.get("limit", 50))
        def scan() -> list[dict[str, Any]]:
            found: list[dict[str, Any]] = []
            for path in root.rglob(pattern):
                found.append({"path": str(path), "mtime": path.stat().st_mtime})
                if len(found) >= limit:
                    break
            return found

        matches = await asyncio.to_thread(scan)
        matches.sort(key=lambda m: m["mtime"], reverse=True)
        return {"matches": matches}, {"count": len(matches)}, True, None

    async def _do_read_active_window(self, args: dict[str, Any]) -> tuple[dict, dict, bool, UndoRecord | None]:
        title = await self.adapter.active_window()
        return {"title": title}, {"available": title is not None}, True, None

    async def _do_screenshot(self, args: dict[str, Any]) -> tuple[dict, dict, bool, UndoRecord | None]:
        image = await self.adapter.screenshot(**args)
        return (
            {"format": "png", "bytes": len(image)},
            {"captured": len(image) > 0, "sha256": hashlib.sha256(image).hexdigest()[:16]},
            len(image) > 0,
            None,
        )

    async def _do_run_shell(self, args: dict[str, Any]) -> tuple[dict, dict, bool, UndoRecord | None]:
        result = await self.adapter.run_shell(
            str(args["command"]), timeout=float(args.get("timeout", 30))
        )
        return result, {"exit_code": result["exit_code"]}, result["exit_code"] == 0, None

    async def _do_process_status(self, args: dict[str, Any]) -> tuple[dict, dict, bool, UndoRecord | None]:
        processes = await self.adapter.find_processes(str(args["name"]))
        return (
            {"running": bool(processes), "processes": processes[:10]},
            {"count": len(processes)},
            True,
            None,
        )

    async def _do_system_info(self, args: dict[str, Any]) -> tuple[dict, dict, bool, UndoRecord | None]:
        import platform

        telemetry = await self.adapter.telemetry()
        info = {
            "os": self.adapter.os_name,
            "platform": platform.platform(),
            "python": platform.python_version(),
            "hostname": platform.node(),
            "telemetry": telemetry.model_dump(mode="json", exclude_none=True),
        }
        return info, {"collected": True}, True, None

    async def _do_clipboard_get(self, args: dict[str, Any]) -> tuple[dict, dict, bool, UndoRecord | None]:
        text = await self.adapter.clipboard_get()
        return {"text": text}, {"length": len(text)}, True, None

    async def _do_clipboard_set(self, args: dict[str, Any]) -> tuple[dict, dict, bool, UndoRecord | None]:
        previous = ""
        # An unreadable clipboard costs the undo record, not the action.
        with contextlib.suppress(Exception):
            previous = await self.adapter.clipboard_get()
        text = str(args["text"])
        await self.adapter.clipboard_set(text)
        readback = await self.adapter.clipboard_get()
        return (
            {"length": len(text)},
            {"readback_matches": readback.strip() == text.strip()},
            readback.strip() == text.strip(),
            UndoRecord(
                action_id=__import__("uuid").uuid4(),
                operation="clipboard_set",
                args={"text": previous},
                description="restore the previous clipboard contents",
            ),
        )

    async def _do_notify(self, args: dict[str, Any]) -> tuple[dict, dict, bool, UndoRecord | None]:
        await self.adapter.notify(str(args["title"]), str(args["body"]))
        return {"delivered": True}, {}, True, None

    async def _do_get_volume(self, args: dict[str, Any]) -> tuple[dict, dict, bool, UndoRecord | None]:
        level = await self.adapter.get_volume()
        return {"level": level}, {}, True, None

    async def _do_set_volume(self, args: dict[str, Any]) -> tuple[dict, dict, bool, UndoRecord | None]:
        previous = None
        with contextlib.suppress(Exception):
            previous = await self.adapter.get_volume()
        level = float(args["level"])
        await self.adapter.set_volume(level)
        try:
            readback = await self.adapter.get_volume()
            verified = abs(readback - level) < 0.06
        except Exception:
            readback, verified = None, False
        undo = (
            UndoRecord(
                action_id=__import__("uuid").uuid4(),
                operation="set_volume",
                args={"level": previous},
                description="restore the previous volume",
            )
            if previous is not None
            else None
        )
        return {"level": level}, {"readback": readback}, verified, undo

    # ------------------------------------------------------------------ helpers

    def _resolve(self, raw: str) -> Path:
        path = Path(str(raw)).expanduser()
        try:
            resolved = path.resolve()
        except OSError as exc:
            raise ValueError(f"cannot resolve path {raw!r}") from exc
        if not any(
            resolved == root or root in resolved.parents for root in self.allowed_roots
        ):
            raise PermissionError(
                f"{resolved} is outside this node's allowed roots "
                f"({', '.join(str(r) for r in self.allowed_roots)})"
            )
        return resolved

    def _failure(self, action: DeviceAction, error: str, started: float) -> DeviceActionResult:
        return DeviceActionResult(
            action_id=action.id,
            ok=False,
            verified=False,
            error=error,
            duration_ms=(time.perf_counter() - started) * 1000,
        )


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()[:16]
