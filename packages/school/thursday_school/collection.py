"""The collection: what is on the shelves, how old it is, and what it is missing (§15, V13).

Where `circulation` counts what moved, this counts what is there. Both are questions a
school librarian is asked with a deadline attached — a stock report, a weeding round, a
budget request — and both are arithmetic over records rather than judgement.

The number this exists for is **age**. A school library fails quietly: nothing breaks, the
shelves stay full, and the science section slowly becomes a history-of-science section. A
count of items published before a cut-off is the cheapest way to see that, and it is exactly
the figure a stock report is expected to carry.

What it does not do is decide. It will say that 62% of the science holdings predate the
cut-off and that the ratio is 7.4 items per pupil; it will not say the collection is bad.
Weeding a title is a librarian's call, and a threshold invented here would be a standard
this project made up.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any


class CollectionError(ValueError):
    """A holdings record that cannot be true."""


@dataclass(frozen=True)
class Item:
    """One title, with however many copies the library holds."""

    title: str
    category: str = "ไม่ระบุ"
    #: Year of publication, in whichever era the library catalogues in. Zero means unknown,
    #: and unknown is carried through as unknown rather than counted as old or as new.
    year: int = 0
    copies: int = 1

    @property
    def known_year(self) -> bool:
        return self.year > 0


def _share(part: int, whole: int) -> float:
    return round(part / whole * 100, 2) if whole else 0.0


def analyse(
    items: list[Item],
    *,
    older_than: int | None = None,
    enrolled: int | None = None,
) -> dict[str, Any]:
    """Count the shelves.

    `older_than` is a publication year, not an age in years, so a report re-run next term
    gives the same answer about the same books. Items with no year are reported in their own
    bucket and left out of the age shares — a book whose year nobody recorded is not
    evidence of anything, and counting it either way would invent some.
    """
    if not items:
        raise CollectionError("ไม่มีรายการหนังสือให้วิเคราะห์")

    copies = sum(i.copies for i in items)
    by_category: Counter[str] = Counter()
    for item in items:
        by_category[item.category or "ไม่ระบุ"] += item.copies

    categories = [
        {"category": c, "copies": n, "share": _share(n, copies)}
        for c, n in sorted(by_category.items(), key=lambda kv: (-kv[1], kv[0]))
    ]

    summary: dict[str, Any] = {
        "titles": len(items),
        "count": len(items),
        "copies": copies,
        "by_category": categories,
        "unknown_year": sum(i.copies for i in items if not i.known_year),
        "percentages": [c["share"] for c in categories],
    }

    if enrolled is not None:
        if enrolled <= 0:
            raise CollectionError(f"จำนวนนักเรียน {enrolled} คน เป็นไปไม่ได้")
        summary["enrolled"] = enrolled
        summary["copies_per_pupil"] = round(copies / enrolled, 2)

    if older_than is not None:
        dated = [i for i in items if i.known_year]
        dated_copies = sum(i.copies for i in dated)
        old_copies = sum(i.copies for i in dated if i.year < older_than)
        summary["age"] = {
            "before": older_than,
            # Denominator is the dated stock, not the whole collection. A library that has
            # not recorded half its publication years does not thereby have a young half.
            "dated_copies": dated_copies,
            "older_copies": old_copies,
            "older_share": _share(old_copies, dated_copies),
            "by_category": [
                {
                    "category": category,
                    "dated": (dc := sum(i.copies for i in dated if i.category == category)),
                    "older": (
                        oc := sum(
                            i.copies
                            for i in dated
                            if i.category == category and i.year < older_than
                        )
                    ),
                    "older_share": _share(oc, dc),
                }
                for category in sorted({i.category for i in dated})
            ],
        }
    return summary


def gaps(items: list[Item], target: dict[str, float]) -> list[dict[str, Any]]:
    """Compare the collection against a target distribution.

    The target is the library's own — a standard, a policy, last year's shape. Nothing here
    supplies one, because a target invented by this module would be a standard nobody
    adopted. The gap is stated in both percentage points and copies, since "you are 8 points
    short in science" and "that is 41 books" are the same fact and only one of them can be
    ordered.
    """
    if not items:
        raise CollectionError("ไม่มีรายการหนังสือให้เทียบ")
    total = round(sum(target.values()), 4)
    if abs(total - 100.0) > 0.01:
        raise CollectionError(f"สัดส่วนเป้าหมายรวม {total:g}% ไม่เท่ากับ 100%")

    copies = sum(i.copies for i in items)
    held: Counter[str] = Counter()
    for item in items:
        held[item.category or "ไม่ระบุ"] += item.copies

    out: list[dict[str, Any]] = []
    for category in sorted(set(target) | set(held)):
        want = float(target.get(category, 0.0))
        have = held.get(category, 0)
        have_share = _share(have, copies)
        out.append(
            {
                "category": category,
                "copies": have,
                "share": have_share,
                "target": want,
                "gap": round(have_share - want, 2),
                "copies_to_target": round(copies * want / 100 - have, 1),
            }
        )
    return out


def build(rows: list[Item] | list[dict[str, Any]]) -> list[Item]:
    """Parse holdings, refusing records that cannot be true."""
    if not rows:
        raise CollectionError("ไม่มีรายการหนังสือ")
    return [r if isinstance(r, Item) else _item(r) for r in rows]


def _item(raw: dict[str, Any]) -> Item:
    try:
        title = str(raw["title"]).strip()
        copies = int(raw.get("copies", 1))
        year = int(raw.get("year", 0) or 0)
    except (KeyError, TypeError, ValueError) as exc:
        raise CollectionError(f"รายการหนังสือไม่ครบ (ต้องมี title): {raw!r}") from exc

    if not title:
        raise CollectionError("มีรายการหนังสือที่ไม่มีชื่อ")
    if copies <= 0:
        raise CollectionError(f"{title}: จำนวนเล่ม {copies} เป็นไปไม่ได้")
    if year < 0:
        raise CollectionError(f"{title}: ปีพิมพ์ {year} เป็นไปไม่ได้")

    return Item(
        title=title,
        category=str(raw.get("category", "") or "ไม่ระบุ").strip(),
        year=year,
        copies=copies,
    )
