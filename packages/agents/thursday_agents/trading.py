"""Trading Agent (§15, V14).

The brief asks for an **optional, isolated** trading module and says plainly: do not promise
profitability, and live execution must not bypass the risk manager. This agent is the thin
surface over `thursday_trading`, and almost all of its interesting properties are things it
cannot do.

It can run a backtest, size a hypothetical order through the risk manager so the owner can
see the arithmetic, and report where a strategy sits on the ladder. It cannot place an
order, because `trade.execute` is in the Permission Engine's **BLOCK set** — no grant, no
configuration and no reasoning of its own reaches it — and because there is no live broker
behind the port to reach in the first place (ADR 0063).

That is two independent refusals for the same act, which is deliberate. The absent adapter
is the one a bug cannot get past; the blocked verb is the one a *future* adapter cannot be
switched on behind.

**Nothing here is advice.** A backtest describes what a set of rules did on a set of bars.
The report carries that sentence in its payload rather than only in this docstring, so
nothing downstream can quote the number without it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from thursday_shared.enums import ModelTier, PermissionLevel
from thursday_shared.models import AgentResult, AgentSpec, Budget, JobContract
from thursday_trading.backtest import run
from thursday_trading.models import Bar, Side, Signal, Stage
from thursday_trading.risk import Account, Limits, RiskManager, RiskRefused
from thursday_trading.stages import Progression, StageRefused

from thursday_agents.base import BaseAgent

ACTIONS: tuple[str, ...] = ("backtest", "size", "ladder")


class TradingAgent(BaseAgent):
    spec = AgentSpec(
        name="trading",
        description=(
            "Runs deterministic backtests, sizes a hypothetical order through the risk "
            "manager, and reports a strategy's position on the backtest→paper→limited→live "
            "ladder. Cannot place an order: there is no live broker and the verb is blocked."
        ),
        capabilities=["trading", "backtest", "risk", "position-sizing", "strategy"],
        tools=[],
        agent_type="specialist",
        supported_input=["action", "bars", "signal", "equity", "limits"],
        supported_output=["report", "summary"],
        output_schema={"report": "dict", "summary": "string", "action": "string"},
        # READ. It computes over numbers the caller supplied and returns them.
        permission_ceiling=PermissionLevel.READ,
        default_budget=Budget(seconds=60, tool_calls=0, usd=0.0),
        model_tier=ModelTier.LOCAL,
        cost_profile="free",
        latency_profile="fast",
        privacy_profile="local_only",
        user_description=(
            "ทดสอบกลยุทธ์กับข้อมูลย้อนหลัง และคำนวณขนาดสถานะตามกฎความเสี่ยงที่ตั้งไว้ ส่งคำสั่งซื้อขายจริงไม่ได้"
        ),
        user_examples=[
            "ทดสอบกลยุทธ์นี้กับข้อมูลย้อนหลัง",
            "ถ้าเสี่ยง 1% ต่อไม้ ควรซื้อกี่หุ้น",
            "กลยุทธ์นี้อยู่ขั้นไหนแล้ว",
        ],
        safety_notes=(
            "ผลทดสอบย้อนหลังคือสิ่งที่กฎชุดนี้ทำกับข้อมูลชุดนั้น ไม่ใช่การคาดการณ์อนาคต "
            "และไม่ใช่คำแนะนำการลงทุน — Thursday ส่งคำสั่งซื้อขายจริงไม่ได้ ไม่มีโค้ดเชื่อมโบรกเกอร์ "
            "และคำสั่ง trade.execute อยู่ในรายการที่ห้ามถาวร"
        ),
        system_prompt="",
    )

    async def execute(self, contract: JobContract, ctx: Any) -> AgentResult:
        action = str(contract.inputs.get("action") or "").strip().lower()
        if action not in ACTIONS:
            return self._refuse(action, f"ไม่รู้จักงาน {action!r} — ทำได้: " + ", ".join(ACTIONS))

        try:
            report, summary = self._make(action, contract.inputs)
        except (RiskRefused, StageRefused) as exc:
            return self._refuse(action, str(exc))
        except (KeyError, TypeError, ValueError) as exc:
            return self._refuse(action, f"ข้อมูลไม่ครบหรือผิดรูปแบบ: {exc}")

        # `count` and `items` are lifted out of the report so the Supervisor's arithmetic
        # check recomputes the trade count rather than taking the summary's word for it.
        output: dict[str, Any] = {"report": report, "summary": summary, "action": action}
        output.update({k: report[k] for k in ("count", "items") if k in report})

        return AgentResult(
            agent=self.spec.name,
            ok=True,
            output=output,
            summary=summary,
            evidence=[{"action": action, "computed": "คำนวณจากข้อมูลที่ให้มา"}],
        )

    def _make(self, action: str, inputs: dict[str, Any]) -> tuple[dict[str, Any], str]:
        if action == "ladder":
            progression = Progression(str(inputs.get("strategy") or "strategy"))
            for stage_name in inputs.get("approved") or []:
                progression.approve(Stage(str(stage_name)))
            report = progression.to_dict()
            blocked = report["blockers"].get(str(Stage.LIVE)) or []
            summary = f"{progression.strategy}: ขั้นปัจจุบัน {progression.stage}"
            if blocked:
                summary += " — ขึ้น LIVE ไม่ได้: " + " / ".join(blocked)
            return report, summary

        manager = RiskManager(_limits(inputs.get("limits")))

        if action == "size":
            signal = _signal(inputs.get("signal") or {})
            account = Account(equity=float(inputs.get("equity", 100_000.0)))
            # Always PAPER. There is no input that selects a stage, because a stage
            # parameter is how a caller would ask for a live order.
            order = manager.size(signal, account, stage=Stage.PAPER)
            report = {
                "symbol": order.symbol,
                "side": str(order.side),
                "quantity": order.quantity,
                "price": order.price,
                "stop": order.stop,
                "notional": order.notional,
                "risk": order.risk,
                "rationale": order.rationale,
                "stage": str(order.stage),
                "placed": False,
                # The rules that produced the number, beside the number. A size with no
                # limits shown is a figure the owner has to take on trust.
                "risk_rules": manager.to_dict(),
            }
            summary = (
                f"{order.symbol}: {order.quantity:,.4f} หน่วย มูลค่า {order.notional:,.2f} "
                f"เสี่ยงสูงสุด {order.risk:,.2f} ({order.rationale}) — ยังไม่ได้ส่งคำสั่ง"
            )
            return report, summary

        bars = [_bar(b) for b in inputs.get("bars") or []]
        crossover = _crossover(int(inputs.get("fast", 3)), int(inputs.get("slow", 8)))
        result = run(
            crossover,
            bars,
            name=str(inputs.get("strategy") or "sma-crossover"),
            symbol=str(inputs.get("symbol") or ""),
            equity=float(inputs.get("equity", 100_000.0)),
            risk=manager,
            stage=Stage.BACKTEST,
        )
        report = result.to_dict()
        summary = (
            f"{report['strategy']} บน {report['bars']} แท่ง: {report['count']} ไม้ "
            f"ผลตอบแทน {report['return_percent']:+.2f}% "
            f"ขาดทุนสูงสุด {report['max_drawdown']:.1%}"
        )
        if report["halted"]:
            summary += f" — หยุดกลางทาง: {report['halted']}"
        return report, summary

    def _refuse(self, action: str, reason: str) -> AgentResult:
        return AgentResult(
            agent=self.spec.name,
            ok=False,
            output={"report": {}, "summary": "", "action": action},
            error=reason,
            summary=reason,
        )


def _crossover(fast: int, slow: int):
    """A moving-average crossover, in the repository so a backtest has something to run.

    Deliberately ordinary. It is here to exercise the engine, not because this project has
    a view on whether it works — and it sees only the bars handed to it.
    """
    if fast >= slow:
        raise ValueError(f"ค่าเฉลี่ยสั้น {fast} ต้องน้อยกว่าค่าเฉลี่ยยาว {slow}")

    def strategy(seen: list[Bar]) -> Signal | None:
        if len(seen) < slow + 1:
            return None
        closes = [b.close for b in seen]
        fast_now = sum(closes[-fast:]) / fast
        slow_now = sum(closes[-slow:]) / slow
        fast_before = sum(closes[-fast - 1 : -1]) / fast
        slow_before = sum(closes[-slow - 1 : -1]) / slow
        if fast_before <= slow_before and fast_now > slow_now:
            last = seen[-1]
            low = min(b.low for b in seen[-slow:])
            if low >= last.close:
                return None
            return Signal("", Side.BUY, last.when, last.close, round(low, 4), "ตัดขึ้น")
        return None

    return strategy


def _bar(raw: Any) -> Bar:
    if isinstance(raw, Bar):
        return raw
    data = dict(raw)
    when = data["when"]
    return Bar(
        when=when if isinstance(when, datetime) else datetime.fromisoformat(str(when)),
        open=float(data["open"]),
        high=float(data["high"]),
        low=float(data["low"]),
        close=float(data["close"]),
        volume=float(data.get("volume", 0.0)),
    )


def _signal(raw: dict[str, Any]) -> Signal:
    when = raw.get("when")
    return Signal(
        symbol=str(raw.get("symbol") or ""),
        side=Side(str(raw.get("side", "BUY")).upper()),
        when=when
        if isinstance(when, datetime)
        else datetime.fromisoformat(str(when or datetime.now().isoformat())),
        price=float(raw["price"]),
        stop=float(raw["stop"]),
        reason=str(raw.get("reason", "")),
    )


def _limits(raw: Any) -> Limits:
    data = dict(raw or {})
    return Limits(
        risk_per_trade=float(data.get("risk_per_trade", 0.01)),
        max_position=float(data.get("max_position", 0.20)),
        max_daily_loss=float(data.get("max_daily_loss", 0.03)),
        max_drawdown=float(data.get("max_drawdown", 0.10)),
        max_positions=int(data.get("max_positions", 5)),
        lot_size=float(data.get("lot_size", 0.0)),
    )
