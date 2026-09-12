"""Rubrics: a grid whose weights add up, or a refusal (§15, V13).

A rubric is arithmetic wearing a table. Criteria carry weights, weights make points, points
make a mark — and every one of those steps is a calculation somebody will be held to when a
parent asks why their child got 17 and not 18.

So none of it is asked of a model. `DataAgent`'s rule applies here for the same reason: a
plausible number that is wrong is the worst possible output, because it survives every check
except the one nobody ran. The model, where one is used at all, writes the *descriptors* —
the sentences describing what "ดี" looks like for this criterion — over a grid whose numbers
are already fixed.

The one hard rule: **weights must total 100.** Not "are normalised to 100" — a rubric whose
weights were silently scaled is a rubric that no longer says what its author wrote, and the
author is the person who has to defend it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: Thailand's usual four-band scale, best first. Bands are data rather than an enum because
#: a school that grades on three bands or five is not a school this should refuse to serve.
DEFAULT_BANDS: tuple[str, ...] = ("ดีมาก", "ดี", "พอใช้", "ควรปรับปรุง")

#: Weights are percentages and must land exactly here.
TOTAL_WEIGHT = 100.0

#: Floating point. 0.1 + 0.2 is not 0.3, and a rubric is not wrong because of that.
TOLERANCE = 0.01


class RubricError(ValueError):
    """A rubric that does not add up. Raised rather than corrected."""


@dataclass(frozen=True)
class Criterion:
    """One row. `weight` is a percentage of the whole rubric."""

    name: str
    weight: float
    #: Descriptor per band, best first, aligned with the rubric's bands. Empty is allowed —
    #: a grid with the numbers right and the wording still to write is a useful half.
    descriptors: tuple[str, ...] = ()

    def points(self, total_points: float) -> float:
        """What this row is worth, in marks."""
        return round(total_points * self.weight / TOTAL_WEIGHT, 2)


@dataclass
class Rubric:
    """A scoring grid. Construct through `build` so it cannot exist without adding up."""

    title: str
    criteria: list[Criterion] = field(default_factory=list)
    bands: tuple[str, ...] = DEFAULT_BANDS
    total_points: float = 100.0

    @property
    def weights(self) -> list[float]:
        return [c.weight for c in self.criteria]

    @property
    def weight_total(self) -> float:
        return round(sum(self.weights), 4)

    def points_for(self, criterion: str) -> float:
        for item in self.criteria:
            if item.name == criterion:
                return item.points(self.total_points)
        raise RubricError(f"ไม่มีเกณฑ์ชื่อ {criterion!r} ในรูบริกนี้")

    def band_value(self, band: str) -> int:
        """Best band scores highest. Four bands give 4-3-2-1, three give 3-2-1."""
        try:
            index = self.bands.index(band)
        except ValueError:
            raise RubricError(f"ไม่มีระดับ {band!r} — รูบริกนี้มีระดับ {', '.join(self.bands)}") from None
        return len(self.bands) - index

    def score(self, awarded: dict[str, str]) -> dict[str, Any]:
        """Turn one student's bands into a mark.

        Every criterion must be given a band. A missing one is refused rather than treated
        as zero: a zero is a judgement, and nobody made it.
        """
        missing = [c.name for c in self.criteria if c.name not in awarded]
        if missing:
            raise RubricError("ยังไม่ได้ให้ระดับกับเกณฑ์: " + ", ".join(missing))

        best = len(self.bands)
        rows: list[dict[str, Any]] = []
        earned = 0.0
        for criterion in self.criteria:
            band = awarded[criterion.name]
            value = self.band_value(band)
            available = criterion.points(self.total_points)
            marks = round(available * value / best, 2)
            earned += marks
            rows.append(
                {
                    "criterion": criterion.name,
                    "band": band,
                    "weight": criterion.weight,
                    "available": available,
                    "marks": marks,
                }
            )

        earned = round(earned, 2)
        return {
            "rows": rows,
            "count": len(rows),
            "earned": earned,
            "total": self.total_points,
            "percent": round(earned / self.total_points * 100, 2) if self.total_points else 0.0,
        }

    def grid(self) -> list[dict[str, Any]]:
        """The rubric as a table somebody can print."""
        return [
            {
                "criterion": c.name,
                "weight": c.weight,
                "points": c.points(self.total_points),
                "descriptors": {
                    band: (c.descriptors[i] if i < len(c.descriptors) else "")
                    for i, band in enumerate(self.bands)
                },
            }
            for c in self.criteria
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "bands": list(self.bands),
            "total_points": self.total_points,
            "criteria": self.grid(),
            "count": len(self.criteria),
            # Named `percentages` on purpose: the Supervisor checks any list under that key
            # sums to 100 (§18). The rubric is handing over the evidence for its own claim
            # rather than asserting it.
            "percentages": self.weights,
        }


def build(
    title: str,
    criteria: list[Criterion] | list[dict[str, Any]],
    *,
    bands: tuple[str, ...] = DEFAULT_BANDS,
    total_points: float = 100.0,
) -> Rubric:
    """Make a rubric, or refuse with the arithmetic that stopped it."""
    if not criteria:
        raise RubricError("รูบริกต้องมีเกณฑ์อย่างน้อยหนึ่งข้อ")
    if len(bands) < 2:
        raise RubricError("รูบริกต้องมีระดับคุณภาพอย่างน้อยสองระดับ")
    if total_points <= 0:
        raise RubricError(f"คะแนนเต็ม {total_points} เป็นไปไม่ได้")

    rows = [c if isinstance(c, Criterion) else _criterion(c) for c in criteria]

    names = [c.name for c in rows]
    duplicates = {n for n in names if names.count(n) > 1}
    if duplicates:
        raise RubricError("เกณฑ์ซ้ำกัน: " + ", ".join(sorted(duplicates)))
    if any(not c.name.strip() for c in rows):
        raise RubricError("มีเกณฑ์ที่ไม่มีชื่อ")

    negative = [c.name for c in rows if c.weight <= 0]
    if negative:
        raise RubricError("น้ำหนักต้องมากกว่าศูนย์: " + ", ".join(negative))

    total = round(sum(c.weight for c in rows), 4)
    if abs(total - TOTAL_WEIGHT) > TOLERANCE:
        # Refused, never rescaled. A rubric whose weights were quietly normalised is one the
        # teacher who wrote it can no longer recognise, and they are the person who has to
        # explain it to a parent.
        raise RubricError(
            f"น้ำหนักรวม {total:g}% ไม่เท่ากับ 100% — "
            + ", ".join(f"{c.name} {c.weight:g}%" for c in rows)
        )

    for criterion in rows:
        if criterion.descriptors and len(criterion.descriptors) != len(bands):
            raise RubricError(
                f"เกณฑ์ {criterion.name!r} มีคำอธิบาย {len(criterion.descriptors)} ระดับ "
                f"แต่รูบริกมี {len(bands)} ระดับ"
            )

    return Rubric(title=title, criteria=rows, bands=tuple(bands), total_points=total_points)


def _criterion(raw: dict[str, Any]) -> Criterion:
    try:
        return Criterion(
            name=str(raw["name"]).strip(),
            weight=float(raw["weight"]),
            descriptors=tuple(str(d) for d in raw.get("descriptors") or ()),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise RubricError(f"เกณฑ์ไม่ครบ (ต้องมี name และ weight): {raw!r}") from exc
