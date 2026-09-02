"""Media Agent (§15, V9).

**This agent inspects media. It cannot edit, convert, resize or generate any.**

That is the first line of the file because it is the thing most likely to be assumed
otherwise. There is no Pillow here, no ffmpeg, no codec of any kind — so an agent that
offered to "compress these photos" would be an agent that fails at the point of use, after
the owner has already decided to rely on it. The honest version does the part that needs no
library at all: it reads the header, and tells you what the file actually is.

That is less than it sounds and more than nothing. "Is this actually a PNG or is it a JPEG
somebody renamed", "what are the dimensions", "how long is this recording" are real
questions, answered exactly, from the first few dozen bytes. Format identification from
*content* rather than extension is the useful part: an extension is a claim by whoever named
the file, and a header is evidence.

When the libraries arrive, editing belongs behind a port and an adapter like everything else
(ADR 0001). It does not belong bolted onto this agent, and it is not claimed here.
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
        description="Identifies image, audio and video files from their headers. Cannot edit them.",
        capabilities=["media", "image", "audio", "inspect", "identify"],
        tools=["file.read"],
        agent_type="specialist",
        supported_input=["path"],
        supported_output=["info"],
        output_schema={"info": "dict", "summary": "string", "path": "string"},
        # READ, and nothing here could want more: it looks at bytes and reports.
        permission_ceiling=PermissionLevel.READ,
        default_budget=Budget(seconds=30, tool_calls=2, usd=0.0),
        model_tier=ModelTier.LOCAL,
        cost_profile="free",
        latency_profile="instant",
        # Header parsing is arithmetic. A photograph never leaves the machine to be
        # identified, and no model is involved at all.
        privacy_profile="local_only",
        system_prompt="",
    )

    async def execute(self, contract: JobContract, ctx: Any) -> AgentResult:
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
