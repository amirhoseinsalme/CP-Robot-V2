"""
Legs Shift scoring system — 9 elements (a through i).

Evaluates whether a breaking stop represents a genuine structural shift
by checking nine independent conditions. A shift is confirmed when at
least 6 of the applicable elements are True.

Imports only from definitions.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from captainpips.core.definitions import (
    ALWAYS_STOP_LABELS,
    Candle,
    CandleLabel,
    Direction,
    Segment,
    SegmentKind,
)

# Labels that are ignored when scanning for Double patterns
log = logging.getLogger(__name__)

_SKIP_LABELS = {CandleLabel.S, CandleLabel.L, CandleLabel.D, CandleLabel.J}


# ---------------------------------------------------------------------------
# Score result
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ShiftScore:
    """Result of the 9-element shift evaluation."""

    a_outbreak: bool
    b_double: bool
    c_structure_break: bool
    d_ema60: bool
    e_followthrough: bool
    f_closegap: bool
    g_size: bool
    h_gapfill: bool
    i_3cgap: bool
    c_applicable: bool = True  # False when only 1 leg exists

    @property
    def true_count(self) -> int:
        return sum([
            self.a_outbreak,
            self.b_double,
            self.c_structure_break,
            self.d_ema60,
            self.e_followthrough,
            self.f_closegap,
            self.g_size,
            self.h_gapfill,
            self.i_3cgap,
        ])

    @property
    def applicable_count(self) -> int:
        """9 normally, 8 when c is N/A (single-leg structure)."""
        return 9 if self.c_applicable else 8

    @property
    def confirmed(self) -> bool:
        return self.true_count >= 6


# ---------------------------------------------------------------------------
# Scorer
# ---------------------------------------------------------------------------

class ShiftScorer:
    """Evaluates the 9 shift elements for a breaking stop."""

    def score(
        self,
        breaking_stop: Segment,
        segments: list[Segment],
        ema_values: list[float],
        all_candles: list[Candle],
    ) -> ShiftScore:
        """
        Score a breaking move against all 9 shift elements.

        Parameters
        ----------
        breaking_stop:  The stop segment that broke the structure.
        segments:       All confirmed segments up to and including the break.
        ema_values:     EMA-60 value per candle, aligned to *all_candles* by index.
        all_candles:    Full candle history (needed for gap/double scanning).
        """
        # Breaking stop moves OPPOSITE to structure direction
        direction = Direction.BULL if breaking_stop.direction == Direction.BEAR else Direction.BEAR
        legs = [s for s in segments if s.kind == SegmentKind.LEG]
        stops = [s for s in segments if s.kind == SegmentKind.STOP]

        single_leg = len(legs) <= 1
        prev_stops = [s for s in stops if s is not breaking_stop]

        # Compute all elements
        a_outbreak = self._check_outbreak(breaking_stop, direction)
        b_double = self._check_double(breaking_stop, direction, all_candles)
        c_structure_break = self._check_structure_break(breaking_stop, segments, direction)
        d_ema60 = self._check_ema60(breaking_stop, ema_values, all_candles)
        e_followthrough = self._check_followthrough(breaking_stop, direction)
        f_closegap = self._check_closegap(breaking_stop, direction)
        g_size = self._check_size(breaking_stop, prev_stops)
        h_gapfill = self._check_gapfill(breaking_stop, legs, all_candles, direction)
        i_3cgap = self._check_3cgap(all_candles, direction)

        log.debug(
            "Shift elements: a=%s b=%s c=%s d=%s e=%s f=%s g=%s h=%s i=%s",
            a_outbreak, b_double, c_structure_break, d_ema60,
            e_followthrough, f_closegap, g_size, h_gapfill, i_3cgap,
        )

        result = ShiftScore(
            a_outbreak=a_outbreak,
            b_double=b_double,
            c_structure_break=c_structure_break,
            d_ema60=d_ema60,
            e_followthrough=e_followthrough,
            f_closegap=f_closegap,
            g_size=g_size,
            h_gapfill=h_gapfill,
            i_3cgap=i_3cgap,
            c_applicable=not single_leg,
        )
        log.debug("Shift true_count=%d", result.true_count)
        return result

    # -----------------------------------------------------------------------
    # Element checkers
    # -----------------------------------------------------------------------

    @staticmethod
    def _check_outbreak(stop: Segment, direction: Direction) -> bool:
        """a — BO in stop direction within the breaking move."""
        candles = stop.candles
        if len(candles) < 2:
            return False
        for i in range(1, len(candles)):
            prev_c, curr_c = candles[i - 1], candles[i]
            if direction == Direction.BULL:
                if curr_c.high > prev_c.high:
                    return True
            elif direction == Direction.BEAR:
                if curr_c.low < prev_c.low:
                    return True
        return False

    @staticmethod
    def _check_double(
        stop: Segment,
        direction: Direction,
        all_candles: list[Candle],
    ) -> bool:
        """
        b — Double Top / Double Bottom within the breaking stop time range.

        Uses all_candles in the stop's time range (includes candidates
        that aren't part of the stop segment itself).

        BULL structure (last leg UP, break goes DOWN):
          Double Top pattern: BEAR -> BULL -> BEAR
        BEAR structure (last leg DOWN, break goes UP):
          Double Bottom pattern: BULL -> BEAR -> BULL
        """
        stop_start = stop.candles[0].raw_time
        stop_end = stop.candles[-1].raw_time

        filtered: list[Direction] = []
        filtered_candles: list[Candle] = []
        for c in all_candles:
            if c.raw_time < stop_start or c.raw_time > stop_end:
                continue
            if c.label in _SKIP_LABELS:
                continue
            bias = c.structure_direction
            filtered.append(bias)
            filtered_candles.append(c)

        log.debug("Shift.b dir=%s stop_range_candles=%d", direction.value, len(filtered))

        if len(filtered) < 3:
            return False

        if direction == Direction.BULL:
            need = [Direction.BEAR, Direction.BULL, Direction.BEAR]
        elif direction == Direction.BEAR:
            need = [Direction.BULL, Direction.BEAR, Direction.BULL]
        else:
            return False

        stage = 0
        for d, c in zip(filtered, filtered_candles):
            if d == need[stage]:
                stage += 1
                if stage == 3:
                    return True

        return False

    @staticmethod
    def _check_structure_break(
        stop: Segment,
        segments: list[Segment],
        direction: Direction,
    ) -> bool:
        """c — Breaking stop breached previous structure."""
        prev_stops = [
            s for s in segments
            if s.kind == SegmentKind.STOP and s is not stop
        ]
        if not prev_stops:
            return False

        prev = prev_stops[-1]
        if direction == Direction.BULL:
            return stop.high > prev.high
        if direction == Direction.BEAR:
            return stop.low < prev.low
        return False

    @staticmethod
    def _check_ema60(
        stop: Segment,
        ema_values: list[float],
        all_candles: list[Candle],
    ) -> bool:
        """d — Price crossed EMA-60 during the breaking stop (close-based)."""
        if not ema_values:
            return False

        ema_by_index: dict[int, float] = {}
        for candle, ema in zip(all_candles, ema_values):
            ema_by_index[candle.index] = ema

        crossed = False
        prev_side: bool | None = None  # True = above, False = below

        for c in stop.candles:
            ema = ema_by_index.get(c.index)
            if ema is None:
                continue
            above = c.close > ema
            if prev_side is not None and above != prev_side:
                crossed = True
                break
            prev_side = above

        return crossed

    @staticmethod
    def _check_followthrough(
        stop: Segment,
        direction: Direction,
    ) -> bool:
        """e — After the first BO candle in the stop, a same-direction candle follows."""
        if len(stop.candles) < 2:
            return False

        bo_idx = None
        for i in range(1, len(stop.candles)):
            prev_c, curr_c = stop.candles[i - 1], stop.candles[i]
            if direction == Direction.BULL and curr_c.high > prev_c.high:
                bo_idx = i
                break
            elif direction == Direction.BEAR and curr_c.low < prev_c.low:
                bo_idx = i
                break

        if bo_idx is None:
            return False

        count = 0
        for c in stop.candles[bo_idx + 1:]:
            count += 1
            if count > 2:
                break
            if direction == Direction.BULL:
                same_dir = c.close > c.open
            else:
                same_dir = c.close < c.open
            if same_dir:
                return True
        return False

    @staticmethod
    def _check_closegap(
        stop: Segment,
        direction: Direction,
    ) -> bool:
        """f — Any candle in stop closes beyond prev candle's extreme."""
        if len(stop.candles) < 2:
            return False

        for i in range(1, len(stop.candles)):
            prev_c = stop.candles[i - 1]
            curr_c = stop.candles[i]
            if direction == Direction.BULL:
                if curr_c.close > prev_c.high:
                    return True
            elif direction == Direction.BEAR:
                if curr_c.close < prev_c.low:
                    return True

        return False

    @staticmethod
    def _check_size(stop: Segment, prev_stops: list[Segment]) -> bool:
        """g — Breaking move >= 1.5x average range of previous stops."""
        if not prev_stops:
            return False

        break_range = stop.high - stop.low
        avg = sum(s.high - s.low for s in prev_stops) / len(prev_stops)

        if avg <= 0:
            return False
        return break_range >= 1.5 * avg

    @staticmethod
    def _check_gapfill(
        stop: Segment,
        legs: list[Segment],
        all_candles: list[Candle],
        direction: Direction,
    ) -> bool:
        """h — A gap from the last leg was filled by the breaking stop."""
        if not legs:
            return False

        last_leg = legs[-1]
        leg_candles = last_leg.candles
        if len(leg_candles) < 2:
            return False

        for i in range(1, len(leg_candles)):
            prev_c, curr_c = leg_candles[i - 1], leg_candles[i]

            if direction == Direction.BEAR:
                if curr_c.low > prev_c.high:
                    if stop.low <= prev_c.high:
                        return True
            elif direction == Direction.BULL:
                if curr_c.high < prev_c.low:
                    if stop.high >= prev_c.low:
                        return True

        if len(leg_candles) >= 3:
            for i in range(len(leg_candles) - 2):
                c0, c2 = leg_candles[i], leg_candles[i + 2]
                if direction == Direction.BEAR:
                    if c2.low > c0.high:
                        if stop.low <= c0.high:
                            return True
                elif direction == Direction.BULL:
                    if c2.high < c0.low:
                        if stop.high >= c0.low:
                            return True

        return False

    @staticmethod
    def _check_3cgap(
        all_candles: list[Candle],
        direction: Direction,
    ) -> bool:
        """
        i — 3 consecutive candles where the extremes don't overlap.

        Bull: high[0] < low[2]  (price jumped up so far that candle 0's
              high is below candle 2's low — no overlap).
        Bear: low[0] > high[2]  (price dropped so far that candle 0's
              low is above candle 2's high — no overlap).

        Scans the tail of all_candles for the most recent occurrence.
        """
        if len(all_candles) < 3:
            return False

        for i in range(len(all_candles) - 2):
            c0, c2 = all_candles[i], all_candles[i + 2]
            if direction == Direction.BULL and c0.high < c2.low:
                return True
            if direction == Direction.BEAR and c0.low > c2.high:
                return True

        return False


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _candle_bias(c: Candle) -> Direction:
    """
    Directional bias for Double-pattern scanning.

    P/R: bull if lower shadow > upper shadow, bear otherwise.
    All others: bull if close > open, bear if close < open.
    """
    if c.label in (CandleLabel.P, CandleLabel.R):
        lower_shadow = min(c.open, c.close) - c.low
        upper_shadow = c.high - max(c.open, c.close)
        return Direction.BULL if lower_shadow > upper_shadow else Direction.BEAR

    if c.close > c.open:
        return Direction.BULL
    if c.close < c.open:
        return Direction.BEAR
    return Direction.NONE


def _candle_before(target: Candle, all_candles: list[Candle]) -> Candle | None:
    """Return the candle immediately before *target* in the full history."""
    for i, c in enumerate(all_candles):
        if c.index == target.index and i > 0:
            return all_candles[i - 1]
    return None
