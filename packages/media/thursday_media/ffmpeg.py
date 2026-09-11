"""The real media editor: ffmpeg, driven by argument lists (ADR 0060).

Every command is built as a list and executed with `asyncio.create_subprocess_exec`. There
is no shell anywhere in this file, which is not a style preference — filenames and subtitle
text reach this module from files, from models and from the owner's own speech, and a shell
would make a filename containing `;` into a command. See `docs/14-threat-model.md`.

Two more rules hold throughout:

**An edit never writes over its input.** `_check_destination` enforces it on every operation,
so it holds for direct callers as well as for plans (`plan.EditPlan.validate` checks the same
thing earlier, where the error is cheaper).

**Nothing is reported as done without being probed.** Every operation ends in
`_finish`, which checks that the destination exists and holds more than a container header
before returning its path. ffmpeg exits zero in situations where it wrote nothing useful —
an empty filter output, a stream that produced no frames — and an exit code is not evidence.
"""

from __future__ import annotations

import asyncio
import re
import shutil
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Any

from thursday_core.logging import get_logger

from thursday_media.ports import (
    Capability,
    EditorHealth,
    ExportPreset,
    MediaProbe,
    MediaUnavailable,
    OperationFailed,
    Overlay,
    StreamInfo,
)

log = get_logger(__name__)

#: How long any one ffmpeg invocation may run. A three-minute promotional video re-encodes
#: in well under this on a laptop; a command still going after it has almost certainly hit
#: an input ffmpeg is waiting on rather than work it is doing.
DEFAULT_TIMEOUT_S = 600.0

#: Audio is normalised to this everywhere so that concatenation and mixing never have to
#: reconcile two sample rates halfway through a filter graph.
SAMPLE_RATE = 44_100

_DURATION = re.compile(r"Duration:\s*(\d+):(\d{2}):(\d{2})\.(\d+)")
_STREAM = re.compile(r"Stream #\d+:\d+[^:]*:\s*(Video|Audio|Subtitle):\s*([A-Za-z0-9_]+)")
_SIZE = re.compile(r"(?<![\d.])(\d{2,5})x(\d{2,5})(?![\d.])")
_FPS = re.compile(r"([\d.]+)\s*fps")
_HZ = re.compile(r"(\d+)\s*Hz")
_CHANNELS = re.compile(r"\b(mono|stereo|(\d+)(?:\.\d+)? channels|5\.1|7\.1)\b")

_CORNERS = {
    "top-left": ("{m}", "{m}"),
    "top-right": ("W-w-{m}", "{m}"),
    "bottom-left": ("{m}", "H-h-{m}"),
    "bottom-right": ("W-w-{m}", "H-h-{m}"),
    "center": ("(W-w)/2", "(H-h)/2"),
}


def discover(explicit: str | None = None) -> tuple[str, str]:
    """Find ffmpeg and ffprobe, in the order a machine is likely to have them.

    An explicit setting wins, then the PATH, then the `imageio-ffmpeg` wheel — which ships a
    static build and is how a machine with no system ffmpeg ends up able to edit anyway.
    Returns empty strings rather than raising: *not finding ffmpeg is a supported state*,
    reported through `health()` and turned into `MediaUnavailable` at the point of use, so
    the UI can say "not installed" instead of showing a traceback.
    """
    if explicit:
        path = Path(explicit).expanduser()
        if path.is_file():
            probe = path.with_name("ffprobe" + path.suffix)
            return str(path), (str(probe) if probe.is_file() else shutil.which("ffprobe") or "")
        log.warning("ffmpeg_path_not_a_file", configured=explicit)

    found = shutil.which("ffmpeg")
    if found:
        return found, shutil.which("ffprobe") or ""

    try:  # pragma: no cover - depends on an optional wheel being installed
        import imageio_ffmpeg

        bundled = imageio_ffmpeg.get_ffmpeg_exe()
        if bundled and Path(bundled).is_file():
            # The wheel ships ffmpeg and not ffprobe, which is why `probe()` can read a file
            # without one.
            return str(bundled), shutil.which("ffprobe") or ""
    except (ImportError, RuntimeError, OSError):
        pass

    return "", ""


def _channels_from(text: str) -> int | None:
    match = _CHANNELS.search(text)
    if not match:
        return None
    layout = match.group(1)
    if layout == "mono":
        return 1
    if layout == "stereo":
        return 2
    if layout == "5.1":
        return 6
    if layout == "7.1":
        return 8
    return int(match.group(2)) if match.group(2) else None


