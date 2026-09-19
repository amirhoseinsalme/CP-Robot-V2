"""
Paradoxical Mode — activates after 3 consecutive alternating structure breaks
and trades a boundary-break of the resulting decision zone.

This is NOT a BaseStrategy subclass. It has its own stateful interface:
  on_break()  — called after every structure break
  on_candle() — called on every bar; returns Signal when zone is broken
  is_active() — reports whether the zone is armed
  get_zone()  — returns (upper, lower) when active

Each break is recorded as (direction, structure_high, structure_low).
Activation fires when the last 3 breaks alternate direction strictly
(BULL→BEAR→BULL or BEAR→BULL→BEAR). The decision zone is computed
from Bear and Bull structures inside those last two breaks.
"""

from __future__ import annotations

from dataclasses import dataclass

from captainpips.core.definitions import Candle, Direction, Signal


# Type alias — one entry in break history
_BreakRecord = tuple[Direction, float, float]  # (direction, high, low)


@dataclass
class Paradoxical:
    """
    Stateful detector for the Paradoxical trade setup.

    Caller responsibilities
    -----------------------
    1. Call on_break() after every confirmed structure break, passing
       the direction of the broken structure plus its full price range.
    2. Call on_candle() on every subsequent bar.
    3. When on_candle() returns a Signal, execute it and stop calling
       on_candle() until the next activation cycle (or reset).
    """

    # Owned state
    active: bool = False
    upper: float | None = None
    lower: float | None = None
    activation_index: int | None = None
    break_history: list[_BreakRecord] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.break_history is None:
            self.break_history = []

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def on_break(
        self,
        direction: Direction,
        structure_high: float,
        structure_low: float,
    ) -> None:
        """
        Record a structure break and check for Paradoxical activation.

        Parameters
        ----------
        direction:       Direction of the structure that broke.
        structure_high:  Highest high of that structure.
        structure_low:   Lowest low of that structure.
        """
        # Deactivate any prior zone — a new break resets the setup
        self.active = False
        self.upper = None
        self.lower = None
        self.activation_index = None

        self.break_history.append((direction, structure_high, structure_low))
        # Keep only the last 3 entries
        if len(self.break_history) > 3:
            self.break_history = self.break_history[-3:]

        if len(self.break_history) == 3 and self._is_alternating():
            self._try_activate()

    def on_candle(self, candle: Candle) -> Signal | None:
        """
        Check whether the candle breaks the decision zone.

        Returns None on the activation candle itself and when inactive.
        Whichever boundary is broken first produces the Signal.
        """
        if not self.active or self.upper is None or self.lower is None:
            return None

        # Skip the candle that triggered activation
        if candle.index == self.activation_index:
            return None

        if candle.high > self.upper:
            return Signal(
                symbol="",
                direction=Direction.BULL,
                entry_price=candle.high,
                reason=f"Paradoxical: candle high {candle.high} broke upper {self.upper}",
                strategy="Paradoxical",
                leg_number=0,
            )

        if candle.low < self.lower:
            return Signal(
                symbol="",
                direction=Direction.BEAR,
                entry_price=candle.low,
                reason=f"Paradoxical: candle low {candle.low} broke lower {self.lower}",
                strategy="Paradoxical",
                leg_number=0,
            )

        return None

    def is_active(self) -> bool:
        """True when the decision zone is armed and watching for a break."""
        return self.active

    def get_zone(self) -> tuple[float, float] | None:
        """Return (upper, lower) when active, else None."""
        if self.active and self.upper is not None and self.lower is not None:
            return (self.upper, self.lower)
        return None

    # -----------------------------------------------------------------------
    # Private helpers
    # -----------------------------------------------------------------------

    def _is_alternating(self) -> bool:
        """True when all three consecutive break directions alternate."""
        d0, d1, d2 = (r[0] for r in self.break_history)
        return d0 != d1 and d1 != d2

    def _try_activate(self) -> None:
        """
        Compute the decision zone from the last 2 breaks and arm if valid.

        Upper = highest high among Bear structures in the last 2 breaks.
        Lower = lowest low  among Bull structures in the last 2 breaks.

        Zone is only armed when upper > lower.
        """
        last_two = self.break_history[-2:]

        bear_highs = [high for d, high, _ in last_two if d == Direction.BEAR]
        bull_lows  = [low  for d, _, low  in last_two if d == Direction.BULL]

        if not bear_highs or not bull_lows:
            return

        upper = max(bear_highs)
        lower = min(bull_lows)

        if upper <= lower:
            return

        self.active = True
        self.upper = upper
        self.lower = lower
        # activation_index must be set by the caller via the next on_candle;
        # we mark None here — the first on_candle will set it on first call
        # only if the caller passes the activation candle explicitly.
        # Callers that track candle index should call set_activation_index().
        self.activation_index = None

    def set_activation_index(self, index: int) -> None:
        """
        Record the bar index on which activation occurred.

        Call this immediately after on_break() confirms activation
        (i.e. is_active() returns True) and pass the current bar's index.
        on_candle() will skip this bar to avoid same-candle entry.
        """
        if self.active:
            self.activation_index = index
