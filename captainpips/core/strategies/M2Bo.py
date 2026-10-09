"""
M2 BO — structure-break breakout strategy.

Activates after a broken structure of ≥3 confirmed legs.
The key level is Stop 2 of the broken structure.
An entry fires when a candle CLOSES beyond that key level.

  Bear structure broken (bull reversal):
    Entry  = BUY at close of first candle closing ABOVE Stop-2 HIGH
    SL     = LOW of the breaking stop (last stop)

  Bull structure broken (bear reversal):
    Entry  = SELL at close of first candle closing BELOW Stop-2 LOW
    SL     = HIGH of the breaking stop (last stop)

This is NOT a BaseStrategy subclass. It mirrors the Paradoxical interface:
  on_break()            — called after every structure break
  on_candle()           — called on every bar; returns Signal when triggered
  is_active()           — True while watching for entry
  get_levels()          — returns (key_level, sl_level) when active
  set_activation_index()— record the break candle index to skip same-bar entry
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from captainpips.core.definitions import Candle, Direction, Segment, Signal

log = logging.getLogger(__name__)


@dataclass
class M2BO:
    """
    Stateful detector for the M2 BO trade setup.

    Caller responsibilities
    -----------------------
    1. Call on_break() after every confirmed structure break, passing
       the broken structure's direction, stops list, and confirmed_legs count.
    2. Call on_candle() on every subsequent bar.
    3. When on_candle() returns a Signal, execute it (signal.sl_override
       carries the pre-computed SL price for this setup).
    """

    active: bool = False
    entry_direction: Direction | None = None
    key_level: float | None = None       # stop-2 high (BUY) or stop-2 low (SELL)
    sl_level: float | None = None        # last-stop low (BUY) or last-stop high (SELL)
    activation_index: int | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def on_break(
        self,
        direction: Direction,
        stops: list[Segment],
        confirmed_legs: int,
    ) -> None:
        """
        Record a structure break and arm the strategy when eligible.

        Parameters
        ----------
        direction:      Direction of the broken structure (BULL or BEAR).
        stops:          All stop segments of the broken structure, in order.
        confirmed_legs: Number of confirmed legs in the broken structure.
        """
        # Reset any prior armed state — a new break always resets
        self.active = False
        self.entry_direction = None
        self.key_level = None
        self.sl_level = None
        self.activation_index = None

        # Require at least 3 legs → at least 2 stops must exist (stop1, stop2, …)
        if confirmed_legs < 3 or len(stops) < 2:
            log.debug(
                "M2BO: on_break skipped (confirmed_legs=%d stops=%d)",
                confirmed_legs, len(stops),
            )
            return

        stop2     = stops[1]    # 0-indexed: the 2nd stop of the structure (key level)
        last_stop = stops[-1]   # the stop that broke the structure

        if direction == Direction.BULL:
            # Bull structure broken by a bear move → SELL setup
            # Entry: candle closes BELOW stop-2 LOW
            # SL:    HIGH of the breaking stop (price came from there)
            self.entry_direction = Direction.BEAR
            self.key_level       = stop2.low
            self.sl_level        = last_stop.high

        elif direction == Direction.BEAR:
            # Bear structure broken by a bull move → BUY setup
            # Entry: candle closes ABOVE stop-2 HIGH
            # SL:    LOW of the breaking stop (price came from there)
            self.entry_direction = Direction.BULL
            self.key_level       = stop2.high
            self.sl_level        = last_stop.low

        else:
            return

        self.active = True
        log.info(
            "M2BO armed: entry_dir=%s key_level=%.5f sl_level=%.5f "
            "confirmed_legs=%d stops=%d",
            self.entry_direction.value,
            self.key_level,
            self.sl_level,
            confirmed_legs,
            len(stops),
        )

    def on_candle(self, candle: Candle) -> Signal | None:
        """
        Check whether the candle triggers an M2 BO entry.

        Returns None while inactive or before the key level is broken.
        The returned Signal carries sl_override with the pre-computed SL price.
        """
        if not self.active:
            return None
        if self.key_level is None or self.sl_level is None:
            return None

        # Skip the candle on which on_break was called (same-bar protection)
        if self.activation_index is not None and candle.index == self.activation_index:
            return None

        if self.entry_direction == Direction.BULL:
            # BUY: wait for a candle close ABOVE stop-2 HIGH
            if candle.close > self.key_level:
                self.active = False  # one shot — disarm immediately
                log.info(
                    "M2BO BUY triggered: close=%.5f > key=%.5f sl=%.5f",
                    candle.close, self.key_level, self.sl_level,
                )
                return Signal(
                    symbol="",
                    direction=Direction.BULL,
                    entry_price=candle.close,
                    reason=(
                        f"M2BO: close {candle.close:.5f} > stop-2 high {self.key_level:.5f}"
                    ),
                    strategy="M2BO",
                    leg_number=0,
                    sl_override=self.sl_level,
                )

        elif self.entry_direction == Direction.BEAR:
            # SELL: wait for a candle close BELOW stop-2 LOW
            if candle.close < self.key_level:
                self.active = False
                log.info(
                    "M2BO SELL triggered: close=%.5f < key=%.5f sl=%.5f",
                    candle.close, self.key_level, self.sl_level,
                )
                return Signal(
                    symbol="",
                    direction=Direction.BEAR,
                    entry_price=candle.close,
                    reason=(
                        f"M2BO: close {candle.close:.5f} < stop-2 low {self.key_level:.5f}"
                    ),
                    strategy="M2BO",
                    leg_number=0,
                    sl_override=self.sl_level,
                )

        return None

    def is_active(self) -> bool:
        """True while watching for an entry candle."""
        return self.active

    def get_levels(self) -> tuple[float, float] | None:
        """Return (key_level, sl_level) when active, else None."""
        if self.active and self.key_level is not None and self.sl_level is not None:
            return (self.key_level, self.sl_level)
        return None

    def set_activation_index(self, index: int) -> None:
        """
        Record the bar index on which the break occurred.
        on_candle() skips that bar to prevent same-candle entry.
        Call immediately after on_break() when is_active() is True.
        """
        if self.active:
            self.activation_index = index