def parse_probe(text: str, *, path: str = "", size_bytes: int = 0) -> MediaProbe:
    """Read ffmpeg's own description of a file off its stderr.

    Preferred over ffprobe's JSON only because ffprobe is not always installed — the
    `imageio-ffmpeg` wheel ships one binary, not two — and a machine that can edit should be
    able to probe. Exposed as a module function so the parser is testable against captured
    output without a subprocess (`tests/unit/test_ffmpeg_probe_v11.py`).
    """
    seconds: float | None = None
    duration = _DURATION.search(text)
    if duration:
        hours, minutes, secs, fraction = duration.groups()
        seconds = (
            int(hours) * 3600
            + int(minutes) * 60
            + int(secs)
            + int(fraction) / (10 ** len(fraction))
        )

    streams: list[StreamInfo] = []
    for line in text.splitlines():
        match = _STREAM.search(line)
        if not match:
            continue
        kind, codec = match.group(1).lower(), match.group(2)
        if kind == "video":
            size = _SIZE.search(line)
            fps = _FPS.search(line)
            streams.append(
                StreamInfo(
                    kind="video",
                    codec=codec,
                    width=int(size.group(1)) if size else None,
                    height=int(size.group(2)) if size else None,
                    fps=float(fps.group(1)) if fps else None,
                )
            )
        elif kind == "audio":
            hz = _HZ.search(line)
            streams.append(
                StreamInfo(
                    kind="audio",
                    codec=codec,
                    sample_rate=int(hz.group(1)) if hz else None,
                    channels=_channels_from(line),
                )
            )
        else:
            streams.append(StreamInfo(kind="subtitle", codec=codec))

    container = ""
    head = re.search(r"Input #0,\s*([^,]+)", text)
    if head:
        container = head.group(1).strip()

    return MediaProbe(
        path=path,
        seconds=seconds,
        container=container,
        size_bytes=size_bytes,
        streams=tuple(streams),
    )


