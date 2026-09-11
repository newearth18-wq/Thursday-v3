"""Media Agent (§15, V9; editing added in V11).

**What this agent can do depends on the machine it is running on, and it says which.**

Two capabilities, and the line between them is where ffmpeg is:

*Identifying* a file needs nothing at all. The header is read directly here — no library, no
model, no ffmpeg — because "is this actually a PNG or a JPEG somebody renamed", "what are the
dimensions", "how long is this recording" are real questions answered exactly from the first
few dozen bytes. An extension is a claim by whoever named the file; a magic number is
evidence. This half works everywhere, always.

*Editing* needs ffmpeg, and goes out through `media.edit` to the Tool Registry like every
other action that changes something — permission-checked, audited, verified, undoable. When
ffmpeg is not installed the tool refuses with a sentence naming the remedy, and
`describe_capabilities` says so up front rather than letting a plan be built on a capability
that is not there. Editing was ported before it was built, exactly as ADR 0001 describes,
and V11 built it: the port is `thursday_media.ports.MediaEditor` and this agent holds one
without knowing which adapter it is.

The rule that survives from the version of this file that could not edit at all: **nothing
here reports a change that did not happen.** An edit is reported from the quality gate's
reading of the finished file, never from the fact that a render was started.
"""

from __future__ import annotations

import base64
import struct
from dataclasses import dataclass
from typing import Any

from thursday_shared.enums import ModelTier, PermissionLevel
from thursday_shared.models import (
    AgentResult,
    AgentSpec,
    Budget,
    JobContract,
    ToolCall,
)

from thursday_agents.base import BaseAgent

#: Enough of a file to identify it and read its dimensions. Headers are small; reading the
#: whole of a two-gigabyte video to answer "what is this" would be absurd.
HEADER_BYTES = 512


@dataclass(frozen=True)
class MediaInfo:
    """What could be read from the header. Absent fields mean *unknown*, never zero."""

    kind: str = "unknown"  # image | audio | unknown
    format: str = ""
    width: int | None = None
    height: int | None = None
    seconds: float | None = None
    channels: int | None = None

    def describe(self) -> str:
        if self.format and self.width and self.height:
            return f"{self.format} image, {self.width}×{self.height}"
        if self.format and self.seconds is not None:
            return f"{self.format} audio, {self.seconds:.1f}s"
        if self.format:
            return f"{self.format} file"
        return "unrecognised format"


def identify(data: bytes) -> MediaInfo:
    """Read a media header. Format from *content*, not from the file's name.

    An extension is a claim by whoever named the file; a magic number is evidence. The two
    disagree often enough — a downloaded ".jpg" that is really a PNG, a ".wav" that is an
    MP3 — that trusting the name is how a pipeline fails on somebody's holiday photos.
    """
    if len(data) < 12:
        return MediaInfo()

    if data[:8] == b"\x89PNG\r\n\x1a\n":
        # IHDR is always the first chunk, and its width and height are big-endian at a
        # fixed offset. No library needed for the part anybody actually asks about.
        width, height = struct.unpack(">II", data[16:24]) if len(data) >= 24 else (None, None)
        return MediaInfo(kind="image", format="PNG", width=width, height=height)

    if data[:3] == b"\xff\xd8\xff":
        return MediaInfo(kind="image", format="JPEG", **_jpeg_size(data))

    if data[:6] in (b"GIF87a", b"GIF89a"):
        width, height = struct.unpack("<HH", data[6:10])
        return MediaInfo(kind="image", format="GIF", width=width, height=height)

    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return MediaInfo(kind="image", format="WebP")

    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return MediaInfo(kind="audio", format="WAV", **_wav_info(data))

    if data[:3] == b"ID3" or data[:2] in (b"\xff\xfb", b"\xff\xf3"):
        return MediaInfo(kind="audio", format="MP3")

    if data[4:8] == b"ftyp":
        return MediaInfo(kind="video", format="MP4")

    return MediaInfo()


