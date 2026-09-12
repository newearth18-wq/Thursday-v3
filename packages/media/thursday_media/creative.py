"""The creative workflow: a script and some pictures become a finished video (§15, V11).

This is the assembly half of the brief's headline request — *"make a short promotional video
for tomorrow's school event"* — and it is deliberately only the assembly half. Research,
scriptwriting, image generation and narration are model work that happens elsewhere and
arrives here as **inputs**. What this module owns is turning those inputs into an `EditPlan`
that a local ffmpeg can render deterministically, and saying plainly which inputs are
missing.

That split is the honest one. `compose` never invents a picture it was not given and never
pretends to have narrated anything: a request with no images comes back `ready=False` with
`missing` naming what it needs, so the caller can go and get it rather than discovering
halfway through a render that there was nothing to render.

The interesting piece is **where the subtitle timings come from**, which falls straight out
of how the narration was supplied:

* One audio file for the whole script → scene lengths are estimated from reading speed, and
  the track is marked `estimated`.
* One audio file per line → each scene is exactly as long as the line that is spoken over
  it, the narration is laid on scene by scene, and the track is `measured` — every cue is
  on the frame the voice starts.

Nothing upgrades the first case into the second. A video with guessed timings is a perfectly
good video, and it is not the same claim as a synchronised one.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from thursday_core.logging import get_logger

from thursday_media.plan import EditPlan, EditStep
from thursday_media.ports import ExportPreset, OperationFailed, preset
from thursday_media.subtitles import SubtitleTrack, from_lines, speaking_seconds, to_srt

log = get_logger(__name__)

#: A scene with no narration and no stated length gets this long. Short enough that a
#: title card does not outstay its welcome, long enough to be read.
DEFAULT_SCENE_SECONDS = 3.0


@dataclass
class Scene:
    """One beat of the video: something to show, and something said over it."""

    text: str = ""
    #: A storyboard frame — a photograph, a generated image, a rendered title card.
    image: str = ""
    #: An explicit length. Ignored when this scene has its own narration file, because the
    #: narration's real length is better information than an estimate.
    seconds: float | None = None
    #: Audio for this line alone. Present for every scene or for none; a mixture is
    #: refused, because it would produce a track that is part measured and part guessed.
    narration: str = ""


@dataclass
class PromoRequest:
    """Everything needed to assemble a short video, and nothing that needs a model."""

    name: str = "promo"
    title: str = ""
    scenes: list[Scene] = field(default_factory=list)
    workdir: Path = Path(".")
    aspect: str = "16:9"
    #: One file covering the whole script. Mutually exclusive with per-scene narration.
    narration: str = ""
    #: Speak the script with Thursday's own synthesiser (V12). Turns the estimated-timing
    #: path into the measured one: each scene becomes exactly as long as the line spoken
    #: over it, so every cue lands on the frame the voice starts.
    narrate: bool = False
    #: Which voice to speak in. Empty means the narrator's configured default.
    voice: str = ""
    music: str = ""
    music_gain_db: float = -18.0
    subtitles: bool = True
    thumbnail: bool = True

    @property
    def target(self) -> ExportPreset:
        return preset(self.aspect)


@dataclass
class PromoBuild:
    """The plan, the subtitle track, and an honest account of what is still missing."""

    plan: EditPlan = field(default_factory=lambda: EditPlan(name="promo"))
    subtitles: SubtitleTrack = field(default_factory=SubtitleTrack)
    subtitle_path: str = ""
    missing: list[str] = field(default_factory=list)
    #: Total length of the assembled video, in seconds, before it is rendered.
    seconds: float = 0.0
    #: What Thursday spoke, when it spoke it itself. `None` when narration was supplied or
    #: there is none — the distinction matters for reporting, since a synthesised voice is
    #: something the owner should be told about rather than left to notice.
    narration: Any = None

    @property
    def ready(self) -> bool:
        return not self.missing and bool(self.plan.steps)

    def describe(self) -> str:
        if not self.ready:
            return "cannot be assembled yet — missing: " + "; ".join(self.missing)
        timing = "synced to the narration" if self.subtitles.measured else "estimated timings"
        return (
            f"{len(self.plan.steps)} steps, {self.seconds:.1f}s, "
            f"{len(self.subtitles.cues)} subtitle cues ({timing})"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "missing": self.missing,
            "seconds": round(self.seconds, 2),
            "narration": self.narration.to_dict() if self.narration is not None else None,
            "subtitle_path": self.subtitle_path,
            "subtitle_timing": self.subtitles.timing,
            "summary": self.describe(),
            "plan": self.plan.to_dict(),
        }


async def compose(request: PromoRequest, editor: Any = None, narrator: Any = None) -> PromoBuild:
    """Turn a request into a renderable plan, or say what is stopping it.

    `editor` is used only to read the length of narration files that were supplied. Without
    one, per-scene narration cannot be measured and the request is treated as having none —
    a downgrade in subtitle accuracy, and never a silent one.

    `narrator` is what makes `request.narrate` possible: it speaks the script, and the
    durations it measures are used directly, so no editor is needed for that path at all.
    """
    build = PromoBuild()
    workdir = Path(request.workdir)
    scenes = [s for s in request.scenes if (s.text.strip() or s.image)]
    #: Lengths measured by the narrator, when Thursday spoke the script itself.
    spoken: list[float] = []

    if not scenes:
        build.missing.append("a script — there are no scenes to show")
        return build

    if request.narrate:
        problem = _narration_blocker(request, scenes, narrator)
        if problem:
            build.missing.append(problem)
            return build
        # Speak first, then carry on as though the audio had been supplied: everything
        # downstream already knows how to time a scene against its own narration file.
        narration = await narrator.narrate(
            [scene.text for scene in scenes],
            workdir / "narration",
            voice=request.voice,
        )
        build.narration = narration
        scenes = [
            Scene(text=scene.text, image=scene.image, seconds=scene.seconds, narration=line.path)
            for scene, line in zip(scenes, narration.lines, strict=True)
        ]
        # Kept, rather than re-derived below. The narrator measured these off the audio it
        # just wrote, so asking an editor to probe the same files would be slower and no
        # more true — and asking reading speed instead would silently discard the
        # measurement, which is what this did until a test compared the numbers.
        spoken = narration.durations

    per_scene = [s for s in scenes if s.narration]
    if per_scene and len(per_scene) != len(scenes):
        # Refused rather than filled in. A track where some cues are measured and the rest
        # are guessed is a guessed track, because everything after the first guess has moved.
        build.missing.append(
            f"narration for every scene or none — {len(per_scene)} of {len(scenes)} have it"
        )
        return build
    if per_scene and request.narration:
        build.missing.append(
            "either one narration file for the whole script or one per scene, not both"
        )
        return build

    without_image = [index for index, scene in enumerate(scenes, start=1) if not scene.image]
    if without_image:
        build.missing.append("a picture for scene " + ", ".join(str(i) for i in without_image))

    for path in await asyncio.to_thread(_absent, _named_inputs(scenes, request)):
        build.missing.append(f"the file {path} does not exist")

    if build.missing:
        return build

    durations = spoken or await _scene_lengths(scenes, editor, measured=bool(per_scene))
    build.seconds = sum(durations)

    steps: list[EditStep] = []
    clips: list[str] = []
    for index, (scene, seconds) in enumerate(zip(scenes, durations, strict=True), start=1):
        still = workdir / f"scene{index:02d}.mp4"
        steps.append(
            EditStep(
                id=f"scene{index:02d}",
                op="still",
                produces=str(still),
                args={
                    "image": scene.image,
                    "seconds": round(seconds, 3),
                    "preset": request.aspect,
                },
                label=f"scene {index}: {_snippet(scene.text) or Path(scene.image).name}",
            )
        )
        if scene.narration:
            voiced = workdir / f"scene{index:02d}-voiced.mp4"
            steps.append(
                EditStep(
                    id=f"voice{index:02d}",
                    op="dub",
                    produces=str(voiced),
                    args={"src": str(still), "narration": scene.narration},
                    label=f"narration over scene {index}",
                )
            )
            clips.append(str(voiced))
        else:
            clips.append(str(still))

    current = workdir / f"{request.name}-joined.mp4"
    steps.append(
        EditStep(
            id="join",
            op="concat",
            produces=str(current),
            args={"sources": clips},
            label=f"join {len(clips)} scenes",
        )
    )

    # One narration file for the whole script, and/or a backing track, laid over the join.
    if request.narration or request.music:
        dubbed = workdir / f"{request.name}-audio.mp4"
        args: dict[str, Any] = {"src": str(current)}
        if request.narration:
            args["narration"] = request.narration
        if request.music:
            args["music"] = request.music
            args["music_gain_db"] = request.music_gain_db
        steps.append(
            EditStep(id="audio", op="dub", produces=str(dubbed), args=args, label="add audio")
        )
        current = dubbed

    build.subtitles = _track(scenes, durations, measured=bool(per_scene))
    if request.subtitles and build.subtitles.cues:
        srt = workdir / f"{request.name}.srt"
        await asyncio.to_thread(_write, srt, to_srt(build.subtitles))
        build.subtitle_path = str(srt)
        burned = workdir / f"{request.name}-subtitled.mp4"
        steps.append(
            EditStep(
                id="subtitles",
                op="burn_subtitles",
                produces=str(burned),
                args={"src": str(current), "subtitles": str(srt)},
                label=f"burn in {len(build.subtitles.cues)} subtitles",
            )
        )
        current = burned

    deliverable = str(current)
    if request.thumbnail:
        steps.append(
            EditStep(
                id="thumbnail",
                op="thumbnail",
                produces=str(workdir / f"{request.name}-thumbnail.png"),
                # Not frame zero: the first frame of a fade or a title card is usually
                # black, and a black thumbnail is how a good video goes unwatched.
                args={"src": deliverable, "at": min(1.0, max(0.2, build.seconds / 10))},
                label="cover image",
            )
        )

    build.plan = EditPlan(name=request.name, steps=steps, deliverable=deliverable)
    build.plan.validate()
    log.info(
        "promo_composed",
        scenes=len(scenes),
        steps=len(steps),
        seconds=round(build.seconds, 2),
        timing=build.subtitles.timing,
    )
    return build


def expectations(request: PromoRequest, build: PromoBuild) -> dict[str, Any]:
    """What the finished file should hold, for the quality gate to check it against.

    Derived from the request rather than from the render, which is the only way the check
    means anything: asking the output what shape it is and then checking it is that shape
    proves nothing.
    """
    has_audio = bool(request.narration or request.music or any(s.narration for s in request.scenes))
    return {
        "preset": request.aspect,
        "audio": has_audio,
        "video": True,
        "subtitles": build.subtitle_path,
        # A tolerance, because concatenation lands on frame boundaries and a 30fps frame is
        # 33ms. Tight enough to catch a scene that did not make it in.
        "min_seconds": max(0.0, build.seconds - 1.0),
        "max_seconds": build.seconds + 1.5,
    }


def _narration_blocker(request: PromoRequest, scenes: list[Scene], narrator: Any) -> str:
    """Why this script cannot be spoken, or empty when it can.

    Checked before a single line is synthesised, because synthesising five of six lines and
    then discovering the sixth is blank leaves a folder of audio nobody asked for.
    """
    if narrator is None or not getattr(narrator, "available", False):
        return (
            "a speech synthesiser — Thursday was asked to narrate this and has no voice "
            "configured (set THURSDAY_TTS_BACKEND=espeak)"
        )
    if request.narration:
        return "either narration Thursday speaks or a narration file, not both"
    if any(scene.narration for scene in scenes):
        return "either narration Thursday speaks or per-scene audio files, not both"
    silent = [index for index, scene in enumerate(scenes, start=1) if not scene.text.strip()]
    if silent:
        # All or none, for the same reason a supplied track cannot be part measured: a scene
        # with nothing spoken over it would have to be timed by a different rule, and every
        # cue after it would move.
        return (
            "words for scene " + ", ".join(str(i) for i in silent) + " — Thursday cannot "
            "narrate a scene with no line, and timing some scenes by voice and the rest by "
            "reading speed would move every cue after the first silent one"
        )
    return ""


async def _scene_lengths(scenes: list[Scene], editor: Any, *, measured: bool) -> list[float]:
    lengths: list[float] = []
    for scene in scenes:
        if measured and editor is not None:
            probe = await editor.probe(scene.narration)
            if probe.seconds is None or probe.seconds <= 0:
                raise OperationFailed(
                    f"the length of {Path(scene.narration).name} could not be read, so the "
                    "scene it narrates cannot be timed"
                )
            lengths.append(probe.seconds)
            continue
        if scene.seconds is not None and scene.seconds > 0:
            lengths.append(float(scene.seconds))
        elif scene.text.strip():
            lengths.append(speaking_seconds(scene.text))
        else:
            lengths.append(DEFAULT_SCENE_SECONDS)
    return lengths


def _track(scenes: list[Scene], durations: list[float], *, measured: bool) -> SubtitleTrack:
    """Cues laid against the scenes they belong to.

    Always against the scene lengths — never re-derived from reading speed. A cue *is* its
    scene: it appears when the picture does and leaves when the picture does. Timing the
    two independently is how a caption ends up still on screen two scenes later, and how a
    track ends up running past the end of the video it belongs to.

    What the caller's `measured` flag decides is only the **label**: the cues are as good as
    the scene lengths they were laid against, so narration that was read off real audio
    gives a measured track and scene lengths guessed from reading speed give an estimated
    one, even though both take the same path through here.

    `gap=0` because a scene boundary is already a visual break; a gap would leave the screen
    blank at exactly the moment the next line starts being spoken.
    """
    lines = [scene.text for scene in scenes]
    if not any(line.strip() for line in lines):
        return SubtitleTrack()
    return from_lines(
        lines,
        durations=durations,
        gap=0.0,
        timing="measured" if measured else "estimated",
    )


def _named_inputs(scenes: list[Scene], request: PromoRequest) -> list[str]:
    paths = [s.image for s in scenes if s.image] + [s.narration for s in scenes if s.narration]
    if request.narration:
        paths.append(request.narration)
    if request.music:
        paths.append(request.music)
    return paths


def _absent(paths: list[str]) -> list[str]:
    """Which of these are not on disk. Checked before a render rather than during one: a
    missing picture found at step four has already been reported to the owner as progress."""
    return [path for path in paths if not Path(path).exists()]


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _snippet(text: str, limit: int = 40) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"
