"""The ladder, and the rung with nothing behind it (V14)."""

from __future__ import annotations

from datetime import date, datetime

import pytest
from thursday_trading.models import Bar, Order, Side, Stage, rank
from thursday_trading.ports import (
    Broker,
    BrokerRefused,
    NoLiveBroker,
    PaperBroker,
    broker_for,
)
from thursday_trading.stages import MIN_TRADES, Progression, StageRefused, StageResult


def result(stage=Stage.BACKTEST, trades=42, pnl=1250.0) -> StageResult:
    return StageResult(
        stage=stage,
        trades=trades,
        pnl=pnl,
        max_drawdown=0.06,
        started=date(2025, 1, 1),
        finished=date(2025, 12, 31),
    )


def test_a_new_strategy_starts_at_backtest_and_may_go_no_further():
    progression = Progression("ma-cross")
    assert progression.stage is Stage.BACKTEST
    assert progression.may_run(Stage.BACKTEST)
    assert not progression.may_run(Stage.PAPER)


def test_the_rungs_must_be_climbed_in_order():
    progression = Progression("ma-cross")
    progression.approve(Stage.LIVE)
    blockers = progression.blockers(Stage.LIVE)
    assert any("ยังไม่ได้รัน LIMITED" in b for b in blockers)


def test_a_recorded_run_is_not_a_promotion():
    """Recording says it happened. Climbing is the owner's decision (§8)."""
    progression = Progression("ma-cross")
    progression.record(result())
    assert progression.blockers(Stage.PAPER) == ["เจ้าของยังไม่อนุมัติให้ขึ้น PAPER"]
    assert progression.stage is Stage.BACKTEST


def test_the_owner_approving_is_what_opens_the_next_rung():
    progression = Progression("ma-cross")
    progression.record(result())
    progression.approve(Stage.PAPER)
    assert progression.may_run(Stage.PAPER)
    assert progression.stage is Stage.PAPER


def test_a_run_with_too_few_trades_is_not_evidence():
    progression = Progression("ma-cross")
    progression.record(result(trades=MIN_TRADES - 1))
    progression.approve(Stage.PAPER)
    assert any(str(MIN_TRADES) in b for b in progression.blockers(Stage.PAPER))


def test_a_losing_backtest_still_counts_as_having_run():
    """Requiring a profit would be this project inventing a standard — and one that
    rewards exactly what a backtest is best at producing, a curve fitted to the past."""
    progression = Progression("ma-cross")
    progression.record(result(pnl=-5000.0))
    progression.approve(Stage.PAPER)
    assert progression.may_run(Stage.PAPER)
    assert progression.results[Stage.BACKTEST].to_dict()["verdict"] == "ran"


def test_a_stage_cannot_be_recorded_before_its_rung_is_open():
    progression = Progression("ma-cross")
    with pytest.raises(StageRefused, match="ยังรัน LIMITED ไม่ได้"):
        progression.record(result(stage=Stage.LIMITED))


def test_the_full_ladder_takes_three_approvals():
    progression = Progression("ma-cross")
    progression.record(result(Stage.BACKTEST))
    progression.approve(Stage.PAPER)
    progression.record(result(Stage.PAPER))
    progression.approve(Stage.LIMITED)
    progression.record(result(Stage.LIMITED))
    progression.approve(Stage.LIVE)
    assert progression.stage is Stage.LIVE
    assert progression.blockers(Stage.LIVE) == []


def test_a_result_that_ends_before_it_starts_is_refused():
    with pytest.raises(StageRefused, match="ก่อนวันเริ่ม"):
        StageResult(Stage.BACKTEST, 5, 0.0, 0.0, date(2026, 2, 1), date(2026, 1, 1))


def test_stage_rank_is_explicit_never_a_string_comparison():
    """`"LIVE" < "PAPER"` is true as strings, and that is how a guest once took CRITICAL
    actions (`test_structural_audit_v86.py`)."""
    assert rank(Stage.BACKTEST) < rank(Stage.PAPER) < rank(Stage.LIMITED) < rank(Stage.LIVE)
    assert str(Stage.LIVE) < str(Stage.PAPER), "which is exactly why rank() exists"


# ------------------------------------------------------------------- the rung with nothing


def test_live_resolves_to_a_broker_that_refuses():
    broker = broker_for(Stage.LIVE)
    assert isinstance(broker, NoLiveBroker)
    assert isinstance(broker, Broker), "it satisfies the port rather than being absent"
    assert broker.live is False


def test_a_live_order_is_refused_with_the_reason():
    order = Order("PTT", Side.BUY, 100, 35.0, 33.5, Stage.LIVE)
    bar = Bar(datetime(2026, 1, 2), 35.0, 35.5, 34.5, 35.2)
    with pytest.raises(BrokerRefused) as raised:
        broker_for(Stage.LIVE).execute(order, bar)
    assert "ไม่มีช่องทางส่งคำสั่งซื้อขายจริง" in str(raised.value)
    assert "ports.py" in str(raised.value), "the refusal points at where to look"


def test_no_broker_in_this_package_is_live():
    """The property that makes the ladder safe today: nothing here can move money."""
    for stage in Stage:
        assert broker_for(stage).live is False


def test_limited_runs_on_paper_and_does_not_pretend_otherwise():
    """The brief calls LIMITED a small live test, which needs the adapter that does not
    exist. It runs on paper rather than being a rung that silently means the one below."""
    assert isinstance(broker_for(Stage.LIMITED), PaperBroker)


# ------------------------------------------------------------------------------ paper fills


def test_a_paper_fill_uses_the_bar_open_not_its_close():
    """A signal from a closed bar can only be acted on in the next one. Filling at the
    close of the bar that produced it is the commonest look-ahead there is."""
    order = Order("PTT", Side.BUY, 100, 35.0, 33.5, Stage.PAPER)
    bar = Bar(datetime(2026, 1, 2), 30.0, 40.0, 29.0, 39.0)
    fill = PaperBroker(slippage=0).execute(order, bar)
    assert fill.price == 30.0


def test_slippage_always_works_against_the_order():
    bar = Bar(datetime(2026, 1, 2), 100.0, 101.0, 99.0, 100.5)
    buy = PaperBroker(slippage=0.01).execute(Order("X", Side.BUY, 1, 100, 95, Stage.PAPER), bar)
    sell = PaperBroker(slippage=0.01).execute(Order("X", Side.SELL, 1, 100, 105, Stage.PAPER), bar)
    assert buy.price == 101.0, "a buyer pays more"
    assert sell.price == 99.0, "a seller receives less"
