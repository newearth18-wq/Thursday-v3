"""The voice state machine (V4).

    IDLE → LISTENING → CAPTURING → TRANSCRIBING → THINKING → SPEAKING → IDLE

An explicit machine rather than a handful of booleans, for the same reason tasks have one:
the interesting bugs in a voice loop are all state bugs. Two overlapping utterances, a
wake word that fires while Thursday is already speaking, a transcription that lands after
the owner has moved on — each is a transition that either exists or does not, and a boolean
`awake` flag cannot express which.

The transition table below is the whole specification. Anything not in it raises.
"""

from __future__ import annotations

from collections import deque
from enum import StrEnum

from thursday_shared.errors import ThursdayError


class VoiceState(StrEnum):
    #: Not listening for anything but the wake word.
    IDLE = "IDLE"
    #: Woken, waiting for speech to begin.
    LISTENING = "LISTENING"
    #: Speech has begun; collecting frames until the VAD says the utterance ended.
    CAPTURING = "CAPTURING"
    #: Audio captured, being turned into words.
    TRANSCRIBING = "TRANSCRIBING"
    #: Thursday Core has the words and is deciding what to do.
    THINKING = "THINKING"
    #: Synthesising and playing a reply. The only state barge-in can interrupt.
    SPEAKING = "SPEAKING"


VOICE_TRANSITIONS: dict[VoiceState, frozenset[VoiceState]] = {
    VoiceState.IDLE: frozenset(
        {
            # The wake word is the only way from IDLE to *listening*, which is the privacy
            # guarantee (T9): nothing is captured until the name is heard.
            VoiceState.LISTENING,
            # Speaking, though, can start from rest — a proactive notification, or a
            # spoken answer to something asked in text. That path opens the speaker, never
            # the microphone, so it takes nothing away from the guarantee above.
            VoiceState.SPEAKING,
        }
    ),
    VoiceState.LISTENING: frozenset(
        {
            VoiceState.CAPTURING,
            # Woken, then silence. Going back to IDLE rather than waiting forever is what
            # stops an accidental wake from leaving the microphone open.
            VoiceState.IDLE,
        }
    ),
    VoiceState.CAPTURING: frozenset({VoiceState.TRANSCRIBING, VoiceState.IDLE}),
    VoiceState.TRANSCRIBING: frozenset(
        {
            VoiceState.THINKING,
            # Nothing intelligible. Say nothing rather than guess.
            VoiceState.IDLE,
        }
    ),
    VoiceState.THINKING: frozenset(
        {
            VoiceState.SPEAKING,
            # Barge-in during THINKING: the owner changed their mind before the answer
            # arrived, so the answer is dropped and the new utterance wins.
            VoiceState.CAPTURING,
            VoiceState.IDLE,
        }
    ),
    VoiceState.SPEAKING: frozenset(
        {
            VoiceState.IDLE,
            # Barge-in proper: the owner talks over the reply.
            VoiceState.CAPTURING,
            VoiceState.LISTENING,
        }
    ),
}

#: States in which the microphone is live. Anything not in here must not be recording —
#: the UI's microphone indicator is driven from this, and it has to be true.
LISTENING_STATES: frozenset[VoiceState] = frozenset({VoiceState.LISTENING, VoiceState.CAPTURING})


class VoiceStateError(ThursdayError):
    """An illegal transition. Raised rather than logged: a voice loop that quietly ends up
    in a state nobody designed is a voice loop that records when it should not."""

    code = "voice_state_error"


#: How many transitions to keep. Enough to explain the last few turns, which is all
#: `history` has ever been used for.
HISTORY_LIMIT = 200


class VoiceStateMachine:
    """Holds the current state and refuses to leave it illegally."""

    def __init__(self, *, on_change: object | None = None) -> None:
        self._state = VoiceState.IDLE
        # Bounded. Every wake, every utterance and every barge-in appends here, and this
        # object lives as long as Thursday does — an unbounded list would grow for the life
        # of the process to serve a debugging aid nobody reads beyond the last few entries.
        self._history: deque[tuple[VoiceState, VoiceState]] = deque(maxlen=HISTORY_LIMIT)
        self._on_change = on_change

    @property
    def state(self) -> VoiceState:
        return self._state

    @property
    def listening(self) -> bool:
        """True exactly when the microphone is capturing. Drives the recording indicator."""
        return self._state in LISTENING_STATES

    @property
    def history(self) -> list[tuple[VoiceState, VoiceState]]:
        return list(self._history)

    def can(self, target: VoiceState) -> bool:
        return target in VOICE_TRANSITIONS[self._state]

    def to(self, target: VoiceState) -> VoiceState:
        if target is self._state:
            return self._state
        if not self.can(target):
            raise VoiceStateError(
                f"voice cannot go from {self._state} to {target}",
                details={"from": str(self._state), "to": str(target)},
            )
        previous, self._state = self._state, target
        self._history.append((previous, target))
        if callable(self._on_change):
            self._on_change(previous, target)
        return self._state

    def reset(self) -> None:
        """Return to IDLE from anywhere.

        The one unconditional transition, because it is the emergency path: "Thursday
        หยุด", a torn-down session, a failure nobody anticipated. A stop that could itself
        be refused by the state machine would be no stop at all (§69).
        """
        if self._state is not VoiceState.IDLE:
            previous, self._state = self._state, VoiceState.IDLE
            self._history.append((previous, VoiceState.IDLE))
            if callable(self._on_change):
                self._on_change(previous, VoiceState.IDLE)
