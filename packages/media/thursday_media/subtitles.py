"""Subtitles: build them, write them, read them back (§15, V11).

No ffmpeg here and no model either. Cue timing is arithmetic, and keeping it arithmetic is
what makes it checkable — `tests/unit/test_subtitles_v11.py` asserts exact timestamps rather
than "roughly right", which is the only way a drift of forty milliseconds per cue gets
noticed before it has accumulated into a second by the end of a narration.

The one thing this module is careful about is the difference between timings it *measured*
and timings it *guessed*. A track built from real audio durations is synchronised. A track
built from a reading-rate estimate is a plausible guess that will drift against real speech,
and `SubtitleTrack.timing` says which one you are holding. The quality checker refuses to
call an estimated track "synced", and the agent says "ประมาณ" out loud when it reports one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: Characters per second of speech for scripts written without word spacing (Thai, Lao,
#: Khmer, Japanese, Chinese). Measured against Thai TTS at a normal narration pace; slower
#: than conversational speech because narration is read, not spoken.
THAI_CHARS_PER_SECOND = 10.0

#: Words per minute for space-separated scripts. 150 is the low end of the range usually
#: quoted for clear narration, chosen deliberately: a subtitle that lingers is readable and
#: one that vanishes early is not.
LATIN_WORDS_PER_MINUTE = 150.0

#: No cue shorter than this, however few characters it holds. Below about four fifths of a
#: second a line is gone before the eye has finished landing on it.
MIN_CUE_SECONDS = 0.8

#: Nor longer than this: a line still on screen after six seconds reads as a frozen video.
MAX_CUE_SECONDS = 6.0

#: Two lines is the broadcast convention and the reason `wrap` exists at all. Three fit, and
#: they cover the picture.
MAX_LINES = 2

#: Per line. Thai renders wider per character than Latin at the same point size, so this is
#: the conservative number rather than the 42 that subtitling guides usually give.
MAX_CHARS_PER_LINE = 38

_THAI_OR_CJK = re.compile(r"[฀-๿ក-៿぀-ヿ一-鿿]")
_SRT_BLOCK = re.compile(
    r"(?P<index>\d+)\s*\n"
    r"(?P<start>\d{2}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*(?P<end>\d{2}:\d{2}:\d{2}[,.]\d{3})"
    r"[^\n]*\n(?P<text>(?:.+\n?)*?)(?=\n\s*\n|\n*\Z)",
    re.MULTILINE,
)


@dataclass(frozen=True)
class Cue:
    """One subtitle. Times are seconds from the start of the video, never frames.

    Frames would tie a cue to a frame rate, and the same track is used against a 30fps
    export and a 25fps one.
    """

    start: float
    end: float
    text: str

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError(f"cue ends before it starts: {self.start} → {self.end}")

    @property
    def seconds(self) -> float:
        return self.end - self.start

    @property
    def lines(self) -> list[str]:
        return self.text.split("\n")


@dataclass
class SubtitleTrack:
    """Cues plus the one fact about them that matters downstream: where the times came from.

    `timing="measured"` means every cue was placed against a duration somebody actually
    read off an audio file. `timing="estimated"` means the times came from
    `speaking_seconds` and are a reading-rate guess. Nothing in this package ever upgrades
    an estimate to a measurement, and `quality.check_output` will not certify a burn-in of
    an estimated track as synchronised.
    """

    cues: list[Cue] = field(default_factory=list)
    timing: str = "estimated"  # estimated | measured
    language: str = "th"

    @property
    def seconds(self) -> float:
        return max((c.end for c in self.cues), default=0.0)

    @property
    def measured(self) -> bool:
        return self.timing == "measured"

    def shifted(self, by: float) -> SubtitleTrack:
        """The whole track moved along the timeline. Used when narration is preceded by a
        title card, which is the common case rather than an exotic one."""
        return SubtitleTrack(
            cues=[Cue(c.start + by, c.end + by, c.text) for c in self.cues],
            timing=self.timing,
            language=self.language,
        )

    def describe(self) -> str:
        kind = "synced to the narration" if self.measured else "estimated from reading speed"
        return f"{len(self.cues)} cues over {self.seconds:.1f}s, {kind}"


def is_unspaced_script(text: str) -> bool:
    """True for Thai, Khmer, Japanese and Chinese — scripts where splitting on spaces does
    not find words, so characters are the only unit available to count."""
    return bool(_THAI_OR_CJK.search(text))


def speaking_seconds(text: str) -> float:
    """How long this is likely to take to say. An estimate, and labelled as one everywhere
    it is used."""
    stripped = text.strip()
    if not stripped:
        return 0.0
    if is_unspaced_script(stripped):
        # Spaces and punctuation are not spoken, so they should not be counted.
        speakable = sum(1 for ch in stripped if not ch.isspace())
        seconds = speakable / THAI_CHARS_PER_SECOND
    else:
        words = len(stripped.split())
        seconds = words / (LATIN_WORDS_PER_MINUTE / 60.0)
    return max(MIN_CUE_SECONDS, seconds)


def wrap(text: str, *, width: int = MAX_CHARS_PER_LINE, max_lines: int = MAX_LINES) -> str:
    """Break a line so it fits on screen.

    Thai is wrapped on character count because there is nothing else to wrap on — no spaces
    between words — which produces a break mid-word roughly as often as not. That is worse
    than a proper dictionary-based line breaker and better than a line that runs off the
    side of the frame, and it is the honest trade: a real Thai line breaker needs a
    dictionary this project does not ship.
    """
    text = " ".join(text.split())
    if len(text) <= width:
        return text

    lines: list[str] = []
    if is_unspaced_script(text):
        remaining = text
        while remaining and len(lines) < max_lines:
            lines.append(remaining[:width])
            remaining = remaining[width:]
    else:
        current = ""
        for word in text.split():
            candidate = f"{current} {word}".strip()
            if len(candidate) <= width:
                current = candidate
                continue
            if current:
                lines.append(current)
            current = word
            if len(lines) >= max_lines:
                break
        if current and len(lines) < max_lines:
            lines.append(current)
    return "\n".join(lines[:max_lines])


def from_lines(
    lines: list[str],
    *,
    durations: list[float] | None = None,
    start: float = 0.0,
    gap: float = 0.08,
    language: str = "th",
    timing: str | None = None,
) -> SubtitleTrack:
    """Turn narration lines into a track.

    Pass `durations` — one per line — and each cue occupies exactly its slot on the
    timeline. Omit them and every line is timed by `speaking_seconds`.

    `timing` labels where those numbers came from, and it is separate from whether they were
    passed in because those are different questions. A caller can supply durations that are
    themselves estimates — scene lengths guessed from reading speed, say — and a track built
    from them is `estimated` however it was constructed. Supplied durations default to
    `measured`; pass `timing="estimated"` when they are not.

    A blank line produces no cue and **still advances the clock** when it has a duration of
    its own. It is a slot on the timeline that happens to have nothing said over it — a
    title card, a logo sting — and swallowing its time would pull every cue after it early.
    """
    if durations is not None and len(durations) != len(lines):
        raise ValueError(
            f"{len(lines)} lines but {len(durations)} durations — "
            "a track cannot be part measured and part guessed"
        )

    cues: list[Cue] = []
    clock = start
    for index, raw in enumerate(lines):
        text = " ".join(raw.split())
        if not text:
            if durations is not None:
                clock += durations[index] + gap
            continue
        seconds = durations[index] if durations is not None else speaking_seconds(text)
        # The cap applies to guesses; a duration that was handed in is how long the slot
        # *is*, and clipping it would put the cue out of step with the picture.
        if durations is None:
            seconds = min(MAX_CUE_SECONDS, max(MIN_CUE_SECONDS, seconds))
        cues.append(Cue(round(clock, 3), round(clock + seconds, 3), wrap(text)))
        clock += seconds + gap

    resolved = timing or ("measured" if durations is not None else "estimated")
    if resolved not in ("measured", "estimated"):
        raise ValueError(f"timing is 'measured' or 'estimated', not {resolved!r}")
    return SubtitleTrack(cues=cues, timing=resolved, language=language)


def _stamp(seconds: float, *, millis_separator: str) -> str:
    if seconds < 0:
        seconds = 0.0
    # Round to milliseconds first. Truncating after formatting turns 1.9999 into 00:00:01,999
    # and then the next cue starts before this one ends.
    total_ms = round(seconds * 1000)
    hours, rest = divmod(total_ms, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, millis = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{millis_separator}{millis:03d}"


def to_srt(track: SubtitleTrack | list[Cue]) -> str:
    """SubRip. The format ffmpeg's `subtitles` filter reads and every player understands."""
    cues = track.cues if isinstance(track, SubtitleTrack) else list(track)
    blocks = []
    for index, cue in enumerate(cues, start=1):
        start = _stamp(cue.start, millis_separator=",")
        end = _stamp(cue.end, millis_separator=",")
        blocks.append(f"{index}\n{start} --> {end}\n{cue.text}\n")
    return "\n".join(blocks)


