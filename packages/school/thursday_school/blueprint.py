"""Exam blueprints: ตารางวิเคราะห์ข้อสอบ that actually balances (§15, V13).

A blueprint is the table a teacher fills in before writing a paper: topics down the side,
cognitive levels across the top, item counts in the cells. It is also the document an
inspector asks for, which means its totals are checked by somebody other than its author.

Everything here is counted, never estimated. The blueprint's own claims — how many items,
how many marks, what share each topic carries — are recomputed from the cells and compared
against what the teacher declared. A mismatch is reported with both numbers, because "the
blueprint is wrong" is useless and "you said 40 items and the cells hold 38" is actionable.

The weighting question this cannot answer: whether *this* distribution is the right one for
*this* class is professional judgement, and there is no arithmetic for it. What it can do is
show the shares so the judgement is made with the numbers in view — and say when a topic
taught for six periods carries two percent of the paper.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: Bloom's revised taxonomy in the wording Thai schools use, simple to complex. Ordered,
#: because "how much of this paper is above recall" is the question blueprints exist for.
LEVELS: tuple[str, ...] = ("จำ", "เข้าใจ", "ประยุกต์ใช้", "วิเคราะห์", "ประเมินค่า", "สร้างสรรค์")

#: At and above this index, an item is asking for more than recall and comprehension.
HIGHER_ORDER_FROM = 2

TOLERANCE = 0.01


class BlueprintError(ValueError):
    """A blueprint whose cells do not match its declared totals."""


@dataclass(frozen=True)
class Row:
    """One topic's line across the cognitive levels."""

    topic: str
    #: level name → number of items. Absent levels are zero.
    items: dict[str, int] = field(default_factory=dict)
    #: Marks per item for this topic. One number per row keeps the arithmetic legible;
    #: a paper that needs per-cell marks gets one row per item type.
    marks_each: float = 1.0
    #: Teaching periods spent on the topic, for the coverage comparison. Zero means "not
    #: stated" rather than "not taught" — the comparison is skipped rather than guessed.
    periods: int = 0

    @property
    def count(self) -> int:
        return sum(self.items.values())

    @property
    def marks(self) -> float:
        return round(self.count * self.marks_each, 2)

    def at(self, level: str) -> int:
        return int(self.items.get(level, 0))


@dataclass
class Blueprint:
    """A whole paper, cell by cell. Build through `build` so it cannot exist unbalanced."""

    title: str
    rows: list[Row] = field(default_factory=list)
    levels: tuple[str, ...] = LEVELS

    @property
    def total_items(self) -> int:
        return sum(r.count for r in self.rows)

    @property
    def total_marks(self) -> float:
        return round(sum(r.marks for r in self.rows), 2)

    def by_level(self) -> dict[str, int]:
        return {level: sum(r.at(level) for r in self.rows) for level in self.levels}

    def higher_order_share(self) -> float:
        """Share of *marks* above recall and comprehension.

        Marks rather than items on purpose: five one-mark recall questions and one
        ten-mark analysis are not a paper that is five-sixths recall.
        """
        total = self.total_marks
        if not total:
            return 0.0
        higher = sum(
            r.at(level) * r.marks_each
            for r in self.rows
            for level in self.levels[HIGHER_ORDER_FROM:]
        )
        return round(higher / total * 100, 2)

    def shares(self) -> list[dict[str, Any]]:
        """Each topic's share of the paper, and of the teaching time when stated."""
        total_marks = self.total_marks
        total_periods = sum(r.periods for r in self.rows)
        out: list[dict[str, Any]] = []
        for row in self.rows:
            share = round(row.marks / total_marks * 100, 2) if total_marks else 0.0
            taught = round(row.periods / total_periods * 100, 2) if total_periods else None
            out.append(
                {
                    "topic": row.topic,
                    "items": row.count,
                    "marks": row.marks,
                    "share": share,
                    "periods": row.periods,
                    "taught_share": taught,
                    # Not a verdict. The gap is stated and the teacher decides; a rule that
                    # called a 12-point gap "wrong" would be inventing a standard.
                    "gap": None if taught is None else round(share - taught, 2),
                }
            )
        return out

    def to_dict(self) -> dict[str, Any]:
        shares = self.shares()
        return {
            "title": self.title,
            "levels": list(self.levels),
            "rows": [
                {
                    "topic": r.topic,
                    "items": {level: r.at(level) for level in self.levels},
                    "count": r.count,
                    "marks_each": r.marks_each,
                    "marks": r.marks,
                }
                for r in self.rows
            ],
            "count": len(self.rows),
            "total_items": self.total_items,
            "total_marks": self.total_marks,
            "by_level": self.by_level(),
            "higher_order_percent": self.higher_order_share(),
            "shares": shares,
            # The Supervisor's own arithmetic check reads this key (§18).
            "percentages": [s["share"] for s in shares],
        }


