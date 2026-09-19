"""
Position size calculator.

Converts a risk percentage and SL distance into a broker lot size,
rounding down to the nearest lot step to never exceed the stated risk.
"""

from __future__ import annotations

import math


class PositionSizer:
    """Stateless position-size calculator."""

    def calculate(
        self,
        account_balance: float,
        risk_pct: float,
        sl_distance: float,
        tick_size: float,
        tick_value: float,
        lot_step: float,
        min_lot: float,
        max_lot: float,
        multiplier: float = 1.0,
    ) -> float:
        """
        Return the lot size to trade, or 0.0 if the minimum cannot be met.

        Parameters
        ----------
        account_balance:  Current account equity in account currency.
        risk_pct:         Percentage of balance to risk (e.g. 1.0 = 1%).
        sl_distance:      Price distance from entry to stop-loss.
        tick_size:        Smallest price increment for the symbol.
        tick_value:       Monetary value of one tick per 1 lot.
        lot_step:         Broker lot increment (e.g. 0.01).
        min_lot:          Broker minimum lot size.
        max_lot:          Broker maximum lot size.
        multiplier:       Scale factor applied after base calculation (default 1.0).

        Raises
        ------
        ValueError: if sl_distance <= 0.
        """
        if sl_distance <= 0:
            raise ValueError(f"sl_distance must be positive, got {sl_distance}")

        risk_amount = account_balance * risk_pct / 100.0
        ticks_at_risk = sl_distance / tick_size
        base_lot = risk_amount / (ticks_at_risk * tick_value)

        sized_lot = base_lot * multiplier

        # Round DOWN to lot_step — never round up and never exceed stated risk
        floored = math.floor(sized_lot / lot_step) * lot_step
        # Fix floating-point dust (e.g. 0.09999999 → 0.10)
        floored = round(floored, _decimal_places(lot_step))

        if floored < min_lot:
            return 0.0

        return min(floored, max_lot)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _decimal_places(step: float) -> int:
    """Number of decimal places in *step* (e.g. 0.01 → 2, 0.1 → 1)."""
    s = f"{step:.10f}".rstrip("0")
    if "." not in s:
        return 0
    return len(s.split(".")[1])
