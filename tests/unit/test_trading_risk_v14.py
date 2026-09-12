"""The risk manager: sizing, caps, and the kill switch (V14).

Exact numbers throughout. A position size that is approximately right is a loss that is
approximately bounded.
"""

from __future__ import annotations

import ast
from datetime import date, datetime
from pathlib import Path

import pytest
from thursday_trading.models import Side, Signal, Stage
from thursday_trading.risk import Account, Limits, RiskManager, RiskRefused

WHEN = datetime(2026, 1, 2)


def signal(price=35.0, stop=33.5, side=Side.BUY) -> Signal:
    return Signal("PTT", side, WHEN, price, stop)


def test_size_comes_from_what_the_account_will_lose_at_the_stop():
    """1% of 100,000 is 1,000; a 1.50 stop distance buys 666.67 units."""
    manager = RiskManager(Limits(risk_per_trade=0.01, max_position=1.0))
    order = manager.size(signal(), Account(equity=100_000), stage=Stage.PAPER)
    assert order.quantity == pytest.approx(666.666667, abs=1e-5)
    assert order.risk == pytest.approx(1000.0, abs=0.01)


def test_a_wider_stop_buys_fewer_units_for_the_same_risk():
    manager = RiskManager(Limits(risk_per_trade=0.01, max_position=1.0))
    account = Account(equity=100_000)
    tight = manager.size(signal(stop=34.5), account, stage=Stage.PAPER)
    wide = manager.size(signal(stop=30.0), account, stage=Stage.PAPER)
    assert tight.quantity > wide.quantity
    assert tight.risk == pytest.approx(wide.risk, abs=0.01), "the risk is the constant"


def test_the_position_cap_trims_the_size_and_says_so():
    manager = RiskManager(Limits(risk_per_trade=0.01, max_position=0.20))
    order = manager.size(signal(stop=34.95), Account(equity=100_000), stage=Stage.PAPER)
    assert order.notional == pytest.approx(20_000.0, abs=0.01)
    assert "เพดานสถานะ" in order.rationale


def test_the_rationale_shows_the_arithmetic_that_produced_the_size():
    order = RiskManager().size(signal(), Account(equity=100_000), stage=Stage.PAPER)
    assert "1.0%" in order.rationale and "100,000.00" in order.rationale


def test_a_lot_size_rounds_the_quantity_down_never_up():
    """A venue with board lots cannot fill 666.67 units. Rounding up to 700 would put more
    at risk than the 1% this method was asked for, so the remainder is dropped."""
    manager = RiskManager(Limits(risk_per_trade=0.01, max_position=1.0, lot_size=100))
    order = manager.size(signal(), Account(equity=100_000), stage=Stage.PAPER)
    assert order.quantity == 600.0
    assert order.risk == pytest.approx(900.0, abs=0.01), "less risk than asked for, not more"
    assert "ปัดลงเป็นล็อต" in order.rationale


def test_without_a_lot_size_a_fractional_quantity_is_left_alone():
    """Crypto and FX do trade fractions. A default lot would be a guess about a venue the
    account never named."""
    order = RiskManager(Limits(max_position=1.0)).size(
        signal(), Account(equity=100_000), stage=Stage.PAPER
    )
    assert order.quantity != int(order.quantity)


def test_an_account_that_cannot_afford_one_lot_is_refused_not_given_a_part_lot():
    manager = RiskManager(Limits(lot_size=100))
    with pytest.raises(RiskRefused, match="ไม่ถึงขั้นต่ำ"):
        manager.size(signal(), Account(equity=10_000), stage=Stage.PAPER)


def test_a_negative_lot_size_is_refused():
    with pytest.raises(RiskRefused, match="lot_size"):
        Limits(lot_size=-1)


def test_an_account_too_small_for_the_stop_is_refused_not_rounded_to_zero():
    manager = RiskManager(Limits(risk_per_trade=0.0001))
    with pytest.raises(RiskRefused, match="เป็นศูนย์"):
        manager.size(signal(price=1_000_000, stop=1), Account(equity=100), stage=Stage.PAPER)


# ------------------------------------------------------------------------------- the caps


