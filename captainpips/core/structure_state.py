"""
Live structure state — processes candles one at a time and builds
the leg/stop structure.

Imports only from definitions and structure_rules.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

log = logging.getLogger(__name__)

from captainpips.core.definitions import (
    ALWAYS_STOP_LABELS,
    FRESH_STOP_LABELS,
    Candle,
    Direction,
    Segment,
    SegmentKind,
)
from captainpips.core.shift_scorer import ShiftScorer
from captainpips.core.structure_rules import (
    BreakDetector,
    LegCounter,
    LegValidator,
    StructurePoints,
)


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

class EventKind(Enum):
    """Types of structural events emitted by the builder."""
    LEG_CONFIRMED = "LEG_CONFIRMED"
    STOP_STARTED = "STOP_STARTED"
    STRUCTURE_BROKEN = "STRUCTURE_BROKEN"
    SINGLE_LEG_REVERSED = "SINGLE_LEG_REVERSED"


@dataclass(frozen=True)
class StructureEvent:
    """An event produced when structure changes."""
    kind: EventKind
    segment: Segment | None = None
    confirmed_legs: int = 0


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------

class StructureBuilder:
    """Processes candles one at a time and builds the leg/stop structure."""

    def __init__(self) -> None:
        self.segments: list[Segment] = []
        self.candidate: list[Candle] = []
        self.all_candles: list[Candle] = []
        self.direction: Direction | None = None
        self.confirmed_legs: int = 0
        self.start_index: int = 0
        self._scorer = ShiftScorer()

    # ---- public API -------------------------------------------------------

    def process(
        self,
        candle: Candle,
        *,
        ema_values: list[float] | None = None,
    ) -> list[StructureEvent]:
        """Process one candle and return any structural events."""
        self.all_candles.append(candle)
        log.debug(
            "process(): segments=%d last=%s candidate=%s candle=%s %s",
            len(self.segments),
            self.segments[-1].kind if self.segments else "none",
            [c.label.value for c in self.candidate],
            candle.label.value,
            candle.structure_direction,
        )
        if not self.segments:
            events = self._handle_fresh(candle)
        elif self.segments[-1].kind == SegmentKind.LEG:
            events = self._handle_leg_active(candle)
        else:
            events = self._handle_stop_active(candle)

        events.extend(self._check_breaks(ema_values))
        return events

    def reset(self, from_candle_index: int) -> None:
        """Clear all state. Used after a structure break."""
        self.segments.clear()
        self.candidate.clear()
        self.all_candles.clear()
        self.direction = None
        self.confirmed_legs = 0
        self.start_index = from_candle_index

    def replay(self, candles: list[Candle]) -> None:
        """Reset then process each candle in order."""
        self.reset(candles[0].index if candles else 0)
        for candle in candles:
            self.process(candle)

    # ---- state handlers ---------------------------------------------------

    def _handle_fresh(self, candle: Candle) -> list[StructureEvent]:
        """No segments yet — accumulate toward the first leg."""
        if candle.label in FRESH_STOP_LABELS:
            if self.candidate and LegValidator.is_valid_leg(self.candidate):
                return self._promote_and_stop(candle)
            self.candidate.clear()
            self.direction = None
            return []

        if candle.label in ALWAYS_STOP_LABELS:
            if self.candidate and LegValidator.is_valid_leg(self.candidate):
                return self._promote_and_stop(candle)
            self.candidate.clear()
            self.direction = None
            return []

        if self.direction is None:
            self.direction = candle.structure_direction
            self.start_index = candle.index

        if (
            candle.structure_direction == self.direction
            and candle.label not in ALWAYS_STOP_LABELS
        ):
            self.candidate.append(candle)
            return []

        if self.candidate and LegValidator.is_valid_leg(self.candidate):
            return self._promote_and_stop(candle)

        self.candidate.clear()
        if candle.label not in ALWAYS_STOP_LABELS:
            self.direction = candle.structure_direction
            self.start_index = candle.index
            self.candidate.append(candle)
        else:
            self.direction = None
        return []

    def _handle_leg_active(self, candle: Candle) -> list[StructureEvent]:
        """Last segment is a LEG — extend it or start a stop."""
        leg = self.segments[-1]

        if (
            candle.structure_direction == self.direction
            and candle.label not in ALWAYS_STOP_LABELS
        ):
            leg.candles.append(candle)
            return []

        stop = Segment(
            kind=SegmentKind.STOP,
            direction=self.direction,
            candles=[candle],
        )
        self.segments.append(stop)
        return [StructureEvent(EventKind.STOP_STARTED, stop, self.confirmed_legs)]

    def _handle_stop_active(self, candle: Candle) -> list[StructureEvent]:
        """Last segment is a STOP — accumulate candidate or deepen stop."""
        stop = self.segments[-1]
        log.debug(f"stop_active: candle={candle.label.value} "
                  f"struct_dir={candle.structure_direction} "
                  f"self.direction={self.direction} "
                  f"candidate={[c.label.value for c in self.candidate]}")

        if candle.label in ALWAYS_STOP_LABELS:
            if self.candidate and LegValidator.is_valid_leg(self.candidate):
                return self._promote_and_stop(candle)
            stop.candles.append(candle)
            self.candidate.clear()
            return []

        if candle.structure_direction == self.direction:
            self.candidate.append(candle)
            return []

        if self.candidate and LegValidator.is_valid_leg(self.candidate):
            return self._promote_and_stop(candle)
        stop.candles.append(candle)
        self.candidate.clear()
        return []

    # ---- helpers ----------------------------------------------------------

    def _promote_and_stop(self, trigger: Candle) -> list[StructureEvent]:
        """Promote candidate to a confirmed leg and open a stop with *trigger*."""
        # Staircase pre-check: for Leg 2+, verify the candidate extends
        # beyond the previous leg before promoting.
        prev_legs = [s for s in self.segments if s.kind == SegmentKind.LEG]
        if prev_legs:
            prev_leg = prev_legs[-1]
            stops = [s for s in self.segments if s.kind == SegmentKind.STOP]
            between_stop = stops[-1] if stops else None
            prev_end = StructurePoints.leg_endpoint(prev_leg, between_stop)

            temp_leg = Segment(
                kind=SegmentKind.LEG,
                direction=self.direction,
                candles=list(self.candidate),
            )
            temp_stop = Segment(
                kind=SegmentKind.STOP,
                direction=self.direction,
                candles=[trigger],
            )
            curr_end = StructurePoints.leg_endpoint(temp_leg, temp_stop)

            if self.direction == Direction.BULL:
                passes = curr_end > prev_end
            elif self.direction == Direction.BEAR:
                passes = curr_end < prev_end
            else:
                passes = False

            if not passes:
                log.debug(
                    "Staircase pre-check failed: prev_end=%.5f curr_end=%.5f",
                    prev_end, curr_end,
                )
                if self.segments and self.segments[-1].kind == SegmentKind.STOP:
                    self.segments[-1].candles.append(trigger)
                return []

        leg = Segment(
            kind=SegmentKind.LEG,
            direction=self.direction,
            candles=list(self.candidate),
        )
        self.segments.append(leg)
        self.candidate.clear()
        self.confirmed_legs = LegCounter.count(self.segments)

        stop = Segment(
            kind=SegmentKind.STOP,
            direction=self.direction,
            candles=[trigger],
        )
        self.segments.append(stop)

        return [
            StructureEvent(EventKind.LEG_CONFIRMED, leg, self.confirmed_legs),
            StructureEvent(EventKind.STOP_STARTED, stop, self.confirmed_legs),
        ]

    def _check_breaks(
        self,
        ema_values: list[float] | None = None,
    ) -> list[StructureEvent]:
        """Check for structural breaks after every candle."""
        stops = [s for s in self.segments if s.kind == SegmentKind.STOP]
        if not stops:
            return []

        events: list[StructureEvent] = []

        if self.confirmed_legs == 1:
            legs = [s for s in self.segments if s.kind == SegmentKind.LEG]
            prev_stop = stops[0] if len(stops) >= 2 else None

            # Condition 1: geometric reverse
            cond1 = bool(legs) and BreakDetector.is_single_leg_reverse(
                legs[0], stops[-1], prev_stop,
            )

            # Condition 2: shift score (8 elements, C excluded)
            cond2 = False
            if legs and ema_values is not None:
                all_candles = self.all_candles
                score = self._scorer.score(
                    breaking_stop=stops[-1],
                    segments=self.segments,
                    ema_values=ema_values,
                    all_candles=all_candles,
                )
                cond2 = score.true_count >= 6

            if cond1 or cond2:
                events.append(
                    StructureEvent(
                        EventKind.SINGLE_LEG_REVERSED,
                        stops[-1],
                        self.confirmed_legs,
                    )
                )

        if len(stops) >= 2 and BreakDetector.is_geometric_break(stops):
            events.append(
                StructureEvent(
                    EventKind.STRUCTURE_BROKEN,
                    stops[-1],
                    self.confirmed_legs,
                )
            )

        already_broken = any(e.kind == EventKind.STRUCTURE_BROKEN for e in events)
        if not already_broken and len(stops) >= 2 and self.candidate:
            prev_stop = stops[-2]
            prev_breach = StructurePoints.stop_breach(prev_stop)
            log.debug(f"Checking candidate breach: "
                         f"candidate={[c.label.value for c in self.candidate]} "
                         f"prev_stop_breach={prev_breach}")
            for c in self.candidate:
                if self.direction == Direction.BULL:
                    if c.low < prev_breach:
                        log.debug(f"Candidate breach detected (BULL): {c.label.value} low={c.low} < breach={prev_breach}")
                        events.append(StructureEvent(
                            EventKind.STRUCTURE_BROKEN, prev_stop, self.confirmed_legs))
                        break
                elif self.direction == Direction.BEAR:
                    if c.high > prev_breach:
                        log.debug(f"Candidate breach detected (BEAR): {c.label.value} high={c.high} > breach={prev_breach}")
                        events.append(StructureEvent(
                            EventKind.STRUCTURE_BROKEN, prev_stop, self.confirmed_legs))
                        break

        return events
