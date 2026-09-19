"""
Base types and constants for CaptainPips trading robot.

This module is the single source of truth for all shared data structures.
It imports only from the candle classifier — never from other captainpips modules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import ClassVar

from captainpips.connectors import candle_classifier


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class CandleLabel(Enum):
    """Candle classification labels produced by the decision tree."""
    C = "C"   # Continuation
    N = "N"   # Neutral
    B = "B"   # Base
    P = "P"   # Participant
    R = "R"   # Reversal
    S = "S"   # Stop
    L = "L"   # Large stop
    D = "D"   # Doji stop
    J = "J"   # Jump stop
    WR = "WR" # Weak reversal
    WB = "WB" # Weak base


class Direction(Enum):
    """Directional bias of a candle, segment, or signal."""
    BULL = "BULL"
    BEAR = "BEAR"
    NONE = "NONE"


class SegmentKind(Enum):
    """Whether a segment is an active leg or a stop region."""
    LEG = "LEG"
    STOP = "STOP"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Candle:
    """Immutable representation of a single classified OHLC candle."""

    index: int
    time: str          # HH:MM format
    raw_time: int      # Unix timestamp from the EA
    label: CandleLabel
    direction: Direction
    open: float
    high: float
    low: float
    close: float

    @classmethod
    def from_raw(
        cls,
        index: int,
        time: str,
        raw_time: int,
        open: float,
        high: float,
        low: float,
        close: float,
    ) -> Candle:
        """
        Construct a Candle by running the decision-tree classifier.

        Args:
            index:    Bar index on the chart.
            time:     Bar open time as HH:MM string.
            raw_time: Bar open time as Unix timestamp (epoch seconds).
            open:     OHLC open price.
            high:     OHLC high price.
            low:      OHLC low price.
            close:    OHLC close price.

        Returns:
            A fully classified, immutable Candle instance.
        """
        label_str, direction_str = candle_classifier.classify_candle(
            open, high, low, close
        )
        return cls(
            index=index,
            time=time,
            raw_time=raw_time,
            label=CandleLabel(label_str),
            direction=Direction(direction_str),
            open=open,
            high=high,
            low=low,
            close=close,
        )

    @property
    def structure_direction(self) -> Direction:
        """Shadow-based direction for P/R; close-based for all others."""
        if self.label in (CandleLabel.P, CandleLabel.R):
            upper = self.high - max(self.open, self.close)
            lower = min(self.open, self.close) - self.low
            if lower > upper:
                return Direction.BULL
            if upper > lower:
                return Direction.BEAR
            return Direction.NONE
        return self.direction

    @property
    def close_direction(self) -> Direction:
        """Direction based on close vs open only."""
        if self.close > self.open:
            return Direction.BULL
        if self.close < self.open:
            return Direction.BEAR
        return Direction.NONE


@dataclass
class Segment:
    """
    An ordered sequence of candles sharing a structural role (leg or stop).

    Computed properties derive from the candle list and are never stored
    separately — the list is the single source of truth.
    """

    kind: SegmentKind
    direction: Direction
    candles: list[Candle] = field(default_factory=list)

    @property
    def high(self) -> float:
        """Highest high across all candles in the segment."""
        return max(c.high for c in self.candles)

    @property
    def low(self) -> float:
        """Lowest low across all candles in the segment."""
        return min(c.low for c in self.candles)

    @property
    def n_candles(self) -> int:
        """Number of candles in the segment."""
        return len(self.candles)

    @property
    def first_candle(self) -> Candle:
        """First (oldest) candle in the segment."""
        return self.candles[0]

    @property
    def last_candle(self) -> Candle:
        """Last (most recent) candle in the segment."""
        return self.candles[-1]


@dataclass(frozen=True)
class Signal:
    """Immutable trading signal emitted by a strategy."""

    symbol: str
    direction: Direction
    entry_price: float
    reason: str
    strategy: str
    leg_number: int


# ---------------------------------------------------------------------------
# Label-set constants
# ---------------------------------------------------------------------------

ALWAYS_STOP_LABELS: frozenset[CandleLabel] = frozenset({
    CandleLabel.S,
    CandleLabel.L,
    CandleLabel.D,
    CandleLabel.J,
})
"""Labels that unconditionally end a leg and open a stop segment."""

BASE_LABELS: frozenset[CandleLabel] = frozenset({
    CandleLabel.C,
    CandleLabel.N,
    CandleLabel.B,
})
"""Neutral / base candles that do not carry directional intent."""

PARTICIPANT_LABELS: frozenset[CandleLabel] = frozenset({
    CandleLabel.P,
    CandleLabel.R,
    CandleLabel.WR,
    CandleLabel.WB,
})
"""Candles that participate in a leg but are not pure continuations."""

FRESH_STOP_LABELS: frozenset[CandleLabel] = frozenset({
    CandleLabel.S,
    CandleLabel.D,
    CandleLabel.J,
})
"""Stop labels that represent a fresh (non-large) stopping event."""
