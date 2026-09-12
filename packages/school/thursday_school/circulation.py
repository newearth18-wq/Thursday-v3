"""Library circulation: what was borrowed, by whom, and how late (§15, V13).

The statistics a school librarian is asked for at the end of a term — how many loans, by
which year group, which titles moved, how many are still out — are counts over records. So
they are counted here, from rows the report can point at, and the rows travel with the
figures for the same reason `DataAgent` carries its own: a number in a report nobody can
trace is a number everybody believes.

Two things this is careful about, both of which are easy to get quietly wrong:

**Overdue is relative to a date.** "How many are overdue" has no answer without saying
*when*. Every calculation here takes the day it is asked about, so a report run in October
about September says what was overdue in September rather than what is overdue now.

**Loans per pupil needs a roll to divide by.** Given no enrolment figure it reports the
loans and leaves the ratio out, rather than dividing by the number of pupils who happened to
borrow something — which flatters the library exactly in proportion to how few children used
it.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Any


class CirculationError(ValueError):
    """A loan record that cannot be true."""


@dataclass(frozen=True)
class Loan:
    """One issue of one item. `returned_on` of `None` means still out."""

    item: str
    borrower: str
    borrowed_on: date
    due_on: date
    returned_on: date | None = None
    #: Class, year group or department — whatever the library reports by.
    group: str = ""
    category: str = ""

    @property
    def out(self) -> bool:
        return self.returned_on is None

    def days_overdue(self, asof: date) -> int:
        """Days past due as at a given day. Zero when returned in time or not yet due."""
        end = self.returned_on or asof
        return max(0, (end - self.due_on).days)

    def overdue(self, asof: date) -> bool:
        """Still out and past due — the list somebody has to chase."""
        return self.out and asof > self.due_on

    def returned_late(self) -> bool:
        """Came back, but after the date. A different question from `overdue`, and
        conflating them undercounts one or overcounts the other: a librarian chasing
        books wants what is still out, and a librarian reviewing the term wants how
        often the deadline was missed."""
        return self.returned_on is not None and self.returned_on > self.due_on

    def loan_days(self, asof: date) -> int:
        return max(0, ((self.returned_on or asof) - self.borrowed_on).days)


def _share(part: int, whole: int) -> float:
    return round(part / whole * 100, 2) if whole else 0.0


def summarise(
    loans: list[Loan],
    *,
    asof: date,
    enrolled: int | None = None,
    top: int = 5,
) -> dict[str, Any]:
    """Count a term's borrowing.

    `enrolled` is the school roll. Without it the per-pupil figure is omitted rather than
    computed against the borrowers, which would make a library used by six children look
    busier than one used by three hundred.
    """
    if not loans:
        raise CirculationError("ไม่มีรายการยืมให้สรุป")

    by_group: Counter[str] = Counter()
    by_month: defaultdict[str, int] = defaultdict(int)
    by_category: Counter[str] = Counter()
    by_item: Counter[str] = Counter()
    borrowers: set[str] = set()

    still_out: list[Loan] = []
    late: list[Loan] = []
    returned_late: list[Loan] = []
    total_loan_days = 0

    for loan in loans:
        by_group[loan.group or "ไม่ระบุ"] += 1
        by_month[f"{loan.borrowed_on:%Y-%m}"] += 1
        by_category[loan.category or "ไม่ระบุ"] += 1
        by_item[loan.item] += 1
        borrowers.add(loan.borrower)
        total_loan_days += loan.loan_days(asof)
        if loan.out:
            still_out.append(loan)
        if loan.overdue(asof):
            late.append(loan)
        if loan.returned_late():
            returned_late.append(loan)

    count = len(loans)
    groups = [
        {"group": g, "loans": n, "share": _share(n, count)}
        for g, n in sorted(by_group.items(), key=lambda kv: (-kv[1], kv[0]))
    ]

    summary: dict[str, Any] = {
        "asof": asof.isoformat(),
        "count": count,
        "borrowers": len(borrowers),
        "titles": len(by_item),
        "still_out": len(still_out),
        "overdue": len(late),
        "overdue_share": _share(len(late), count),
        # Separate from `overdue` on purpose — see `Loan.returned_late`.
        "returned_late": len(returned_late),
        "on_time_share": _share(count - len(late) - len(returned_late), count),
        "average_loan_days": round(total_loan_days / count, 2),
        "by_group": groups,
        "by_month": [{"month": m, "loans": n} for m, n in sorted(by_month.items())],
        "by_category": [
            {"category": c, "loans": n, "share": _share(n, count)}
            for c, n in sorted(by_category.items(), key=lambda kv: (-kv[1], kv[0]))
        ],
        "top_titles": [{"item": i, "loans": n} for i, n in by_item.most_common(max(0, top))],
        "most_overdue": [
            {
                "item": loan.item,
                "borrower": loan.borrower,
                "group": loan.group,
                "days": loan.days_overdue(asof),
                "due_on": loan.due_on.isoformat(),
            }
            for loan in sorted(late, key=lambda x: -x.days_overdue(asof))[: max(0, top)]
        ],
        # The Supervisor recomputes any list under this key (§18). Group shares are the one
        # partition of the loans that is complete, so it is the honest thing to offer.
        "percentages": [g["share"] for g in groups],
    }

    if enrolled is not None:
        if enrolled <= 0:
            raise CirculationError(f"จำนวนนักเรียน {enrolled} คน เป็นไปไม่ได้")
        summary["enrolled"] = enrolled
        summary["loans_per_pupil"] = round(count / enrolled, 2)
        summary["reach_percent"] = _share(len(borrowers), enrolled)
    return summary


def build(rows: list[Loan] | list[dict[str, Any]]) -> list[Loan]:
    """Parse loan records, refusing any that cannot be true."""
    if not rows:
        raise CirculationError("ไม่มีรายการยืม")
    return [r if isinstance(r, Loan) else _loan(r) for r in rows]


def _loan(raw: dict[str, Any]) -> Loan:
    try:
        item = str(raw["item"]).strip()
        borrower = str(raw["borrower"]).strip()
        borrowed_on = _date(raw["borrowed_on"])
        due_on = _date(raw["due_on"])
    except (KeyError, TypeError) as exc:
        raise CirculationError(
            f"รายการยืมไม่ครบ (ต้องมี item, borrower, borrowed_on, due_on): {raw!r}"
        ) from exc

    returned = raw.get("returned_on")
    returned_on = _date(returned) if returned else None

    if not item or not borrower:
        raise CirculationError(f"รายการยืมไม่มีชื่อหนังสือหรือผู้ยืม: {raw!r}")
    if due_on < borrowed_on:
        raise CirculationError(f"{item}: กำหนดคืน {due_on} ก่อนวันยืม {borrowed_on}")
    if returned_on and returned_on < borrowed_on:
        raise CirculationError(f"{item}: คืน {returned_on} ก่อนวันยืม {borrowed_on}")

    return Loan(
        item=item,
        borrower=borrower,
        borrowed_on=borrowed_on,
        due_on=due_on,
        returned_on=returned_on,
        group=str(raw.get("group", "")).strip(),
        category=str(raw.get("category", "")).strip(),
    )


def _date(value: Any) -> date:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise CirculationError(f"วันที่ {value!r} อ่านไม่ได้ — ใช้รูปแบบ YYYY-MM-DD") from exc