def to_vtt(track: SubtitleTrack | list[Cue]) -> str:
    """WebVTT, for anything that will be played in a browser."""
    cues = track.cues if isinstance(track, SubtitleTrack) else list(track)
    blocks = ["WEBVTT\n"]
    for cue in cues:
        start = _stamp(cue.start, millis_separator=".")
        end = _stamp(cue.end, millis_separator=".")
        blocks.append(f"{start} --> {end}\n{cue.text}\n")
    return "\n".join(blocks)


def _seconds(stamp: str) -> float:
    hours, minutes, rest = stamp.split(":")
    secs, millis = re.split(r"[,.]", rest)
    return int(hours) * 3600 + int(minutes) * 60 + int(secs) + int(millis) / 1000


def parse_srt(text: str) -> SubtitleTrack:
    """Read a track back.

    Round-tripping matters because burning subtitles is destructive to the text — once they
    are pixels in a frame there is no getting them back — so a caller that wants to adjust
    an existing track has to be able to read the sidecar file. Timings that came off a file
    are `measured`: somebody wrote them down deliberately.
    """
    cues = [
        Cue(
            _seconds(match.group("start")),
            _seconds(match.group("end")),
            match.group("text").strip("\n").strip(),
        )
        for match in _SRT_BLOCK.finditer(text.replace("\r\n", "\n").strip() + "\n\n")
    ]
    return SubtitleTrack(cues=cues, timing="measured")