class FFmpegEditor:
    """Media editing through a local ffmpeg. No network, no model, no cloud."""

    def __init__(
        self,
        ffmpeg: str = "",
        ffprobe: str = "",
        *,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        allowed_roots: tuple[Path, ...] = (),
    ) -> None:
        self.name = "ffmpeg"
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe
        self.timeout_s = timeout_s
        #: When set, every path an operation touches must sit inside one of these. The same
        #: path jail the device node uses (§9), for the same reason: a plan can name a file,
        #: and a plan can come from a model.
        self.allowed_roots = tuple(Path(r).expanduser().resolve() for r in allowed_roots)
        self._version = ""
        self._encoders: frozenset[str] = frozenset()

    @classmethod
    def discovered(
        cls,
        explicit: str | None = None,
        *,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        allowed_roots: tuple[Path, ...] = (),
    ) -> FFmpegEditor:
        ffmpeg, ffprobe = discover(explicit)
        return cls(ffmpeg, ffprobe, timeout_s=timeout_s, allowed_roots=allowed_roots)

    @property
    def available(self) -> bool:
        return bool(self.ffmpeg)

    # ------------------------------------------------------------------ running the binary

    async def _run(self, args: list[str], *, cwd: Path | None = None) -> tuple[int, str]:
        """One ffmpeg invocation. Returns its exit code and stderr, which is where ffmpeg
        writes everything worth reading."""
        if not self.available:
            raise MediaUnavailable(
                "ffmpeg is not installed on this machine, so Thursday cannot edit media "
                "here — install ffmpeg and restart, or set THURSDAY_FFMPEG_PATH to it"
            )

        command = [self.ffmpeg, "-hide_banner", "-nostdin", *args]
        log.debug("ffmpeg_run", args=args[:8])
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(cwd) if cwd else None,
        )
        try:
            _, stderr = await asyncio.wait_for(process.communicate(), timeout=self.timeout_s)
        except TimeoutError:
            process.kill()
            with suppress(ProcessLookupError):
                await process.wait()
            raise OperationFailed(
                f"ffmpeg did not finish within {self.timeout_s:.0f}s and was stopped"
            ) from None
        return process.returncode or 0, stderr.decode("utf-8", errors="replace")

    async def _ask(self, args: list[str]) -> str:
        """Ask ffmpeg about itself. Reads *stdout*, which is where `-version`, `-encoders`
        and `-filters` write — unlike every other invocation in this file, whose output is
        the stderr log. Getting this wrong reports a fully capable build as having no
        encoders at all."""
        if not self.available:
            return ""
        process = await asyncio.create_subprocess_exec(
            self.ffmpeg,
            "-hide_banner",
            "-nostdin",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=self.timeout_s)
        except TimeoutError:
            process.kill()
            with suppress(ProcessLookupError):
                await process.wait()
            return ""
        return stdout.decode("utf-8", errors="replace")

    async def _must_run(self, args: list[str], *, what: str, cwd: Path | None = None) -> str:
        code, stderr = await self._run(args, cwd=cwd)
        if code != 0:
            raise OperationFailed(f"{what} failed: {_last_error(stderr)}", exit_code=code)
        return stderr

    # ------------------------------------------------------------------------- guard rails

    def _resolve(self, path: str, *, kind: str) -> Path:
        resolved = Path(path).expanduser().resolve()
        if self.allowed_roots and not any(
            resolved == root or root in resolved.parents for root in self.allowed_roots
        ):
            raise OperationFailed(f"{kind} {resolved} is outside the folders Thursday may work in")
        return resolved

    def _check_sources(self, sources: list[str]) -> list[Path]:
        resolved = []
        for source in sources:
            path = self._resolve(source, kind="input")
            if not path.exists():
                raise OperationFailed(f"there is no file at {path}")
            if path.stat().st_size == 0:
                raise OperationFailed(f"{path} is empty")
            resolved.append(path)
        return resolved

    def _check_destination(self, dst: str, sources: list[Path]) -> Path:
        path = self._resolve(dst, kind="output")
        if path in sources:
            raise OperationFailed(
                f"refusing to write {path} — it is also an input, and an edit must never "
                "overwrite the file it read"
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    async def _finish(self, dst: Path, *, what: str) -> str:
        """Check that the thing we said we made is actually there."""
        size = await asyncio.to_thread(_size_of, dst)
        if size is None:
            raise OperationFailed(f"{what} reported success but wrote no file at {dst}")
        if size < 1024:
            raise OperationFailed(
                f"{what} wrote only {size} bytes to {dst.name} — that is a container header "
                "with no content in it"
            )
        return str(dst)

    # ------------------------------------------------------------------------------- probe

    async def probe(self, path: str) -> MediaProbe:
        """Read what is actually in a file.

        A file that is *there* but unreadable comes back as an empty `MediaProbe` rather
        than an exception — "nothing could be read from this" is an honest answer and the
        quality gate knows to treat it as `unknown`. A file that is not there at all raises,
        because that is a different fact and the caller can act on it.
        """
        target = self._resolve(path, kind="input")
        size = await asyncio.to_thread(_size_of, target)
        if size is None:
            raise OperationFailed(f"there is no file at {target}")

        if self.ffprobe:
            code, text = await self._probe_with_ffprobe(target)
            if code == 0:
                return _from_ffprobe_json(text, path=str(target), size_bytes=size)

        # ffmpeg with no output writes its description of the input to stderr and exits
        # non-zero for want of an output file. That exit code is expected, not a failure.
        _, stderr = await self._run(["-i", str(target)])
        return parse_probe(stderr, path=str(target), size_bytes=size)

    async def _probe_with_ffprobe(self, target: Path) -> tuple[int, str]:
        process = await asyncio.create_subprocess_exec(
            self.ffprobe,
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(target),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=self.timeout_s)
        except TimeoutError:
            process.kill()
            with suppress(ProcessLookupError):
                await process.wait()
            return 1, ""
        return process.returncode or 0, stdout.decode("utf-8", errors="replace")

    async def _seconds(self, path: Path) -> float:
        probed = await self.probe(str(path))
        if probed.seconds is None:
            raise OperationFailed(
                f"the length of {path.name} could not be read, and this edit needs it"
            )
        return probed.seconds

    # -------------------------------------------------------------------------- operations

    async def trim(self, src: str, dst: str, *, start: float, end: float | None = None) -> str:
        """Cut a section out. Re-encodes rather than stream-copying.

        A stream copy is far faster and cuts at the nearest keyframe, which for a 30fps
        H.264 file can be two seconds from where the owner asked. For clips this short, an
        exact cut is worth the encode.
        """
        sources = self._check_sources([src])
        target = self._check_destination(dst, sources)
        if start < 0:
            raise OperationFailed(f"a clip cannot start at {start}s")
        if end is not None and end <= start:
            raise OperationFailed(f"a clip from {start}s to {end}s has no length")

        args = ["-y", "-i", str(sources[0]), "-ss", f"{start:.3f}"]
        if end is not None:
            args += ["-to", f"{end:.3f}"]
        args += [*_VIDEO_OUT, *_AUDIO_OUT, str(target)]
        await self._must_run(args, what=f"trimming {sources[0].name}")
        return await self._finish(target, what="trim")

    async def concat(self, sources: list[str], dst: str) -> str:
        """Join clips end to end, normalising them first.

        The concat *filter* requires every input to share a frame size, pixel format and
        sample rate, and real material never does — a phone clip, a screen recording and a
        still are three different shapes. So each input is scaled and padded to the first
        one's frame before joining, and an input with no audio gets silence of its own
        length rather than being dropped out of the audio graph, which is what makes the
        sound jump forward against the picture.
        """
        if len(sources) < 2:
            raise OperationFailed("concatenating needs at least two clips")
        paths = self._check_sources(sources)
        target = self._check_destination(dst, paths)

        probes = [await self.probe(str(p)) for p in paths]
        first_video = next((p.video for p in probes if p.video), None)
        if first_video is None or not first_video.width or not first_video.height:
            raise OperationFailed("none of these clips has a readable video stream")
        width, height = first_video.width, first_video.height
        fps = round(first_video.fps or 30)
        with_audio = any(p.has_audio for p in probes)

        args: list[str] = ["-y"]
        for path in paths:
            args += ["-i", str(path)]
        if with_audio and not all(p.has_audio for p in probes):
            args += [
                "-f",
                "lavfi",
                "-i",
                f"anullsrc=channel_layout=stereo:sample_rate={SAMPLE_RATE}",
            ]
        silence_index = len(paths)

        chains: list[str] = []
        labels: list[str] = []
        for index, probe in enumerate(probes):
            chains.append(f"[{index}:v]{_fit(width, height)},fps={fps},format=yuv420p[v{index}]")
            labels.append(f"[v{index}]")
            if not with_audio:
                continue
            if probe.has_audio:
                chains.append(
                    f"[{index}:a]aresample={SAMPLE_RATE},aformat=channel_layouts=stereo[a{index}]"
                )
            else:
                length = probe.seconds if probe.seconds is not None else 0.0
                if length <= 0:
                    raise OperationFailed(
                        f"{paths[index].name} has no audio and no readable length, so the "
                        "silence to put in its place cannot be measured"
                    )
                chains.append(
                    f"[{silence_index}:a]atrim=0:{length:.3f},asetpts=PTS-STARTPTS[a{index}]"
                )
            labels.append(f"[a{index}]")

        streams = 2 if with_audio else 1
        joined = "".join(labels)
        outputs = "[v][a]" if with_audio else "[v]"
        chains.append(f"{joined}concat=n={len(paths)}:v=1:a={1 if with_audio else 0}{outputs}")

        args += ["-filter_complex", ";".join(chains), "-map", "[v]"]
        if with_audio:
            args += ["-map", "[a]", *_AUDIO_OUT]
        args += [*_VIDEO_OUT, str(target)]
        await self._must_run(args, what=f"joining {len(paths)} clips")
        log.info("media_concat", clips=len(paths), streams=streams, output=target.name)
        return await self._finish(target, what="concat")

    async def resize(self, src: str, dst: str, *, target: ExportPreset) -> str:
        """Fit the picture into a preset without cropping it.

        Scale-then-pad rather than crop-to-fill, because the material this runs on is
        usually somebody's event photos and a crop that fills a 9:16 frame from a 16:9
        photo removes a third of the picture — including, reliably, somebody's head.
        """
        sources = self._check_sources([src])
        out = self._check_destination(dst, sources)
        args = [
            "-y",
            "-i",
            str(sources[0]),
            "-vf",
            f"{_fit(target.width, target.height)},fps={target.fps},format=yuv420p",
            *_VIDEO_OUT,
            "-crf",
            str(target.crf),
        ]
        probe = await self.probe(str(sources[0]))
        if probe.has_audio:
            args += ["-c:a", "aac", "-b:a", target.audio_bitrate]
        else:
            args += ["-an"]
        args.append(str(out))
        await self._must_run(args, what=f"resizing to {target.aspect}")
        return await self._finish(out, what="resize")

    async def still(self, image: str, dst: str, *, seconds: float, target: ExportPreset) -> str:
        """Turn one image into a clip of a given length. Silent, by design.

        Storyboard frames become video this way, and the narration is laid over the joined
        result rather than over each frame — so a still carries no audio track at all and
        `concat` supplies silence where it needs to.
        """
        if seconds <= 0:
            raise OperationFailed(f"a still cannot be {seconds}s long")
        sources = self._check_sources([image])
        out = self._check_destination(dst, sources)
        args = [
            "-y",
            "-loop",
            "1",
            "-i",
            str(sources[0]),
            "-t",
            f"{seconds:.3f}",
            "-vf",
            f"{_fit(target.width, target.height)},fps={target.fps},format=yuv420p",
            *_VIDEO_OUT,
            "-crf",
            str(target.crf),
            "-an",
            str(out),
        ]
        await self._must_run(args, what=f"making a {seconds:.1f}s clip from {sources[0].name}")
        return await self._finish(out, what="still")

    async def burn_subtitles(self, src: str, dst: str, *, subtitles: str) -> str:
        """Render an SRT into the picture.

        The subtitle path is copied to a temporary directory under a fixed plain name and
        ffmpeg is run from there. ffmpeg's filter syntax gives `:`, `'` and `\\` meaning
        inside an argument, so a real path — `C:\\Users\\...` on Windows, or anything with an
        apostrophe in it — has to be escaped into that syntax, and an escaping bug here is a
        filter-injection bug. Copying to `subs.srt` and passing a bare filename removes the
        question rather than answering it.
        """
        sources = self._check_sources([src, subtitles])
        out = self._check_destination(dst, sources)
        video, track = sources

        with tempfile.TemporaryDirectory(prefix="thursday-subs-") as workspace:
            staged = Path(workspace) / "subs.srt"
            await asyncio.to_thread(staged.write_bytes, track.read_bytes())
            probe = await self.probe(str(video))
            # Built once, conditionally. The previous version appended "-c:a copy" and then
            # `list.remove`d both tokens for a silent input, which works until a flag with
            # the same spelling appears earlier in the list and the wrong one is removed.
            audio = ["-c:a", "copy"] if probe.has_audio else ["-an"]
            args = [
                "-y",
                "-i",
                str(video),
                "-vf",
                f"subtitles=subs.srt:force_style='{_SUBTITLE_STYLE}'",
                *_VIDEO_OUT,
                *audio,
                str(out),
            ]
            await self._must_run(args, what="burning in subtitles", cwd=Path(workspace))
        return await self._finish(out, what="burn_subtitles")

    async def dub(
        self,
        src: str,
        dst: str,
        *,
        narration: str | None = None,
        music: str | None = None,
        music_gain_db: float = -18.0,
    ) -> str:
        """Lay narration and/or music over a video.

        Music is looped to the length of the picture and turned down; narration is not
        touched. The output is cut to the video's own duration with an explicit `-t` rather
        than `-shortest`, because `-shortest` means "stop at whichever stream ends first" —
        and a thirty-second backing track under a ninety-second video would silently
        produce a thirty-second video.
        """
        if not narration and not music:
            raise OperationFailed("dubbing needs narration, music, or both")

        wanted = [src] + [p for p in (narration, music) if p]
        sources = self._check_sources(wanted)
        out = self._check_destination(dst, sources)
        video = sources[0]
        original = await self.probe(str(video))
        if not original.has_video:
            # Caught here rather than left to ffmpeg, which fails on `-map 0:v` with a
            # message about stream specifiers that says nothing about what to do instead.
            raise OperationFailed(
                f"{video.name} has no picture to dub onto — to join audio files, mix them "
                "into a clip that has one"
            )
        length = await self._seconds(video)

        args: list[str] = ["-y", "-i", str(video)]
        index = 1
        chains: list[str] = []
        mix: list[str] = []

        if original.has_audio:
            chains.append(f"[0:a]aresample={SAMPLE_RATE},aformat=channel_layouts=stereo[orig]")
            mix.append("[orig]")
        if narration:
            args += ["-i", str(sources[index])]
            chains.append(
                f"[{index}:a]aresample={SAMPLE_RATE},aformat=channel_layouts=stereo[narr]"
            )
            mix.append("[narr]")
            index += 1
        if music:
            # Looped at the input, so a short track covers a long video instead of ending.
            args += ["-stream_loop", "-1", "-i", str(sources[index])]
            chains.append(
                f"[{index}:a]aresample={SAMPLE_RATE},aformat=channel_layouts=stereo,"
                f"volume={music_gain_db:.1f}dB[music]"
            )
            mix.append("[music]")

        if len(mix) == 1:
            chains.append(f"{mix[0]}atrim=0:{length:.3f},asetpts=PTS-STARTPTS[out]")
        else:
            chains.append(
                f"{''.join(mix)}amix=inputs={len(mix)}:duration=longest:normalize=0,"
                f"atrim=0:{length:.3f},asetpts=PTS-STARTPTS[out]"
            )

        args += [
            "-filter_complex",
            ";".join(chains),
            "-map",
            "0:v",
            "-map",
            "[out]",
            "-c:v",
            "copy",
            *_AUDIO_OUT,
            "-t",
            f"{length:.3f}",
            str(out),
        ]
        await self._must_run(args, what="adding audio")
        return await self._finish(out, what="dub")

    async def normalize_audio(self, src: str, dst: str) -> str:
        """EBU R128 loudness normalisation, to the level streaming platforms expect.

        One pass. A two-pass loudnorm measures the file and then applies exact corrections,
        which is more accurate; one pass is deterministic enough for narration and does not
        double the encode time of every render.
        """
        sources = self._check_sources([src])
        out = self._check_destination(dst, sources)
        probe = await self.probe(str(sources[0]))
        if not probe.has_audio:
            raise OperationFailed(f"{sources[0].name} has no audio to normalise")

        args = ["-y", "-i", str(sources[0]), "-af", "loudnorm=I=-16:TP=-1.5:LRA=11"]
        args += ["-c:v", "copy"] if probe.has_video else []
        args += [*_AUDIO_OUT, str(out)]
        await self._must_run(args, what="normalising loudness")
        return await self._finish(out, what="normalize_audio")

    async def strip_silence(self, src: str, dst: str, *, threshold_db: float = -45.0) -> str:
        """Remove silent stretches from an audio file.

        **Audio only, and it refuses rather than guessing on video.** Cutting silence out of
        a soundtrack while leaving the picture alone puts the two out of sync for the rest
        of the video, and doing it properly means cutting the picture at the same points —
        a different and much larger operation. Refusing here is the honest half of the
        brief's "silence removal": the case this is actually used for is tightening a
        generated narration before it is dubbed onto anything.
        """
        sources = self._check_sources([src])
        out = self._check_destination(dst, sources)
        probe = await self.probe(str(sources[0]))
        if probe.has_video:
            raise OperationFailed(
                f"{sources[0].name} has a picture as well as a soundtrack. Cutting silence "
                "out of the audio alone would put it out of sync with the video, so "
                "Thursday will not do it — strip the silence from the narration before it "
                "is dubbed on"
            )
        if not probe.has_audio:
            raise OperationFailed(f"{sources[0].name} has no audio in it")

        args = [
            "-y",
            "-i",
            str(sources[0]),
            "-af",
            (
                f"silenceremove=start_periods=1:start_threshold={threshold_db:.1f}dB:"
                f"start_silence=0.2:stop_periods=-1:stop_threshold={threshold_db:.1f}dB:"
                "stop_silence=0.3:detection=rms"
            ),
            *_AUDIO_OUT,
            str(out),
        ]
        await self._must_run(args, what="removing silence")
        return await self._finish(out, what="strip_silence")

    async def overlay(self, src: str, dst: str, *, overlays: list[Overlay]) -> str:
        """Lay images over the picture — a logo, a lower third, a school crest."""
        if not overlays:
            raise OperationFailed("no overlay was given")
        sources = self._check_sources([src, *(o.image for o in overlays)])
        out = self._check_destination(dst, sources)
        video = sources[0]

        args: list[str] = ["-y", "-i", str(video)]
        for image in sources[1:]:
            args += ["-i", str(image)]

        chains: list[str] = []
        current = "[0:v]"
        for index, item in enumerate(overlays, start=1):
            if item.corner not in _CORNERS:
                raise OperationFailed(
                    f"{item.corner!r} is not a corner — use one of {', '.join(_CORNERS)}"
                )
            opacity = min(1.0, max(0.0, item.opacity))
            chains.append(f"[{index}:v]format=rgba,colorchannelmixer=aa={opacity:.3f}[ov{index}]")
            x, y = (part.format(m=item.margin) for part in _CORNERS[item.corner])
            enable = ""
            if item.start > 0 or item.end is not None:
                end = item.end if item.end is not None else 1e9
                enable = f":enable='between(t,{item.start:.3f},{end:.3f})'"
            label = f"[v{index}]" if index < len(overlays) else "[v]"
            chains.append(f"{current}[ov{index}]overlay={x}:{y}{enable}{label}")
            current = label

        probe = await self.probe(str(video))
        args += ["-filter_complex", ";".join(chains), "-map", "[v]"]
        args += ["-map", "0:a", "-c:a", "copy"] if probe.has_audio else []
        args += [*_VIDEO_OUT, str(out)]
        await self._must_run(args, what="laying on overlays")
        return await self._finish(out, what="overlay")

    async def crossfade(self, a: str, b: str, dst: str, *, seconds: float = 0.5) -> str:
        """Dissolve from one clip into the next."""
        if seconds <= 0:
            raise OperationFailed(f"a crossfade cannot last {seconds}s")
        sources = self._check_sources([a, b])
        out = self._check_destination(dst, sources)
        first, second = sources

        probes = [await self.probe(str(p)) for p in sources]
        length = probes[0].seconds
        if length is None:
            raise OperationFailed(f"the length of {first.name} could not be read")
        if length <= seconds:
            raise OperationFailed(
                f"{first.name} is {length:.2f}s long and cannot hold a {seconds:.2f}s "
                "crossfade at its end"
            )
        video = probes[0].video
        if video is None or not video.width or not video.height:
            raise OperationFailed(f"{first.name} has no readable video stream")

        offset = length - seconds
        fit = _fit(video.width, video.height)
        fps = round(video.fps or 30)
        chains = [
            f"[0:v]{fit},fps={fps},format=yuv420p[x0]",
            f"[1:v]{fit},fps={fps},format=yuv420p[x1]",
            f"[x0][x1]xfade=transition=fade:duration={seconds:.3f}:offset={offset:.3f}[v]",
        ]
        args = ["-y", "-i", str(first), "-i", str(second)]
        both_have_audio = all(p.has_audio for p in probes)
        if both_have_audio:
            chains.append(f"[0:a][1:a]acrossfade=d={seconds:.3f}:c1=tri:c2=tri[aout]")
        args += ["-filter_complex", ";".join(chains), "-map", "[v]"]
        args += ["-map", "[aout]", *_AUDIO_OUT] if both_have_audio else []
        args += [*_VIDEO_OUT, str(out)]
        await self._must_run(args, what="crossfading")
        return await self._finish(out, what="crossfade")

    async def thumbnail(self, src: str, dst: str, *, at: float = 0.0) -> str:
        """One frame, as a PNG. Used for a video's cover image."""
        sources = self._check_sources([src])
        out = self._check_destination(dst, sources)
        if at < 0:
            raise OperationFailed(f"cannot take a frame at {at}s")
        args = ["-y", "-ss", f"{at:.3f}", "-i", str(sources[0]), "-frames:v", "1", str(out)]
        await self._must_run(args, what=f"taking a frame at {at:.1f}s")
        # A PNG of a flat title card is legitimately tiny, so this checks for *nothing*
        # rather than applying `_finish`'s container-header floor.
        if not await asyncio.to_thread(_size_of, out):
            raise OperationFailed(f"no frame at {at:.1f}s — the video may be shorter than that")
        return str(out)

    # ------------------------------------------------------------------------------ health

    async def probe_capabilities(self) -> EditorHealth:
        """What this ffmpeg can actually do, asked of the binary rather than assumed.

        Builds differ. A distribution package without libx264 cannot produce H.264, and
        finding that out at the end of a render is finding it out at the worst moment.
        """
        if not self.available:
            return EditorHealth(
                backend="none",
                available=False,
                detail=(
                    "ffmpeg was not found on the PATH, in THURSDAY_FFMPEG_PATH, or in an "
                    "installed imageio-ffmpeg. Media editing is unavailable on this machine."
                ),
            )

        text = await self._ask(["-version"])
        first = text.splitlines()[0] if text.splitlines() else ""
        match = re.search(r"ffmpeg version (\S+)", first)
        version = match.group(1) if match else first[:40]
        self._version = version

        self._encoders = _names(await self._ask(["-encoders"]))
        names = _names(await self._ask(["-filters"]))

        capabilities = [
            Capability("h264", "libx264" in self._encoders, "libx264 encoder"),
            Capability("aac", "aac" in self._encoders, "AAC audio encoder"),
            Capability("subtitles", "subtitles" in names, "libass subtitle rendering"),
            Capability("loudness", "loudnorm" in names, "EBU R128 normalisation"),
            Capability("silence", "silenceremove" in names, "silence removal"),
            Capability("transitions", "xfade" in names, "crossfades"),
            Capability("overlay", "overlay" in names, "image overlays"),
        ]
        missing = [c.name for c in capabilities if not c.ok]
        return EditorHealth(
            backend="ffmpeg",
            available=True,
            version=version,
            detail=(
                f"ffmpeg {version}"
                + (f" — missing: {', '.join(missing)}" if missing else " — all operations")
            ),
            capabilities=capabilities,
        )

    def health(self) -> dict[str, Any]:
        """The cheap synchronous view, for the health endpoint."""
        return EditorHealth(
            backend="ffmpeg" if self.available else "none",
            available=self.available,
            version=self._version,
            detail=(
                f"ffmpeg at {self.ffmpeg}"
                if self.available
                else "ffmpeg is not installed — media editing is unavailable"
            ),
        ).to_dict()


#: Scale into the frame without distorting, then letterbox the rest. Used by every operation
#: that changes a frame size, so they cannot drift apart.
def _fit(width: int, height: int) -> str:
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1"
    )


