"""Media editing (§15, V11 — Phase 5).

The part of Thursday that changes a file rather than describing one. Everything here is
local, deterministic and reversible-by-construction: an edit writes a *new* file and never
the one it read.

The port is `MediaEditor`. `FFmpegEditor` is the real adapter; `UnavailableEditor` is what
the container builds when ffmpeg is not on the machine, and it refuses with a sentence
naming the remedy rather than failing somewhere deeper (ADR 0060).
"""

from thursday_media.creative import PromoBuild, PromoRequest, Scene, compose, expectations
from thursday_media.plan import EditPlan, EditStep, PlanReport, StepOutcome
from thursday_media.ports import (
    PRESETS,
    ExportPreset,
    MediaEditor,
    MediaProbe,
    MediaUnavailable,
    OperationFailed,
    StreamInfo,
    preset,
)
from thursday_media.quality import QualityReport, check_output
from thursday_media.subtitles import Cue, parse_srt, to_srt, to_vtt

__all__ = [
    "PRESETS",
    "Cue",
    "EditPlan",
    "EditStep",
    "ExportPreset",
    "MediaEditor",
    "MediaProbe",
    "MediaUnavailable",
    "OperationFailed",
    "PlanReport",
    "PromoBuild",
    "PromoRequest",
    "QualityReport",
    "Scene",
    "StepOutcome",
    "StreamInfo",
    "check_output",
    "compose",
    "expectations",
    "parse_srt",
    "preset",
    "to_srt",
    "to_vtt",
]
