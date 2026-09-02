"""Finding the skill someone means (§53, V9).

    "Thursday ทำรายงานคะแนนแบบที่เคยทำ"

Nothing in that sentence is the skill's name. It says *the kind of thing* — a grade report —
and *that there was a previous time*. The second half is the real signal: "แบบที่เคยทำ",
"แบบเดิม", "like last time" are the owner saying they are not describing a new job, they are
asking for a job that already exists. Without something that hears it, the sentence falls
through to whatever handles ordinary questions and comes back "I found nothing", which is
both true and useless — the skill is right there.

Matching is lexical, on character trigrams, for the same reason the memory layer's is: Thai
is written without spaces, so "คะแนน" inside "ทำรายงานคะแนนแบบที่เคยทำ" is a real mention
that any word-splitting approach misses entirely. The skill's name, description and tags are
all searched, because the owner names a skill by what it does far more often than by what it
is called.

Deliberately not embeddings. A skill run is an *action* — it does things to files, on
devices, under permissions — and the difference between the right skill and a
nearly-related one is not a difference of degree. Lexical matching fails visibly and
locally; a vector search fails plausibly and needs a model to be running to fail at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: Below this, the owner is not asking for a skill they already have. Set high on purpose:
#: running the wrong workflow is worse than asking which one, because a workflow has steps
#: and the wrong steps have already happened by the time anybody notices.
MATCH_FLOOR = 0.35

#: The gap a winner needs over the runner-up. Two skills that match a sentence equally well
#: mean the sentence does not identify one of them, however high both scores are.
DECISIVE_MARGIN = 0.1

#: Phrases that mean "the one from before". These do not identify *which* skill; they say
#: the owner believes one exists, which is what turns a request into a skill lookup.
RECALL_MARKERS: tuple[str, ...] = (
    "แบบที่เคยทำ",
    "แบบเคย",
    "แบบเดิม",
    "อย่างที่เคย",
    "เหมือนเดิม",
    "เหมือนที่เคยทำ",
    "ตามที่เคยทำ",
    "ที่เคยทำ",
    "like last time",
    "like before",
    "the usual",
    "same as last time",
    "as usual",
)


def _trigrams(text: str) -> set[str]:
    cleaned = "".join(ch for ch in text.lower() if not ch.isspace())
    return {cleaned[i : i + 3] for i in range(max(0, len(cleaned) - 2))}


def _overlap(needle: str, haystack: str) -> float:
    """How much of ``needle`` literally appears in ``haystack``, 0–1."""
    wanted = _trigrams(needle)
    if not wanted:
        return 0.0
    return len(wanted & _trigrams(haystack)) / len(wanted)


def mentions_a_previous_time(utterance: str) -> bool:
    """Whether the owner said this is something already done before."""
    lowered = utterance.lower()
    return any(marker.lower() in lowered for marker in RECALL_MARKERS)


@dataclass(frozen=True)
class SkillMatch:
    skill: Any
    score: float
    reason: str
    #: The others that came close, so an ambiguous match can name the alternatives rather
    #: than making the owner guess what Thursday was choosing between.
    runners_up: tuple[tuple[str, float], ...] = ()

    @property
    def confident(self) -> bool:
        return self.score >= MATCH_FLOOR

    def question(self) -> str:
        names = " / ".join([self.skill.name, *(n for n, _ in self.runners_up[:3])])
        return f"หมายถึงสกิลไหนครับ — {names}"


#: Words that carry no information about *which* skill is wanted. Stripped before matching
#: against a description, so "ทำ...แบบที่เคยทำ" is compared on "รายงานคะแนน" — the part that
#: actually names the work.
_FILLER = ("thursday", "ช่วย", "หน่อย", "ให้", "ครับ", "ค่ะ", "please", "can you", "ทำ")


def _core_of(utterance: str) -> str:
    """The part of what was said that identifies the work."""
    text = utterance.lower()
    for marker in RECALL_MARKERS:
        text = text.replace(marker.lower(), " ")
    for word in _FILLER:
        text = text.replace(word, " ")
    return " ".join(text.split())


def score_skill(utterance: str, skill: Any) -> float:
    """How well one skill matches what was said.

    The direction of each comparison is chosen per field, and getting it wrong makes the
    whole feature silently useless — which it was, first time round.

    * **Name and tags** are short, so the question is how much of the *name* appears in the
      sentence. Asking how much of a sentence appears in a two-word name scores everything
      near zero.
    * **The description** is prose of comparable length to the sentence, so the question
      reverses: how much of what the owner said appears in the description. This is the
      field that carries cross-language matches — a skill called "School Grade Report" is
      asked for in Thai, and its Thai description is the only thing the two have in common.
      Weighted below an outright name match, because a long description overlaps with a
      lot of things.
    """
    text = utterance.lower()
    best = _overlap(skill.name, text)
    for tag in getattr(skill, "tags", []):
        best = max(best, _overlap(tag, text))
    if description := getattr(skill, "description", ""):
        best = max(best, _overlap(_core_of(utterance), description) * 0.8)
    return round(best, 4)


def find_skill(utterance: str, skills: list[Any]) -> SkillMatch | None:
    """The skill the owner meant, or None.

    Returns an unconfident match rather than nothing when two skills tie, so the caller can
    ask which one. Silence and ambiguity are different answers and the caller needs both.
    """
    if not skills:
        return None
    scored = sorted(
        ((skill, score_skill(utterance, skill)) for skill in skills),
        key=lambda pair: pair[1],
        reverse=True,
    )
    best, score = scored[0]
    runners_up = tuple((s.name, v) for s, v in scored[1:4])

    if score < MATCH_FLOOR:
        return None
    if len(scored) > 1 and score - scored[1][1] < DECISIVE_MARGIN:
        return SkillMatch(
            skill=best,
            # Below the floor deliberately: the caller must ask, not pick.
            score=MATCH_FLOOR - 0.01,
            reason=f"{scored[1][0].name!r} matches this just as well",
            runners_up=runners_up,
        )
    return SkillMatch(
        skill=best,
        score=score,
        reason=f"matches the skill {best.name!r}",
        runners_up=runners_up,
    )
