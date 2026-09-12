"""Trading: optional, isolated, and with no way to place a real order (§15, V14).

The brief asks for market data → signals → strategy → risk manager → execution, a stage
ladder from backtest to live, and one rule above the rest: **live execution must not bypass
the risk manager.**

Two decisions shape everything here.

**The rule is structural, not procedural.** `Order` is constructed in `risk.py` and nowhere
else — a test walks the package's AST and asserts it. A strategy emits a `Signal`, which
carries no size; only `RiskManager.size` turns one into something with a quantity on it. So
"the risk manager cannot be bypassed" is not a convention somebody has to remember.

**There is no live broker, and that is the design.** `Stage.LIVE` resolves to `NoLiveBroker`,
which satisfies the port and refuses. This mirrors what `communication.py` does with
outbound mail: no code path to a sent message. A live adapter is a few hundred lines and an
API key, and the moment it exists the thing between a bug and somebody's money is a
conditional rather than an absence.

**Nothing here promises profitability.** A backtest describes what those rules did on that
data. `StageResult.verdict` is "ran", not "passed", and promoting a strategy up the ladder
is the owner's decision taken with the figures in front of them — not a threshold this
project invented.
"""

from thursday_trading.backtest import BacktestReport, Trade, run
from thursday_trading.models import Bar, Fill, Order, Side, Signal, Stage
from thursday_trading.ports import Broker, BrokerRefused, NoLiveBroker, PaperBroker, broker_for
from thursday_trading.risk import Account, Limits, RiskManager, RiskRefused
from thursday_trading.stages import Progression, StageRefused, StageResult

__all__ = [
    "Account",
    "BacktestReport",
    "Bar",
    "Broker",
    "BrokerRefused",
    "Fill",
    "Limits",
    "NoLiveBroker",
    "Order",
    "PaperBroker",
    "Progression",
    "RiskManager",
    "RiskRefused",
    "Side",
    "Signal",
    "Stage",
    "StageRefused",
    "StageResult",
    "Trade",
    "broker_for",
    "run",
]
