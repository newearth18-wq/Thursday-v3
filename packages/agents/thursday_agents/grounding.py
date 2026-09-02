"""Is this text actually about the numbers it claims to report? (§18, V9)

Both the data and document agents end by asking a model for a sentence over figures that
have already been computed. Both then face the same question, and it is not "did the model
answer" — it is "did the model answer *this*".

The case that motivated this is not hypothetical. Running offline, the model returned its
standard "I cannot answer analytical questions right now" message. That is a non-empty
string, so it passed the report step's `output.document is not empty` criterion, and the
owner received a document that read like a report, was shaped like a report, and contained
none of the figures that had just been correctly computed for it. The analysis was done and
then thrown away, and every check said PASS.

So the check is not emptiness. It is whether any computed figure survived into the prose.
"""

from __future__ import annotations

from typing import Any


def grounded(text: str, figures: Any) -> bool:
    """Whether ``text`` contains at least one of the figures it is reporting on.

    A substring test, deliberately crude. It cannot tell a good report from a bad one and
    does not try — it answers the one question with a cheap exact answer: did any of the
    computed numbers reach the page. With no figures to carry, any non-empty text is
    trivially grounded, because there is nothing it could have dropped.
    """
    numbers = numbers_in(figures)
    if not numbers:
        return bool(text.strip())
    # Thousands separators would otherwise hide "1,250" from a search for "1250".
    haystack = text.replace(",", "")
    return any(str(n) in haystack or f"{n:g}" in haystack for n in numbers)


def numbers_in(value: Any) -> list[float]:
    """Every number inside a nested structure, flattened.

    Booleans are excluded on purpose: `True` is an `int` in Python, and a report containing
    the digit 1 would otherwise count as grounded in a flag nobody wrote down.
    """
    if isinstance(value, bool):
        return []
    if isinstance(value, int | float):
        return [value]
    if isinstance(value, list | tuple):
        return [n for item in value for n in numbers_in(item)]
    if isinstance(value, dict):
        return [n for item in value.values() for n in numbers_in(item)]
    return []