def build(
    title: str,
    rows: list[Row] | list[dict[str, Any]],
    *,
    levels: tuple[str, ...] = LEVELS,
    expect_items: int | None = None,
    expect_marks: float | None = None,
) -> Blueprint:
    """Build a blueprint, checking the cells against what the teacher said they add to."""
    if not rows:
        raise BlueprintError("ตารางวิเคราะห์ต้องมีเนื้อหาอย่างน้อยหนึ่งหน่วย")

    parsed = [r if isinstance(r, Row) else _row(r, levels) for r in rows]

    names = [r.topic for r in parsed]
    duplicates = {n for n in names if names.count(n) > 1}
    if duplicates:
        raise BlueprintError("หน่วยการเรียนซ้ำกัน: " + ", ".join(sorted(duplicates)))

    empty = [r.topic for r in parsed if r.count == 0]
    if empty:
        # A topic with no items is either an omission or a decision. Either way the teacher
        # should see it named rather than find a paper that skips a unit they taught.
        raise BlueprintError("หน่วยที่ยังไม่มีข้อสอบ: " + ", ".join(empty))

    blueprint = Blueprint(title=title, rows=parsed, levels=tuple(levels))

    if expect_items is not None and blueprint.total_items != expect_items:
        raise BlueprintError(f"ระบุว่ามี {expect_items} ข้อ แต่ในตารางนับได้ {blueprint.total_items} ข้อ")
    if expect_marks is not None and abs(blueprint.total_marks - expect_marks) > TOLERANCE:
        raise BlueprintError(
            f"ระบุคะแนนเต็ม {expect_marks:g} แต่ในตารางรวมได้ {blueprint.total_marks:g}"
        )
    return blueprint


def _row(raw: dict[str, Any], levels: tuple[str, ...]) -> Row:
    try:
        items = {str(k): int(v) for k, v in (raw.get("items") or {}).items() if int(v)}
    except (TypeError, ValueError) as exc:
        raise BlueprintError(f"จำนวนข้อต้องเป็นจำนวนเต็ม: {raw!r}") from exc

    unknown = sorted(set(items) - set(levels))
    if unknown:
        raise BlueprintError(
            "ระดับพฤติกรรมที่ไม่รู้จัก: " + ", ".join(unknown) + " — ใช้ได้: " + ", ".join(levels)
        )
    if any(v < 0 for v in items.values()):
        raise BlueprintError(f"จำนวนข้อติดลบ: {raw!r}")

    try:
        topic = str(raw["topic"]).strip()
        marks_each = float(raw.get("marks_each", 1.0))
        periods = int(raw.get("periods", 0))
    except (KeyError, TypeError, ValueError) as exc:
        raise BlueprintError(f"แถวไม่ครบ (ต้องมี topic): {raw!r}") from exc

    if not topic:
        raise BlueprintError("มีหน่วยการเรียนที่ไม่มีชื่อ")
    if marks_each <= 0:
        raise BlueprintError(f"คะแนนต่อข้อของ {topic!r} ต้องมากกว่าศูนย์")
    return Row(topic=topic, items=items, marks_each=marks_each, periods=periods)
