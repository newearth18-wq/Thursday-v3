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

from thursday_core.logging import get_logger
from thursday_shared.models import DeviceAction, DeviceActionResult, UndoRecord

from thursday_devices import actions as catalogue
from thursday_devices.node.adapters.base import OSAdapter

log = get_logger(__name__)

#: How long to wait for a launched application to appear before giving up on verification.
LAUNCH_SETTLE_S = 0.4
LAUNCH_VERIFY_ATTEMPTS = 6
#: How many matching files a search will walk before it stops and says it stopped.
#: A Downloads folder is small; a home directory with a node_modules in it is not.
SEARCH_SCAN_CAP = 5000


class NodeExecutor:
    """Dispatches an action to the OS adapter, then verifies it."""

    def __init__(self, adapter: OSAdapter, *, allowed_roots: list[Path] | None = None) -> None:
        self.adapter = adapter
        #: A second line of defence behind the core's path scopes — the node refuses
        #: to touch anything outside these roots even if the core asked it to.
        self.allowed_roots = [
            Path(p).expanduser().resolve() for p in (allowed_roots or [Path.home()])
        ]

    def supported_actions(self) -> list[str]:
        """The actions this node can actually run.

        Derived from the dispatch table rather than declared separately, so it cannot claim
        a capability the node has no handler for — the diagnostics endpoint that says
        otherwise would be believed.
        """
        return sorted(self._handlers())

    async def execute(self, action: DeviceAction) -> DeviceActionResult:
        started = time.perf_counter()
        name = catalogue.canonical(action.action)
        spec = catalogue.get(name)
        if spec is None:
            return self._failure(action, f"unknown action {action.action!r}", started)
        if missing := catalogue.missing_args(name, action.args):
            return self._failure(action, f"missing required args: {', '.join(missing)}", started)
        if not self.adapter.capabilities().supports(spec.capability):
            return self._failure(
                action, f"this device does not support {spec.capability!r}", started
            )

        handler = self._handlers().get(name)
        if handler is None:
            return self._failure(action, f"no handler for {name!r} on this node", started)

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

    def _handlers(self) -> dict[str, Any]:
        """Catalogue name → handler. Explicit, because dotted names are not attribute names."""
        return {
            "app.open": self._app_open,
            "app.close": self._app_close,
            "system.process.start": self._app_open,
            "system.process.stop": self._app_close,
            "system.process.list": self._system_process_list,
            "system.info": self._system_info,
            "file.open": self._file_open,
            "file.read": self._file_read,
            "file.write": self._file_write,
            "file.create": self._file_write,
            "file.folder.create": self._file_folder_create,
            "file.move": self._file_move,
            "file.rename": self._file_move,
            "file.copy": self._file_copy,
            "file.delete": self._file_delete,
            "file.list": self._file_list,
            "file.search": self._file_search,
            "window.active": self._window_active,
            "screen.capture": self._screen_capture,
            "clipboard.read": self._clipboard_read,
            "clipboard.write": self._clipboard_write,
            "audio.volume.get": self._audio_volume_get,
            "audio.volume.set": self._audio_volume_set,
            "notify.show": self._notify_show,
            "shell.run": self._shell_run,
            "powershell.run": self._shell_run,
            "browser.open": self._browser_open,
        }

    # ------------------------------------------------------------------ handlers
    # Each returns (data, evidence, verified, undo).

    async def _app_open(self, args: dict[str, Any]) -> tuple[dict, dict, bool, UndoRecord | None]:
        # `app.open` names it `app`; `system.process.start` names it `name`.
        name = str(args.get("app") or args["name"])
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
                operation="app.close",
                args={"app": name},
                description=f"close {name}",
            )
            if verified
            else None
        )
        return {"app": name, **launch}, evidence, verified, undo

    async def _app_close(self, args: dict[str, Any]) -> tuple[dict, dict, bool, UndoRecord | None]:
        name = str(args.get("app") or args["name"])
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
                operation="app.open",
                args={"app": name},
                description=f"reopen {name}",
            ),
        )

    async def _file_open(self, args: dict[str, Any]) -> tuple[dict, dict, bool, UndoRecord | None]:
        path = self._resolve(args["path"])
        result = await self.adapter.open_path(str(path))
        await asyncio.sleep(LAUNCH_SETTLE_S)
        window = await self.adapter.active_window()
        # A handler process is the strongest signal available without app integration.
        verified = bool(result.get("pid")) or bool(window)
        return {"path": str(path)}, {"active_window": window, **result}, verified, None

    async def _file_read(self, args: dict[str, Any]) -> tuple[dict, dict, bool, UndoRecord | None]:
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

    async def _file_write(self, args: dict[str, Any]) -> tuple[dict, dict, bool, UndoRecord | None]:
        path = self._resolve(args["path"])
        content = str(args.get("content", ""))

        def write() -> tuple[str | None, str | None, str, str, int]:
            before = path.read_text(encoding="utf-8") if path.exists() else None
            backup: str | None = None
            if before is not None:
                # PART 21 — an existing document is versioned before it is overwritten.
                backup_dir = path.parent / ".thursday-versions"
                backup_dir.mkdir(parents=True, exist_ok=True)
                target = backup_dir / f"{path.stem}.{int(time.time())}{path.suffix}"
                shutil.copy2(path, target)
                backup = str(target)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            # VERIFY: read it back and compare, rather than trusting the write call.
            return (
                before,
                backup,
                path.read_text(encoding="utf-8"),
                _hash(path),
                path.stat().st_size,
            )

        previous, backup, written, digest, size = await asyncio.to_thread(write)
        return (
            {"path": str(path), "bytes": len(content.encode()), "backup": backup},
            {"sha256": digest, "size": size, "backup": backup},
            written == content,
            UndoRecord(
                action_id=__import__("uuid").uuid4(),
                operation="file.restore",
                args={"path": str(path)},
                previous_state=(
                    {"content": previous, "backup": backup}
                    if previous is not None
                    else {"absent": True}
                ),
                description=f"restore {path.name}",
            ),
        )

    async def _file_folder_create(
        self, args: dict[str, Any]
    ) -> tuple[dict, dict, bool, UndoRecord | None]:
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
                operation="file.folder.delete",
                args={"path": str(path)},
                description=f"remove {path.name}",
            ),
        )

    async def _file_move(self, args: dict[str, Any]) -> tuple[dict, dict, bool, UndoRecord | None]:
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
                operation="file.move",
                args={"src": str(dst), "dst": str(src)},
                description=f"move {dst.name} back",
            ),
        )

    async def _file_copy(self, args: dict[str, Any]) -> tuple[dict, dict, bool, UndoRecord | None]:
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
                operation="file.delete",
                args={"path": str(dst)},
                description=f"remove the copy at {dst.name}",
            ),
        )

    async def _file_delete(
        self, args: dict[str, Any]
    ) -> tuple[dict, dict, bool, UndoRecord | None]:
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
                operation="file.restore_from_trash",
                args={"src": str(destination), "dst": str(path)},
                description=f"restore {path.name}",
            ),
        )

    async def _file_list(self, args: dict[str, Any]) -> tuple[dict, dict, bool, UndoRecord | None]:
        path = self._resolve(args["path"])
        limit = int(args.get("limit", 200))

        def listing() -> list[dict[str, Any]]:
            return [
                {
                    "name": child.name,
                    "is_dir": child.is_dir(),
                    "size": child.stat().st_size if child.is_file() else None,
                }
                for child in sorted(path.iterdir())[:limit]
            ]

        entries = await asyncio.to_thread(listing)
        return {"path": str(path), "entries": entries}, {"count": len(entries)}, True, None

    async def _file_search(
        self, args: dict[str, Any]
    ) -> tuple[dict, dict, bool, UndoRecord | None]:
        """Find files under a root, newest first.

        ``pattern`` takes one glob or several — "the latest Excel file" means ``*.xlsx``
        *and* ``*.xls``, and asking twice would sort two lists the caller then has to merge.

        The ordering is the subtle part. Truncating to ``limit`` during the walk and sorting
        afterwards returns the newest of whatever the filesystem happened to yield first,
        which is not the newest file and is wrong in a way nobody notices: the answer looks
        entirely plausible. So the walk is bounded by its own much larger cap, the sort
        happens over everything found, and ``limit`` applies last. When that cap is reached
        the result says so rather than presenting a partial answer as complete.
        """
        root = self._resolve(args["root"])
        raw = args["pattern"]
        patterns = [str(p) for p in (raw if isinstance(raw, list | tuple) else [raw])]
        limit = int(args.get("limit", 50))
        scan_cap = int(args.get("scan_cap", SEARCH_SCAN_CAP))

        def scan() -> tuple[list[dict[str, Any]], bool]:
            seen: dict[str, dict[str, Any]] = {}
            truncated = False
            for pattern in patterns:
                for path in root.rglob(pattern):
                    if len(seen) >= scan_cap:
                        truncated = True
                        break
                    try:
                        if not path.is_file():
                            continue
                        stat = path.stat()
                    except OSError:
                        # A file that vanished or that this node may not stat is skipped,
                        # not fatal: a search should not fail because of one bad entry.
                        continue
                    seen[str(path)] = {
                        "path": str(path),
                        "name": path.name,
                        "mtime": stat.st_mtime,
                        "size": stat.st_size,
                    }
                if truncated:
                    break
            found = sorted(seen.values(), key=lambda m: m["mtime"], reverse=True)
            return found, truncated

        matches, truncated = await asyncio.to_thread(scan)
        return (
            {"matches": matches[:limit], "truncated": truncated},
            {"scanned": len(matches), "returned": min(len(matches), limit), "patterns": patterns},
            True,
            None,
        )

    async def _window_active(
        self, args: dict[str, Any]
    ) -> tuple[dict, dict, bool, UndoRecord | None]:
        title = await self.adapter.active_window()
        return {"title": title}, {"available": title is not None}, True, None

    async def _screen_capture(
        self, args: dict[str, Any]
    ) -> tuple[dict, dict, bool, UndoRecord | None]:
        image = await self.adapter.screenshot(**args)
        return (
            {"format": "png", "bytes": len(image)},
            {"captured": len(image) > 0, "sha256": hashlib.sha256(image).hexdigest()[:16]},
            len(image) > 0,
            None,
        )

    async def _shell_run(self, args: dict[str, Any]) -> tuple[dict, dict, bool, UndoRecord | None]:
        result = await self.adapter.run_shell(
            str(args["command"]), timeout=float(args.get("timeout", 30))
        )
        return result, {"exit_code": result["exit_code"]}, result["exit_code"] == 0, None

    async def _system_process_list(
        self, args: dict[str, Any]
    ) -> tuple[dict, dict, bool, UndoRecord | None]:
        processes = await self.adapter.find_processes(str(args["name"]))
        return (
            {"running": bool(processes), "processes": processes[:10]},
            {"count": len(processes)},
            True,
            None,
        )

    async def _system_info(
        self, args: dict[str, Any]
    ) -> tuple[dict, dict, bool, UndoRecord | None]:
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

    async def _clipboard_read(
        self, args: dict[str, Any]
    ) -> tuple[dict, dict, bool, UndoRecord | None]:
        text = await self.adapter.clipboard_get()
        return {"text": text}, {"length": len(text)}, True, None

    async def _clipboard_write(
        self, args: dict[str, Any]
    ) -> tuple[dict, dict, bool, UndoRecord | None]:
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
                operation="clipboard.write",
                args={"text": previous},
                description="restore the previous clipboard contents",
            ),
        )

    async def _notify_show(
        self, args: dict[str, Any]
    ) -> tuple[dict, dict, bool, UndoRecord | None]:
        await self.adapter.notify(str(args["title"]), str(args["body"]))
        return {"delivered": True}, {}, True, None

    async def _audio_volume_get(
        self, args: dict[str, Any]
    ) -> tuple[dict, dict, bool, UndoRecord | None]:
        level = await self.adapter.get_volume()
        return {"level": level}, {}, True, None

    async def _audio_volume_set(
        self, args: dict[str, Any]
    ) -> tuple[dict, dict, bool, UndoRecord | None]:
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
                operation="audio.volume.set",
                args={"level": previous},
                description="restore the previous volume",
            )
            if previous is not None
            else None
        )
        return {"level": level}, {"readback": readback}, verified, undo

    async def _browser_open(
        self, args: dict[str, Any]
    ) -> tuple[dict, dict, bool, UndoRecord | None]:
        url = str(args["url"])
        if not url.startswith(("http://", "https://")):
            raise ValueError(f"refusing to open a non-http URL: {url!r}")
        result = await self.adapter.open_url(url)
        await asyncio.sleep(LAUNCH_SETTLE_S)
        window = await self.adapter.active_window()
        return {"url": url, **result}, {"active_window": window}, bool(result or window), None

    # ------------------------------------------------------------------ helpers

    def _resolve(self, raw: str) -> Path:
        path = Path(str(raw)).expanduser()
        try:
            resolved = path.resolve()
        except OSError as exc:
            raise ValueError(f"cannot resolve path {raw!r}") from exc
        if not any(resolved == root or root in resolved.parents for root in self.allowed_roots):
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