def test_the_drawdown_cap_stops_the_account():
    manager = RiskManager(Limits(max_drawdown=0.10))
    account = Account(equity=100_000)
    account.mark(89_000, date(2026, 1, 3))
    assert account.drawdown == pytest.approx(0.11, abs=0.001)
    assert manager.check(account)
    with pytest.raises(RiskRefused, match="ขาดทุนสะสม"):
        manager.size(signal(), account, stage=Stage.PAPER)


def test_the_high_water_mark_never_falls():
    """A peak that drops turns a drawdown into a fresh start."""
    account = Account(equity=100_000)
    account.mark(120_000, date(2026, 1, 3))
    account.mark(90_000, date(2026, 1, 4))
    assert account.peak == 120_000
    assert account.drawdown == pytest.approx(0.25, abs=0.001)


def test_the_daily_loss_cap_is_measured_from_the_day_start():
    manager = RiskManager(Limits(max_daily_loss=0.03))
    account = Account(equity=100_000)
    account.mark(100_000, date(2026, 1, 2))
    account.mark(96_000, date(2026, 1, 2))
    assert account.day_loss == pytest.approx(0.04, abs=0.001)
    assert any("วันนี้" in reason for reason in manager.check(account))


def test_a_new_day_resets_the_daily_loss_but_not_the_drawdown():
    manager = RiskManager(Limits(max_daily_loss=0.03, max_drawdown=0.50))
    account = Account(equity=100_000)
    account.mark(96_000, date(2026, 1, 2))
    assert manager.check(account), "the day is over its cap"
    account.mark(96_000, date(2026, 1, 3))
    assert account.day_loss == 0.0, "a new day starts from where the old one ended"
    assert account.drawdown == pytest.approx(0.04, abs=0.001), "the drawdown persists"


def test_too_many_open_positions_blocks_a_new_one():
    manager = RiskManager(Limits(max_positions=2))
    account = Account(equity=100_000, open_positions=2)
    with pytest.raises(RiskRefused, match="เพดาน 2"):
        manager.size(signal(), account, stage=Stage.PAPER)


def test_caps_are_checked_after_a_fill_not_only_before_the_next_signal():
    """A cap checked only on the way in lets a position already open take the account
    past it."""
    manager = RiskManager(Limits(max_drawdown=0.05))
    account = Account(equity=100_000)
    breaches = manager.after_fill(account, 94_000, date(2026, 1, 3))
    assert breaches
    assert manager.halted, "breaching a cap halts without anyone asking"


# ------------------------------------------------------------------------- the kill switch


def test_a_halt_blocks_every_size_until_a_person_clears_it():
    manager = RiskManager()
    manager.halt("ผู้ใช้สั่งหยุด")
    with pytest.raises(RiskRefused, match="ผู้ใช้สั่งหยุด"):
        manager.size(signal(), Account(equity=100_000), stage=Stage.PAPER)

    manager.resume()
    assert manager.size(signal(), Account(equity=100_000), stage=Stage.PAPER).quantity > 0


def test_a_halt_does_not_time_out_or_reset_itself():
    """A kill switch that turns itself back on is not a kill switch. `resume` is the only
    caller, and nothing in this module calls it."""
    source = Path("packages/trading/thursday_trading/risk.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "resume"
    ]
    assert calls == [], "risk.py must never resume itself"


# ------------------------------------------------------------------------------ the limits


@pytest.mark.parametrize(
    "field", ["risk_per_trade", "max_position", "max_daily_loss", "max_drawdown"]
)
def test_a_fraction_outside_zero_to_one_is_refused(field):
    with pytest.raises(RiskRefused, match=field):
        Limits(**{field: 1.5})


def test_an_impossible_account_is_refused():
    with pytest.raises(RiskRefused, match="เป็นไปไม่ได้"):
        Account(equity=0)


# ---------------------------------------------------------- the rule the module exists for


def test_only_the_risk_manager_constructs_an_order():
    """The brief's rule — live execution must not bypass the risk manager — held
    structurally. A strategy that could build an `Order` could size its own position."""
    package = Path("packages/trading/thursday_trading")
    builders: list[str] = []
    for path in package.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "Order"
            ):
                builders.append(path.name)
    assert builders == ["risk.py"], f"Order is constructed outside risk.py: {builders}"
