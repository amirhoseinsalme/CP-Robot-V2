"""
Leg3Drive strategy — Mode A and Mode B entry logic.

Activates after Leg 2 is confirmed, D > B is verified, and Stop 2
shows a correction (same correction rule as Leg2Chain).

Mode A / Mode B logic is identical to Leg2Chain — only the trigger
conditions (confirmed_legs == 2, D > B check, leg_number in Signal)
and the strategy name differ.
"""

from __future__ import annotations

from captainpips.core.definitions import Candle, Direction, Segment, SegmentKind, Signal
from captainpips.core.structure_rules import StructurePoints
from captainpips.core.structure_state import EventKind, StructureEvent
from captainpips.core.strategies.base import BaseStrategy, EntryWatch


class Leg3Drive(BaseStrategy):

    @property
    def name(self) -> str:
        return "Leg3Drive"

    # -----------------------------------------------------------------------
    # BaseStrategy interface
    # -----------------------------------------------------------------------

    def on_structure_event(
        self,
        event: StructureEvent,
        segments: list[Segment],
        confirmed_legs: int,
        candle: Candle,
    ) -> EntryWatch | None:
        if confirmed_legs != 2:
            return None
        if event.kind != EventKind.STOP_STARTED:
            return None

        direction = _leg_direction(segments)
        if direction is None:
            return None

        if not _d_beyond_b(segments, direction):
            return None

        return EntryWatch(
            strategy=self.name,
            leg_number=3,
            mode="WAIT_CORRECTION",
            trigger_price=0.0,
            trigger_direction=direction,
            pending_bo_index=None,
        )

    def on_candle(
        self,
        candle: Candle,
        watch: EntryWatch,
        segments: list[Segment],
    ) -> Signal | None:
        direction = watch.trigger_direction
        prev_candle = watch.prev_candle

        result: Signal | None = None

        if watch.mode == "WAIT_CORRECTION":
            result = self._handle_wait_correction(watch, segments, direction)
        elif watch.mode in ("MONITOR", "MONITOR_BEAR"):
            result = self._handle_monitor(candle, watch, direction, prev_candle)
        elif watch.mode == "B":
            result = self._handle_mode_b(candle, watch, direction, prev_candle)
        elif watch.mode == "B_POST_BO":
            result = self._handle_mode_b_post_bo(candle, watch, direction)

        watch.prev_candle = candle
        return result

    # -----------------------------------------------------------------------
    # State handlers (identical logic to Leg2Chain, stop 2 scoped)
    # -----------------------------------------------------------------------

    def _handle_wait_correction(
        self,
        watch: EntryWatch,
        segments: list[Segment],
        direction: Direction,
    ) -> Signal | None:
        """Wait until Stop 2 develops a correction, then open the monitoring window."""
        stops = [s for s in segments if s.kind == SegmentKind.STOP]
        if len(stops) >= 2 and self._has_correction(stops[1], direction):
            watch.mode = "MONITOR"
            watch.pending_bo_index = 0
        return None

    def _handle_monitor(
        self,
        candle: Candle,
        watch: EntryWatch,
        direction: Direction,
        prev_candle: Candle | None,
    ) -> Signal | None:
        """
        2-candle monitoring window after the correction BO.

        Decision table
        ──────────────────────────────────────────────────────────
        Case A  BO + no bear flag       → Mode A entry (immediate)
        Case B  BO + bear flag was set  → arm Mode B, wait for FT
        Case C  opposite-close, no BO   → set bear flag, continue
        Case D  both on same candle     → bear takes priority → arm Mode B
        Case E  window exhausts, no BO  → if flag: transition to B
                                          else: DONE, no entry
        """
        watch.pending_bo_index = (watch.pending_bo_index or 0) + 1

        is_bo = self._is_leg_direction_bo(candle, prev_candle, direction)
        is_opp = self._is_opposite_by_close(candle, direction)
        bear_seen = watch.mode == "MONITOR_BEAR"

        if is_opp:
            bear_seen = True
            watch.mode = "MONITOR_BEAR"

        if is_bo:
            if not bear_seen:
                # Case A — Mode A entry
                watch.mode = "DONE"
                return Signal(
                    symbol="",
                    direction=direction,
                    entry_price=candle.close,
                    reason="Leg3Drive Mode A: BO in leg direction",
                    strategy=self.name,
                    leg_number=watch.leg_number,
                )
            # Case B / D — Mode B armed with this BO
            watch.mode = "B_POST_BO"
            watch.pending_bo_index = 0
            return None

        if watch.pending_bo_index >= 2:
            if bear_seen:
                watch.mode = "B"
                watch.pending_bo_index = None
            else:
                watch.mode = "DONE"

        return None

    def _handle_mode_b(
        self,
        candle: Candle,
        watch: EntryWatch,
        direction: Direction,
        prev_candle: Candle | None,
    ) -> Signal | None:
        """Mode B: waiting indefinitely for a leg-direction BO."""
        if self._is_leg_direction_bo(candle, prev_candle, direction):
            watch.mode = "B_POST_BO"
            watch.pending_bo_index = 0
        return None

    def _handle_mode_b_post_bo(
        self,
        candle: Candle,
        watch: EntryWatch,
        direction: Direction,
    ) -> Signal | None:
        """
        2-candle FT window after the Mode B BO.

        FT = candle closes in leg direction (close vs open only).
        No FT within 2 candles → back to Mode B, wait for the next BO
        (Mode B waits indefinitely; a failed FT window doesn't cancel it).
        """
        watch.pending_bo_index = (watch.pending_bo_index or 0) + 1

        if self._is_ft_by_close(candle, direction):
            watch.mode = "DONE"
            return Signal(
                symbol="",
                direction=direction,
                entry_price=candle.close,
                reason="Leg3Drive Mode B: FT after BO",
                strategy=self.name,
                leg_number=watch.leg_number,
            )

        if watch.pending_bo_index >= 2:
            watch.mode = "B"
            watch.pending_bo_index = None

        return None

    # -----------------------------------------------------------------------
    # Private helpers
    # -----------------------------------------------------------------------

    @staticmethod
    def _has_correction(stop: Segment, leg_direction: Direction) -> bool:
        """
        A correction exists when the stop contains a BO in the stop direction:
        BULL leg (bearish stop): any candle closes below the previous candle's low.
        BEAR leg (bullish stop): any candle closes above the previous candle's high.
        """
        candles = stop.candles
        if len(candles) < 2:
            return False
        for i in range(1, len(candles)):
            if leg_direction == Direction.BULL:
                if candles[i].close < candles[i - 1].low:
                    return True
            elif leg_direction == Direction.BEAR:
                if candles[i].close > candles[i - 1].high:
                    return True
        return False

    @staticmethod
    def _is_leg_direction_bo(candle: Candle, prev_candle: Candle | None, direction: Direction) -> bool:
        """BO in leg direction — close breaks beyond the previous candle's extreme."""
        if prev_candle is None:
            return False
        if direction == Direction.BULL:
            return candle.close > prev_candle.high
        if direction == Direction.BEAR:
            return candle.close < prev_candle.low
        return False

    @staticmethod
    def _is_opposite_by_close(candle: Candle, direction: Direction) -> bool:
        if direction == Direction.BULL:
            return candle.close < candle.open
        if direction == Direction.BEAR:
            return candle.close > candle.open
        return False

    @staticmethod
    def _is_ft_by_close(candle: Candle, direction: Direction) -> bool:
        if direction == Direction.BULL:
            return candle.close > candle.open
        if direction == Direction.BEAR:
            return candle.close < candle.open
        return False


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _leg_direction(segments: list[Segment]) -> Direction | None:
    """Return the direction of the first LEG segment, or None."""
    for seg in segments:
        if seg.kind == SegmentKind.LEG:
            return seg.direction
    return None


def _d_beyond_b(segments: list[Segment], direction: Direction) -> bool:
    """
    Verify D > B — the second leg endpoint is beyond the first.

    Extracts legs and their adjacent stops, then compares endpoints
    using StructurePoints so the shared-candle rule is applied
    consistently with LegCounter.
    """
    legs = [s for s in segments if s.kind == SegmentKind.LEG]
    stops = [s for s in segments if s.kind == SegmentKind.STOP]

    if len(legs) < 2 or len(stops) < 1:
        return False

    leg1, leg2 = legs[0], legs[1]
    stop1 = stops[0]
    stop2 = stops[1] if len(stops) >= 2 else None

    point_b = StructurePoints.leg_endpoint(leg1, stop1)
    point_d = StructurePoints.leg_endpoint(leg2, stop2)

    if direction == Direction.BULL:
        return point_d > point_b
    if direction == Direction.BEAR:
        return point_d < point_b
    return False
