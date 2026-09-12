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
from thursday_school.circulation import CirculationError, Loan
from thursday_school.circulation import build as build_loans
from thursday_school.circulation import summarise as summarise_loans
from thursday_school.collection import CollectionError, Item
from thursday_school.collection import analyse as analyse_collection
from thursday_school.collection import build as build_items
from thursday_school.collection import gaps as collection_gaps
from thursday_school.lesson import Activity, Lesson, LessonError
from thursday_school.lesson import build as build_lesson
from thursday_school.rubric import Criterion, Rubric, RubricError
from thursday_school.rubric import build as build_rubric
from thursday_school.runsheet import Item as RunSheetItem
from thursday_school.runsheet import RunSheet, RunSheetError
from thursday_school.runsheet import build as build_runsheet

__all__ = [
    "Activity",
    "Blueprint",
    "BlueprintError",
    "BlueprintRow",
    "CirculationError",
    "CollectionError",
    "Criterion",
    "Item",
    "Lesson",
    "LessonError",
    "Loan",
    "Rubric",
    "RubricError",
    "RunSheet",
    "RunSheetError",
    "RunSheetItem",
    "analyse_collection",
    "build_blueprint",
    "build_items",
    "build_lesson",
    "build_loans",
    "build_rubric",
    "build_runsheet",
    "collection_gaps",
    "summarise_loans",
]