def _jpeg_size(data: bytes) -> dict[str, Any]:
    """Walk JPEG segments to the frame header.

    JPEG puts its dimensions in a start-of-frame marker whose position depends on how much
    metadata came first, so unlike PNG there is no fixed offset to read. Walking the
    segments is the only correct way, and it is twenty lines.
    """
    index = 2
    while index + 9 < len(data):
        if data[index] != 0xFF:
            index += 1
            continue
        marker = data[index + 1]
        # SOF0-SOF15, excluding the four that are not frame headers.
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            height, width = struct.unpack(">HH", data[index + 5 : index + 9])
            return {"width": width, "height": height}
        if index + 4 > len(data):
            break
        length = struct.unpack(">H", data[index + 2 : index + 4])[0]
        index += 2 + length
    return {}


def _wav_info(data: bytes) -> dict[str, Any]:
    """Channels and duration from the fmt and data chunk sizes."""
    if len(data) < 44:
        return {}
    channels, rate = struct.unpack("<HI", data[22:28])
    byte_rate = struct.unpack("<I", data[28:32])[0]
    size = struct.unpack("<I", data[40:44])[0]
    if not byte_rate or not rate:
        return {"channels": channels}
    return {"channels": channels, "seconds": size / byte_rate}


class MediaAgent(BaseAgent):
    spec = AgentSpec(
        name="media",
        description=(
            "Identifies image, audio and video files from their headers, and edits video "
            "locally — trim, join, resize, subtitle, dub — where ffmpeg is installed."
        ),
        capabilities=[
            "media",
            "image",
            "audio",
            "video",
            "inspect",
            "identify",
            "edit",
            "render",
            "subtitle",
            "trim",
            "resize",
        ],
        tools=["file.read", "media.probe", "media.edit"],
        agent_type="specialist",
        supported_input=["path", "plan", "action"],
        supported_output=["info", "deliverable", "quality"],
        output_schema={"info": "dict", "summary": "string", "path": "string"},
        # MODIFY, because editing writes files. It never overwrites one — an edit produces
        # a new file and leaves the original exactly where it was (ADR 0060) — which is why
        # this ceiling is MODIFY rather than anything higher.
        permission_ceiling=PermissionLevel.MODIFY,
        # A render is minutes of CPU, not the thirty seconds header-reading needs.
        default_budget=Budget(seconds=900, tool_calls=6, usd=0.0),
        model_tier=ModelTier.LOCAL,
        cost_profile="free",
        latency_profile="slow",
        # Header parsing is arithmetic and ffmpeg is a local binary. A photograph never
        # leaves the machine to be identified, and a video never leaves it to be edited.
        privacy_profile="local_only",
        system_prompt="",
    )

    def __init__(self, editor: Any = None) -> None:
        """`editor` is a `MediaEditor`. `None` means this agent can identify and not edit —
        the same state as an `UnavailableEditor`, and reported the same way."""
        self._editor = editor

    def describe_capabilities(self) -> dict[str, Any]:
        """What this agent can actually do here, for the UI and for the planner.

        Asked before a plan is built rather than discovered during one. A planner that knows
        editing is unavailable can say so in one sentence; one that finds out at step four
        has already told the owner it was working on their video.
        """
        editing = bool(self._editor is not None and getattr(self._editor, "available", False))
        return {
            "identify": True,
            "edit": editing,
            "detail": (
                "local editing through ffmpeg"
                if editing
                else "identification only — ffmpeg is not installed on this machine"
            ),
        }

    async def execute(self, contract: JobContract, ctx: Any) -> AgentResult:
        if contract.inputs.get("plan"):
            return await self._edit(contract, ctx)

        path = str(contract.inputs.get("path") or "")
        if not path:
            return AgentResult(
                agent=self.spec.name,
                ok=False,
                output={"info": {}, "summary": "", "path": ""},
                error="no file was named",
                summary="no file to inspect",
            )

        read = await ctx.call_tool(
            ToolCall(
                tool="file.read",
                args={"path": path, "bytes": HEADER_BYTES},
                reason="read a media header to identify the file",
            )
        )
        if not read.ok:
            return AgentResult(
                agent=self.spec.name,
                ok=False,
                output={"info": {}, "summary": "", "path": path},
                error=read.error or f"could not read {path}",
                summary=f"could not read {path}",
            )

        info = identify(_bytes_of(read.data))
        summary = f"{path}: {info.describe()}"
        return AgentResult(
            agent=self.spec.name,
            ok=True,
            output={
                "info": {
                    "kind": info.kind,
                    "format": info.format,
                    "width": info.width,
                    "height": info.height,
                    "seconds": info.seconds,
                    "channels": info.channels,
                },
                "summary": summary,
                "path": path,
                # Said in the output, not just in the docstring: nothing downstream should
                # be able to read this agent's result as evidence that a file was changed.
                "modified": False,
            },
            summary=summary,
            evidence=[{"path": path, "format": info.format or "unrecognised"}],
        )

    async def _edit(self, contract: JobContract, ctx: Any) -> AgentResult:
        """Render an edit plan through the tool, and report what the *finished file* holds.

        The three outcomes are kept distinct, because collapsing them is how an assistant
        ends up claiming work it did not do:

        * the tool refused (no ffmpeg, a bad plan) — nothing ran, nothing was written;
        * the render failed partway — the steps that finished are named, and so is the one
          that did not;
        * the render finished — and is reported from the quality gate's reading of the
          output, so a file of the wrong shape or with no sound in it is a failure with the
          reason attached, not a success with a caveat.
        """
        plan = contract.inputs.get("plan")
        if not isinstance(plan, dict):
            return AgentResult(
                agent=self.spec.name,
                ok=False,
                output={"info": {}, "summary": "", "path": ""},
                error="an edit needs a plan describing the steps to run",
                summary="no edit plan",
            )

        args: dict[str, Any] = {"plan": plan}
        for key in ("checkpoint", "expect"):
            if contract.inputs.get(key):
                args[key] = contract.inputs[key]

        result = await ctx.call_tool(
            ToolCall(tool="media.edit", args=args, reason="render the requested edit")
        )
        data = result.data or {}
        deliverable = str(data.get("deliverable") or "")
        quality = data.get("quality") or {}
        summary = str(data.get("summary") or "")

        if not result.ok:
            return AgentResult(
                agent=self.spec.name,
                ok=False,
                output={
                    "info": {},
                    "summary": summary,
                    "path": "",
                    "deliverable": "",
                    "steps": data.get("steps") or [],
                    "quality": quality,
                    # Said explicitly so nothing downstream can read a failed render as a
                    # file that exists.
                    "modified": bool(data.get("steps")),
                },
                error=result.error or "the edit did not finish",
                summary=summary or "the edit did not finish",
            )

        return AgentResult(
            agent=self.spec.name,
            # `verified` is the quality gate's verdict on the finished file. A render that
            # produced the wrong thing is not a success here, whatever ffmpeg returned.
            ok=bool(result.verified),
            output={
                "info": quality,
                "summary": summary,
                "path": deliverable,
                "deliverable": deliverable,
                "steps": data.get("steps") or [],
                "quality": quality,
                "modified": True,
            },
            error=None if result.verified else result.error,
            summary=summary,
            evidence=[{"deliverable": deliverable, "checks": quality.get("checks") or []}],
        )


def _bytes_of(data: dict[str, Any]) -> bytes:
    """Get raw bytes from a node's read result, whichever way it encoded them."""
    if isinstance(data.get("bytes"), bytes | bytearray):
        return bytes(data["bytes"])
    if isinstance(data.get("base64"), str):
        try:
            return base64.b64decode(data["base64"])
        except ValueError:
            return b""
    content = data.get("content")
    if isinstance(content, bytes | bytearray):
        return bytes(content)
    if isinstance(content, str):
        # A node that read a binary file as text has already lost bytes to decoding. Latin-1
        # is the round-trip that preserves the first 256 code points, which is enough for a
        # magic number even when the rest is mangled.
        return content.encode("latin-1", errors="replace")
    return b""
