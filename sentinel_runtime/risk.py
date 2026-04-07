from __future__ import annotations

from decimal import Decimal

from .config import RiskConfig
from .models import BalanceSnapshot, RiskEvaluation, RiskSnapshot


class RiskManager:
    def __init__(self, config: RiskConfig) -> None:
        self._config = config
        self._starting_balance = config.starting_balance
        self._hard_stop_reason: str | None = None

    @property
    def starting_balance(self) -> Decimal | None:
        return self._starting_balance

    def restore_starting_balance(self, starting_balance: Decimal | None) -> None:
        if self._starting_balance is None and starting_balance is not None:
            self._starting_balance = starting_balance

    def bootstrap(self, current_balance: Decimal) -> None:
        if self._starting_balance is None:
            self._starting_balance = current_balance

    def evaluate(
        self,
        balance_snapshot: BalanceSnapshot,
        daily_realized_pnl: Decimal,
        open_positions: int,
        open_orders: int,
    ) -> RiskEvaluation:
        self.bootstrap(balance_snapshot.total_equity)
        assert self._starting_balance is not None

        baseline_balance = self._starting_balance
        max_daily_loss_amount = baseline_balance * self._config.max_daily_loss_pct
        max_drawdown_amount = baseline_balance * self._config.max_drawdown_pct
        minimum_reserve_balance = baseline_balance * self._config.min_balance_reserve_pct
        current_drawdown_amount = max(Decimal("0"), baseline_balance - balance_snapshot.total_equity)
        drawdown_pct = (
            current_drawdown_amount / baseline_balance
            if baseline_balance > 0
            else Decimal("0")
        )
        snapshot = RiskSnapshot(
            total_equity=balance_snapshot.total_equity,
            available_balance=balance_snapshot.available_balance,
            daily_realized_pnl=daily_realized_pnl,
            open_positions=open_positions,
            open_orders=open_orders,
            drawdown_pct=drawdown_pct,
            minimum_reserve_balance=minimum_reserve_balance,
            max_daily_loss_amount=max_daily_loss_amount,
            max_drawdown_amount=max_drawdown_amount,
        )

        if self._hard_stop_reason is not None:
            return RiskEvaluation(allowed=False, reason=self._hard_stop_reason, snapshot=snapshot)

        if daily_realized_pnl <= -max_daily_loss_amount:
            self._hard_stop_reason = (
                f"Hard stop: daily loss limit reached ({daily_realized_pnl} <= -{max_daily_loss_amount})."
            )
            return RiskEvaluation(allowed=False, reason=self._hard_stop_reason, snapshot=snapshot)

        if current_drawdown_amount >= max_drawdown_amount:
            self._hard_stop_reason = (
                f"Hard stop: drawdown limit reached ({current_drawdown_amount} >= {max_drawdown_amount})."
            )
            return RiskEvaluation(allowed=False, reason=self._hard_stop_reason, snapshot=snapshot)

        if balance_snapshot.available_balance < minimum_reserve_balance:
            return RiskEvaluation(
                allowed=False,
                reason=(
                    f"Blocked: available balance {balance_snapshot.available_balance} is below "
                    f"the reserve floor {minimum_reserve_balance}."
                ),
                snapshot=snapshot,
            )

        if open_positions >= self._config.max_open_positions:
            return RiskEvaluation(
                allowed=False,
                reason=f"Blocked: open positions {open_positions} >= limit {self._config.max_open_positions}.",
                snapshot=snapshot,
            )

        if open_orders >= self._config.max_open_orders:
            return RiskEvaluation(
                allowed=False,
                reason=f"Blocked: open orders {open_orders} >= limit {self._config.max_open_orders}.",
                snapshot=snapshot,
            )

        return RiskEvaluation(allowed=True, reason=None, snapshot=snapshot)
