"""School work: the artefacts a teacher, a librarian and an event organiser actually produce.

Three domains, one rule, taken from `DataAgent` and `DocumentAgent` before them: **the
numbers are computed here and the model only writes sentences over figures already fixed.**
A rubric whose weights were guessed, a blueprint whose items were estimated or a run sheet
whose minutes were approximated is worse than no document at all — it is wrong in a way that
survives every check except the one nobody ran, and these are documents other people are
held to.

So each module refuses rather than corrects. Weights that miss 100% are not rescaled; a
lesson that overruns its period is not trimmed; a blueprint that contradicts its own totals
is not reconciled. What to drop is professional judgement, and the person holding it is the
one who has to defend the result.
"""

from thursday_school.blueprint import Blueprint, BlueprintError
from thursday_school.blueprint import Row as BlueprintRow
from thursday_school.blueprint import build as build_blueprint
from thursday_school.lesson import Activity, Lesson, LessonError
from thursday_school.lesson import build as build_lesson
from thursday_school.rubric import Criterion, Rubric, RubricError
from thursday_school.rubric import build as build_rubric

__all__ = [
    "Activity",
    "Blueprint",
    "BlueprintError",
    "BlueprintRow",
    "Criterion",
    "Lesson",
    "LessonError",
    "Rubric",
    "RubricError",
    "build_blueprint",
    "build_lesson",
    "build_rubric",
]