#: Shared output settings. `yuv420p` is not optional: x264 defaults to a pixel format that
#: QuickTime and most phones will not play, and a video the owner cannot open on their phone
#: is a failed render that looks like a successful one.
_VIDEO_OUT = ("-c:v", "libx264", "-preset", "medium", "-pix_fmt", "yuv420p")
_AUDIO_OUT = ("-c:a", "aac", "-b:a", "128k", "-ar", str(SAMPLE_RATE))

_SUBTITLE_STYLE = (
    "FontSize=22,PrimaryColour=&H00FFFFFF,OutlineColour=&H90000000,"
    "BorderStyle=3,Outline=1,Shadow=0,MarginV=40"
)


def _size_of(path: Path) -> int | None:
    """A file's size, or `None` when it is not there. One syscall, off the event loop."""
    try:
        return path.stat().st_size
    except OSError:
        return None


def _names(listing: str) -> frozenset[str]:
    """The second column of an `ffmpeg -encoders` / `-filters` table.

    The tables put capability flags in the first column (`V....D`, `TSC`) and the name in
    the second, after a header block that this skips by requiring the flag column to hold
    no spaces and the line to carry at least two more fields.
    """
    found: set[str] = set()
    for line in listing.splitlines():
        parts = line.split()
        if len(parts) >= 3 and not line.startswith((" -", "-")) and "=" not in parts[0]:
            found.add(parts[1])
    return frozenset(found)


