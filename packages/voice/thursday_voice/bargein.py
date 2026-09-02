"""Barge-in (V4, §44).

An assistant you cannot interrupt is one you end up talking over and then repeating
yourself to. Worse, it is one that keeps reading out a wrong answer while you are trying to
correct it.

So interruption is a first-class operation, and it does four things in order:

1. stop synthesis *now* — before the next chunk plays, not after the sentence finishes;
2. keep the conversation context, because "no, the other one" only means something in the
   light of what was just said;
3. reopen the microphone;
4. hand the new utterance to the core as an ordinary turn.

The previous implementation had an ``interrupt()`` that could never fire: the field holding
the synthesis task was assigned ``None`` and never anything else, so the cancel was
unreachable and the method always reported "nothing to stop". This one owns the task it is
asked to cancel, which is the only way the guarantee is real.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from thursday_core.logging import get_logger

log = get_logger(__name__)


@dataclass
class InterruptedUtterance:
    """What Thursday was saying when it was cut off.

    Kept rather than discarded: the owner may follow up with "what was that?", and an
    assistant that has forgotten its own half-finished sentence is no use. It is also what
    makes the interruption visible in the transcript instead of a gap.
    """

    text: str
    spoken_chars: int = 0
    mode: str = "NORMAL"

    @property
    def partial(self) -> str:
        return self.text[: self.spoken_chars]

    @property
    def unspoken(self) -> str:
        return self.text[self.spoken_chars :]

    @property
    def completed(self) -> bool:
        return self.spoken_chars >= len(self.text)


@dataclass
class BargeInController:
    """Owns the in-flight utterance, and can end it."""

    tts: Any
    #: Set while Thursday is speaking. The whole mechanism turns on this being real.
    _task: asyncio.Task[None] | None = field(default=None, repr=False)
    _current: InterruptedUtterance | None = field(default=None, repr=False)
    #: Every interruption, for the transcript and for tests.
    interruptions: list[InterruptedUtterance] = field(default_factory=list)

    @property
    def speaking(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def current(self) -> InterruptedUtterance | None:
        return self._current

    def begin(self, task: asyncio.Task[None], utterance: InterruptedUtterance) -> None:
        """Register the utterance now being spoken."""
        self._task = task
        self._current = utterance

    def report_progress(self, chars: int) -> None:
        """How much has actually reached the speaker.

        Tracked so an interruption can say what the owner *heard*, not what was queued.
        Those differ by however much audio was still buffered, and the difference is
        exactly what makes "as I was saying" either right or wrong.
        """
        if self._current is not None:
            self._current.spoken_chars = max(self._current.spoken_chars, chars)

    async def interrupt(self, *, reason: str = "owner spoke") -> InterruptedUtterance | None:
        """Stop speaking. Safe to call when nothing is being said.

        Returns what was cut off, or None if Thursday was already silent.
        """
        if not self.speaking:
            # Still ask the provider to stop: a previous utterance may have audio queued in
            # the device buffer even though our task has finished.
            await self._quiet_stop()
            self._task = None
            return None

        assert self._task is not None
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # the utterance failed on its own; stopping it is still fine
            log.debug("speech_task_ended_with_error", error=str(exc))
        finally:
            self._task = None

        await self._quiet_stop()

        cut = self._current
        self._current = None
        if cut is not None:
            self.interruptions.append(cut)
            log.info(
                "barge_in",
                reason=reason,
                spoken=cut.spoken_chars,
                remaining=len(cut.unspoken),
            )
        return cut

    async def finished(self) -> None:
        """The utterance completed on its own."""
        if self._current is not None:
            self._current.spoken_chars = len(self._current.text)
        self._task = None
        self._current = None

    async def _quiet_stop(self) -> None:
        """Tell the provider to stop, and never let that failure become the caller's.

        A TTS backend that errors while being silenced must not stop the owner from being
        heard — the thing they interrupted us to say is more important than tidy shutdown.
        """
        stop = getattr(self.tts, "stop", None)
        if stop is None:
            return
        try:
            await stop()
        except Exception as exc:
            log.warning("tts_stop_failed", error=str(exc))
