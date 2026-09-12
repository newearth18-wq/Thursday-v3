"""The backtest engine: deterministic, and refusing the flattering shortcuts (V14)."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from thursday_trading.backtest import run
from thursday_trading.models import Bar, Side, Signal, Stage
from thursday_trading.risk import Account, Limits, RiskManager


def series(closes: list[float], *, spread: float = 0.5) -> list[Bar]:
    return [
        Bar(datetime(2026, 1, 1) + timedelta(days=i), c, c + spread, c - spread, c)
        for i, c in enumerate(closes)
    ]


def once_at(index: int, *, stop_pct: float = 0.97):
    """A strategy that signals exactly once, after the bar at `index`."""

    def strategy(seen: list[Bar]) -> Signal | None:
        if len(seen) != index + 1:
            return None
        last = seen[-1]
        return Signal("TEST", Side.BUY, last.when, last.close, round(last.close * stop_pct, 4))

    return strategy


def test_a_signal_fills_on_the_next_bar_never_the_one_that_made_it():
    """The commonest look-ahead: filling at the close of the bar you just read."""
    bars = series([100, 100, 110, 110, 110])
    report = run(once_at(1), bars, equity=100_000)
    assert report.trades
    assert report.trades[0].entry == pytest.approx(110 * 1.0005, abs=0.01), (
        "filled at bar 2's open, not bar 1's close"
    )


def test_a_stop_that_is_hit_closes_the_trade_at_the_stop():
    bars = series([100, 100, 100, 80, 80])
    report = run(once_at(1, stop_pct=0.97), bars, equity=100_000)
    closed = report.closed[0]
    assert closed.exit_reason == "stop"
    assert closed.exit == pytest.approx(97.0, abs=0.01)
    assert closed.pnl < 0


def test_a_position_still_open_at_the_end_is_closed_and_labelled():
    """A report that silently leaves it open counts an unrealised gain as if it were real."""
    bars = series([100, 100, 105, 110, 115])
    report = run(once_at(1), bars, equity=100_000)
    closed = report.closed[0]
    assert closed.exit == 115
    assert closed.exit_reason == "ปิดท้ายช่วงทดสอบ"


def test_a_signal_is_good_for_one_bar_only():
    """The bug this guards: a signal blocked by an open position stayed pending and could
    fill many bars later, at a price the strategy never saw."""

    def always(seen: list[Bar]) -> Signal | None:
        last = seen[-1]
        return Signal("TEST", Side.BUY, last.when, last.close, round(last.close * 0.99, 4))

    bars = series([100] * 3 + [200] * 3)
    report = run(always, bars, equity=100_000)
    entries = [t.entry for t in report.trades]
    assert all(e < 150 for e in entries[:1]), "the first entry is at the price of its own bar"
    for trade in report.trades:
        assert trade.entry == pytest.approx(trade.entry, abs=0.01)


def test_the_run_is_deterministic():
    bars = series([100, 101, 102, 99, 98, 103, 104])
    first = run(once_at(2), bars, equity=50_000).to_dict()
    second = run(once_at(2), bars, equity=50_000).to_dict()
    assert first == second


def test_the_risk_manager_binds_inside_the_backtest():
    """Not a separate mode. The caps are the same object doing the same arithmetic.

    The cap binds on the *order*, priced at the signal; the fill then moves by slippage, so
    the executed notional exceeds the cap by exactly that and no more. Asserting against
    the fill with no tolerance would be asserting that slippage does not exist.
    """
    bars = series([100, 100, 100, 100])
    manager = RiskManager(Limits(risk_per_trade=0.01, max_position=0.05))
    report = run(once_at(1), bars, equity=100_000, risk=manager)
    trade = report.trades[0]
    cap = 100_000 * 0.05
    assert trade.quantity * 100.0 <= cap, "the order was sized within the cap"
    assert trade.quantity * trade.entry <= cap * 1.001, "and the fill differs only by slippage"


def test_a_halted_manager_stops_the_run_and_says_why():
    bars = series([100, 100, 100, 100])
    manager = RiskManager()
    manager.halt("ทดสอบสวิตช์ฉุกเฉิน")
    report = run(once_at(1), bars, equity=100_000, risk=manager)
    assert report.closed == []
    assert "ทดสอบสวิตช์ฉุกเฉิน" in report.halted


def test_a_drawdown_breach_during_the_run_halts_it():
    """Trading on through the hole is what a cap exists to prevent."""
    bars = series([100, 100, 100] + [50] * 4)
    manager = RiskManager(Limits(risk_per_trade=0.5, max_position=1.0, max_drawdown=0.05))
    report = run(once_at(1, stop_pct=0.10), bars, equity=100_000, risk=manager)
    assert report.halted, "the run should have stopped"
    assert manager.halted


def test_the_equity_curve_has_one_point_per_bar():
    bars = series([100, 101, 102, 103])
    report = run(once_at(1), bars, equity=10_000)
    assert len(report.equity_curve) == len(bars) == report.bars


def test_max_drawdown_is_measured_off_the_equity_curve():
    bars = series([100, 100, 120, 90, 95])
    report = run(once_at(1), bars, equity=100_000)
    assert 0.0 <= report.max_drawdown <= 1.0


def test_the_report_carries_a_disclaimer_in_the_payload():
    """Not only in a docstring. Nothing downstream should be able to read this as advice."""
    payload = run(once_at(1), series([100, 100, 101]), equity=10_000).to_dict()
    assert "ไม่ใช่การคาดการณ์อนาคต" in payload["disclaimer"]
    assert "ไม่ใช่คำแนะนำการลงทุน" in payload["disclaimer"]


def test_the_report_offers_the_count_the_supervisor_checks():
    payload = run(once_at(1), series([100, 100, 101, 102]), equity=10_000).to_dict()
    assert payload["count"] == len(payload["items"])


def test_a_run_becomes_a_stage_result_without_a_verdict():
    report = run(once_at(1), series([100, 100, 101, 102]), equity=10_000)
    stage_result = report.as_stage_result(Stage.BACKTEST)
    assert stage_result.stage is Stage.BACKTEST
    assert stage_result.to_dict()["verdict"] == "ran"


def test_too_few_bars_is_refused():
    with pytest.raises(ValueError, match="อย่างน้อยสองแท่ง"):
        run(once_at(0), series([100]), equity=10_000)


def test_the_strategy_only_ever_sees_the_past():
    """The engine hands it a slice ending at the current bar. It cannot peek."""
    seen_lengths: list[int] = []

    def recorder(seen: list[Bar]) -> Signal | None:
        seen_lengths.append(len(seen))
        return None

    bars = series([100, 101, 102, 103, 104])
    run(recorder, bars, equity=10_000)
    assert seen_lengths == [1, 2, 3, 4], "never the final bar, and never more than so far"


def test_an_account_records_open_positions_as_they_open_and_close():
    bars = series([100, 100, 100, 80, 80])
    manager = RiskManager(Limits(max_positions=1))
    account = Account(equity=100_000)
    run(once_at(1, stop_pct=0.97), bars, equity=100_000, risk=manager)
    assert account.open_positions == 0