def _last_error(stderr: str) -> str:
    """The line worth showing the owner out of a hundred lines of ffmpeg output."""
    lines = [line.strip() for line in stderr.strip().splitlines() if line.strip()]
    for line in reversed(lines):
        lowered = line.lower()
        if any(
            word in lowered
            for word in ("error", "invalid", "no such file", "unable", "failed", "not found")
        ):
            return line[:300]
    return lines[-1][:300] if lines else "no output"


def _from_ffprobe_json(text: str, *, path: str, size_bytes: int) -> MediaProbe:
    """ffprobe's JSON, when the machine has ffprobe. Same shape as the stderr parser."""
    import json

    try:
        raw = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return MediaProbe(path=path, size_bytes=size_bytes)

    streams: list[StreamInfo] = []
    for stream in raw.get("streams") or []:
        kind = str(stream.get("codec_type") or "unknown")
        if kind == "video":
            streams.append(
                StreamInfo(
                    kind="video",
                    codec=str(stream.get("codec_name") or ""),
                    width=_int(stream.get("width")),
                    height=_int(stream.get("height")),
                    fps=_rate(stream.get("avg_frame_rate")),
                )
            )
        elif kind == "audio":
            streams.append(
                StreamInfo(
                    kind="audio",
                    codec=str(stream.get("codec_name") or ""),
                    sample_rate=_int(stream.get("sample_rate")),
                    channels=_int(stream.get("channels")),
                )
            )
        else:
            streams.append(StreamInfo(kind=kind, codec=str(stream.get("codec_name") or "")))

    container = str((raw.get("format") or {}).get("format_name") or "")
    seconds = _float((raw.get("format") or {}).get("duration"))
    return MediaProbe(
        path=path,
        seconds=seconds,
        container=container,
        size_bytes=size_bytes,
        streams=tuple(streams),
    )


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _rate(value: Any) -> float | None:
    if not isinstance(value, str) or "/" not in value:
        return _float(value)
    numerator, _, denominator = value.partition("/")
    try:
        den = float(denominator)
        return float(numerator) / den if den else None
    except (TypeError, ValueError, ZeroDivisionError):
        return None
