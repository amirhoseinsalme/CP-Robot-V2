"""
Leg/stop formation, counting, and break-detection rules.

Imports only from definitions — no strategy logic, no signals, no connectors.
"""

from __future__ import annotations

from captainpips.core.definitions import (
    ALWAYS_STOP_LABELS,
    FRESH_STOP_LABELS,
    Candle,
    CandleLabel,
    Direction,
    Segment,
    SegmentKind,
)


# ---------------------------------------------------------------------------
# Leg validation
# ---------------------------------------------------------------------------

class LegValidator:
    """Static helpers that decide whether a sequence of candles forms a leg."""

    @staticmethod
    def has_valid_base(candles: list[Candle]) -> bool:
        """At least one C/N candle, or at least two B candles."""
        cn = sum(1 for c in candles if c.label in {CandleLabel.C, CandleLabel.N})
        if cn >= 1:
            return True
        b = sum(1 for c in candles if c.label == CandleLabel.B)
        return b >= 2

    @staticmethod
    def no_always_stop(candles: list[Candle]) -> bool:
        """No S/D/J/L anywhere — all are stop-only labels."""
        for c in candles:
            if c.label in ALWAYS_STOP_LABELS:
                return False
        return True

    @staticmethod
    def all_same_direction(candles: list[Candle]) -> bool:
        """Every candle shares a single direction."""
        if not candles:
            return False
        first = candles[0].structure_direction
        return all(c.structure_direction == first for c in candles)

    @staticmethod
    def is_valid_leg(candles: list[Candle]) -> bool:
        """Combines base, stop, and direction checks."""
        if not candles:
            return False
        return (
            LegValidator.all_same_direction(candles)
            and LegValidator.has_valid_base(candles)
            and LegValidator.no_always_stop(candles)
        )


# ---------------------------------------------------------------------------
# Structural point calculations (A / B / C / D)
# ---------------------------------------------------------------------------

class StructurePoints:
    """Compute the price levels that define a leg/stop structure."""

    @staticmethod
    def leg_start(leg: Segment, prev_stop: Segment | None = None) -> float:
        """Starting price of a leg (Point A).

        When *prev_stop* is provided its extreme is folded in — same-direction
        S/L/D/J candles in the preceding stop can define a more extreme origin.
        """
        if leg.direction == Direction.BULL:
            base = min(c.low for c in leg.candles)
            if prev_stop:
                base = min(base, prev_stop.low)
            return base
        base = max(c.high for c in leg.candles)
        if prev_stop:
            base = max(base, prev_stop.high)
        return base

    @staticmethod
    def leg_endpoint(
        leg: Segment,
        next_stop: Segment | None = None,
    ) -> float:
        """
        Furthest price reached by a leg (Point B / D).

        Uses the full stop extreme so that all candles in the
        following stop contribute to the leg endpoint.
        """
        if leg.direction == Direction.BULL:
            stop_ext = next_stop.high if next_stop else 0.0
            return max(leg.high, stop_ext)
        stop_ext = next_stop.low if next_stop else float("inf")
        return min(leg.low, stop_ext)

    @staticmethod
    def stop_breach(stop: Segment) -> float:
        """
        Breach level of a stop region.

        Uses the extreme across ALL candles in the stop — the candle
        that creates the deepest retracement defines the breach level.
        BULL structure → stops retrace downward → breach = stop.low.
        BEAR structure → stops retrace upward   → breach = stop.high.
        """
        if stop.direction == Direction.BULL:
            return stop.low
        return stop.high


# ---------------------------------------------------------------------------
# Leg counting
# ---------------------------------------------------------------------------

class LegCounter:
    """Count geometrically confirmed legs in an ordered segment list."""

    @staticmethod
    def count(segments: list[Segment]) -> int:
        """
        Return the number of confirmed legs in *segments*.

        Segments are expected in chronological order, alternating
        LEG / STOP / LEG / STOP / …

        Confirmation rules
        ------------------
        * Leg 1 is always confirmed.
        * Leg N (N >= 2): the staircase must extend — each new leg
          endpoint must surpass the previous, and the stop between
          must stay nested (not breach the prior leg's start).
          Nesting uses the full stop extreme (Segment.high / .low).
        """
        legs: list[Segment] = []
        stops: list[Segment] = []
        for seg in segments:
            if seg.kind == SegmentKind.LEG:
                legs.append(seg)
            else:
                stops.append(seg)

        if not legs:
            return 0

        confirmed = 1
        direction = legs[0].direction

        for i in range(1, len(legs)):
            if i - 1 >= len(stops):
                break

            prev_leg = legs[i - 1]
            curr_leg = legs[i]
            between_stop = stops[i - 1]
            next_stop = stops[i] if i < len(stops) else None

            prev_end = StructurePoints.leg_endpoint(prev_leg, between_stop)
            curr_end = StructurePoints.leg_endpoint(curr_leg, next_stop)
            prev_leg_prev_stop = stops[i - 2] if i >= 2 else None
            prev_start = StructurePoints.leg_start(prev_leg, prev_leg_prev_stop)

            if direction == Direction.BULL:
                staircase = curr_end > prev_end
                nested = between_stop.low >= prev_start
            elif direction == Direction.BEAR:
                staircase = curr_end < prev_end
                nested = between_stop.high <= prev_start
            else:
                break

            print(f"  [LegCounter] i={i} curr_end={curr_end:.0f} prev_end={prev_end:.0f} "
                  f"staircase={staircase} nested={nested} "
                  f"stop_high={between_stop.high:.0f} stop_low={between_stop.low:.0f} "
                  f"prev_start={prev_start:.0f}")

            if staircase and nested:
                confirmed += 1
            else:
                break

        return confirmed


# ---------------------------------------------------------------------------
# Break detection
# ---------------------------------------------------------------------------

class BreakDetector:
    """Detect structural breaks in a leg/stop sequence."""

    @staticmethod
    def is_single_leg_reverse(
        leg: Segment,
        stop: Segment,
        prev_stop: Segment | None = None,
    ) -> bool:
        """The stop fully reverses the leg — price breaches the true origin.

        The reference point is the leg start OR the prior stop's extreme,
        whichever is more favourable to the original direction (lower low
        for BULL, higher high for BEAR).  Same-direction S/L/D/J candles
        in the prior stop can define a lower origin than the leg itself.
        """
        start = StructurePoints.leg_start(leg)
        if leg.direction == Direction.BULL:
            ref = min(prev_stop.low, start) if prev_stop else start
            return stop.low < ref
        if leg.direction == Direction.BEAR:
            ref = max(prev_stop.high, start) if prev_stop else start
            return stop.high > ref
        return False

    @staticmethod
    def is_geometric_break(stops: list[Segment]) -> bool:
        """
        Current stop breach exceeds previous stop breach.

        Uses the full segment extreme (deepest retracement defines breach).
        Bull: current stop.low < previous stop.low.
        Bear: current stop.high > previous stop.high.
        """
        if len(stops) < 2:
            return False

        prev = stops[-2]
        curr = stops[-1]
        direction = curr.direction

        prev_breach = StructurePoints.stop_breach(prev)
        curr_breach = StructurePoints.stop_breach(curr)

        if direction == Direction.BULL:
            return curr_breach < prev_breach
        if direction == Direction.BEAR:
            return curr_breach > prev_breach
        return False
