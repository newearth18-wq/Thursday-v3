"""The Trading agent, and the two things that stop it placing an order (V14).

The brief's rule for this module is short: do not promise profitability, and live
execution must not bypass the risk manager. The tests below are mostly about the second
half. The agent can compute; it cannot trade, and it cannot be made to trade by asking
differently — the verb is blocked and the adapter is missing, independently.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from thursday_agents.trading import ACTIONS, TradingAgent
from thursday_core.supervisor import Supervisor
from thursday_security.policy import HARD_BLOCKED, PolicyTable
from thursday_shared.enums import PermissionLevel, PolicyDecision
from thursday_shared.ids import new_id
from thursday_shared.models import JobContract, Spend
from thursday_trading.models import Stage
from thursday_trading.ports import NoLiveBroker, broker_for

# A downtrend, a crossover, then a rally that gives the position back. Chosen so the run
# produces exactly one closed trade at a loss: a fixture that wins would let a wrong
# assertion about the disclaimer pass unnoticed.
CLOSES = [
    100,
    99,
    98,
    97,
    96,
    95,
    94,
    93,
    92,
    94,
    97,
    101,
    105,
    109,
    113,
    117,
    120,
    118,
    112,
    104,
    96,
]


def bars(closes: list[float]) -> list[dict[str, float | str]]:
    start = datetime(2026, 3, 2, 9, 30)
    rows = []
    for i, close in enumerate(closes):
        open_ = closes[i - 1] if i else close
        rows.append(
            {
                "when": (start + timedelta(days=i)).isoformat(),
                "open": round(open_, 2),
                "high": round(max(open_, close) + 0.5, 2),
                "low": round(min(open_, close) - 0.5, 2),
                "close": round(close, 2),
                "volume": 1000,
            }
        )
    return rows


class Ctx:
    spend = Spend()

    async def emit(self, event):
        return None


def contract(**inputs) -> JobContract:
    return JobContract(
        task_id=new_id(),
        step_id=new_id(),
        agent="trading",
        objective="งานเทรด",
        inputs=inputs,
    )


@pytest.fixture
def agent() -> TradingAgent:
    return TradingAgent()


# ------------------------------------------------------------------------------ backtest


async def test_it_runs_a_backtest_and_reports_what_the_rules_did(agent):
    result = await agent.run(
        contract(action="backtest", bars=bars(CLOSES), symbol="AOT", fast=3, slow=8), Ctx()
    )
    assert result.ok, result.error
    report = result.output["report"]
    assert report["bars"] == len(CLOSES)
    assert report["count"] == 1
    assert report["count"] == len(report["items"])
    assert report["pnl"] < 0
    assert report["final_equity"] == pytest.approx(
        report["starting_equity"] + report["pnl"], abs=0.01
    )
    assert "1 ไม้" in result.summary


async def test_the_trade_rows_carry_the_symbol_the_run_was_given(agent):
    """The strategy is handed bars, not a ticker, so the signal it returns has no symbol.
    If the run does not put one on, the header names the instrument and the rows do not."""
    result = await agent.run(contract(action="backtest", bars=bars(CLOSES), symbol="AOT"), Ctx())
    assert [t["symbol"] for t in result.output["report"]["items"]] == ["AOT"]


async def test_every_backtest_carries_the_sentence_that_stops_it_being_advice(agent):
    result = await agent.run(contract(action="backtest", bars=bars(CLOSES), symbol="AOT"), Ctx())
    disclaimer = result.output["report"]["disclaimer"]
    assert "ไม่ใช่การคาดการณ์อนาคต" in disclaimer
    assert "ไม่ใช่คำแนะนำการลงทุน" in disclaimer


async def test_a_run_with_no_crossover_reports_nothing_rather_than_inventing_one(agent):
    """A flat market is a real answer. Zero trades, and a summary that says zero."""
    flat = [100.0] * 20
    result = await agent.run(contract(action="backtest", bars=bars(flat), symbol="AOT"), Ctx())
    assert result.ok, result.error
    assert result.output["report"]["count"] == 0
    assert result.output["report"]["items"] == []
    assert "0 ไม้" in result.summary


async def test_two_bars_are_not_a_backtest(agent):
    result = await agent.run(contract(action="backtest", bars=bars([100, 101])[:1]), Ctx())
    assert not result.ok
    assert "อย่างน้อยสองแท่ง" in result.error


# ---------------------------------------------------------------------------------- size


async def test_it_shows_the_arithmetic_behind_a_position_size(agent):
    result = await agent.run(
        contract(
            action="size",
            signal={"symbol": "PTT", "side": "BUY", "price": 100.0, "stop": 95.0},
            equity=100_000.0,
        ),
        Ctx(),
    )
    assert result.ok, result.error
    report = result.output["report"]
    # 1% of 100,000 = 1,000 of risk, over a 5-baht stop = 200 units.
    assert report["quantity"] == pytest.approx(200.0)
    assert report["risk"] == pytest.approx(1_000.0)
    assert "1.0%" in report["rationale"]


async def test_the_size_report_shows_the_rules_that_produced_it(agent):
    """A quantity with no limits beside it is a figure the owner has to take on trust."""
    result = await agent.run(
        contract(
            action="size",
            limits={"risk_per_trade": 0.005},
            signal={"symbol": "PTT", "side": "BUY", "price": 100.0, "stop": 95.0},
        ),
        Ctx(),
    )
    rules = result.output["report"]["risk_rules"]
    assert rules["halted"] is False
    assert rules["limits"]["risk_per_trade"] == 0.005
    assert set(rules["limits"]) >= {"max_position", "max_daily_loss", "max_drawdown", "lot_size"}


async def test_a_size_is_never_a_placed_order(agent):
    result = await agent.run(
        contract(
            action="size",
            signal={"symbol": "PTT", "side": "BUY", "price": 100.0, "stop": 95.0},
        ),
        Ctx(),
    )
    assert result.output["report"]["placed"] is False
    assert result.output["report"]["stage"] == str(Stage.PAPER)
    assert "ยังไม่ได้ส่งคำสั่ง" in result.summary


async def test_asking_for_a_live_stage_is_not_a_thing_the_agent_accepts(agent):
    """There is no `stage` input. A caller who supplies one is ignored, not obeyed —
    a stage parameter is the shape a request for a live order would take."""
    result = await agent.run(
        contract(
            action="size",
            stage="LIVE",
            signal={"symbol": "PTT", "side": "BUY", "price": 100.0, "stop": 95.0},
        ),
        Ctx(),
    )
    assert result.ok, result.error
    assert result.output["report"]["stage"] == str(Stage.PAPER)


async def test_a_signal_with_no_stop_is_refused_rather_than_given_one(agent):
    result = await agent.run(
        contract(action="size", signal={"symbol": "PTT", "side": "BUY", "price": 100.0}), Ctx()
    )
    assert not result.ok
    assert "ข้อมูลไม่ครบ" in result.error


async def test_a_board_lot_rounds_down_and_says_so(agent):
    """SET trades in hundreds. 200 units is two lots exactly; 250 would be two, not three —
    rounding up would put more on the line than the risk figure allows."""
    result = await agent.run(
        contract(
            action="size",
            equity=125_000.0,
            limits={"lot_size": 100},
            signal={"symbol": "PTT", "side": "BUY", "price": 100.0, "stop": 95.0},
        ),
        Ctx(),
    )
    assert result.ok, result.error
    report = result.output["report"]
    assert report["quantity"] == 200.0  # 1,250 of risk ÷ 5 = 250, down to two lots
    assert report["risk"] == pytest.approx(1_000.0)  # and the risk taken falls with it
    assert "ปัดลงเป็นล็อต" in report["rationale"]


async def test_an_account_too_small_for_one_lot_is_told_so(agent):
    result = await agent.run(
        contract(
            action="size",
            equity=10_000.0,
            limits={"lot_size": 100},
            signal={"symbol": "PTT", "side": "BUY", "price": 100.0, "stop": 95.0},
        ),
        Ctx(),
    )
    assert not result.ok
    assert "ไม่ถึงขั้นต่ำ" in result.error
    assert "เล็กเกินไป" in result.error


# -------------------------------------------------------------------------------- ladder


async def test_the_ladder_says_what_is_missing_before_live(agent):
    result = await agent.run(contract(action="ladder", strategy="sma-crossover"), Ctx())
    assert result.ok, result.error
    report = result.output["report"]
    assert report["stage"] == str(Stage.BACKTEST)
    assert report["blockers"][str(Stage.LIVE)]
    assert "ขึ้น LIVE ไม่ได้" in result.summary


async def test_approval_alone_does_not_move_a_strategy_up(agent):
    """Approving LIVE without the stages below it recorded leaves LIVE blocked. Consent is
    one of the conditions, not a way around the others."""
    result = await agent.run(
        contract(action="ladder", strategy="sma-crossover", approved=["PAPER", "LIMITED", "LIVE"]),
        Ctx(),
    )
    assert result.ok, result.error
    assert result.output["report"]["stage"] == str(Stage.BACKTEST)
    assert result.output["report"]["blockers"][str(Stage.LIVE)]


async def test_a_recorded_stage_says_it_ran_never_that_it_passed(agent):
    result = await agent.run(contract(action="ladder", strategy="sma-crossover"), Ctx())
    for recorded in result.output["report"]["results"].values():
        assert recorded["verdict"] == "ran"


# ------------------------------------------------------------------------------ refusals


async def test_an_unknown_action_lists_the_ones_that_exist(agent):
    result = await agent.run(contract(action="execute"), Ctx())
    assert not result.ok
    for action in ACTIONS:
        assert action in result.error


async def test_the_agent_has_no_action_that_places_an_order():
    assert "execute" not in ACTIONS
    assert "order" not in ACTIONS


# ------------------------------------------------ the two independent refusals of a trade


def test_the_execute_verb_is_in_the_block_set():
    assert "trade.execute" in HARD_BLOCKED
    policy = PolicyTable().get("trade.execute")
    assert policy.level is PermissionLevel.ADMIN
    assert policy.default is PolicyDecision.BLOCK


def test_and_there_is_no_live_broker_behind_the_port_either():
    """Belt and braces, deliberately. The blocked verb is what a future adapter cannot be
    switched on behind; the absent adapter is what a bug cannot get past."""
    broker = broker_for(Stage.LIVE)
    assert isinstance(broker, NoLiveBroker)
    assert broker.live is False
    assert NoLiveBroker.REASON


def test_paper_trading_asks_every_single_time():
    policy = PolicyTable().get("trade.paper")
    assert policy.level is PermissionLevel.EXTERNAL
    assert policy.default is PolicyDecision.ASK_ALWAYS
    assert policy.reversible is False


# ------------------------------------------------------- the Supervisor checks the counts


async def test_the_trade_count_is_offered_for_recomputation(agent):
    result = await agent.run(contract(action="backtest", bars=bars(CLOSES), symbol="AOT"), Ctx())
    report = await Supervisor(models=None, use_llm_critique=False).verify(
        contract(action="backtest"), result
    )
    assert "count_matches_items" in {c["name"] for c in report.checks}
    assert report.verdict.value == "PASS", report.checks


# --------------------------------------------------------------------------------- wiring


def test_the_agent_is_registered_and_cannot_reach_anything(container):
    """No tools and a READ ceiling. Whatever the model decides it wants to do, the most
    this agent can do is arithmetic over numbers the caller already had."""
    spec = container.agents.get("trading").spec
    assert spec.permission_ceiling is PermissionLevel.READ
    assert spec.tools == []
    assert spec.privacy_profile == "local_only"
    assert spec.user_description and spec.user_examples and spec.output_schema


def test_the_agent_tells_the_owner_up_front_that_it_cannot_trade(container):
    spec = container.agents.get("trading").spec
    assert "ส่งคำสั่งซื้อขายจริงไม่ได้" in spec.user_description
    assert "ไม่ใช่คำแนะนำการลงทุน" in spec.safety_notes
