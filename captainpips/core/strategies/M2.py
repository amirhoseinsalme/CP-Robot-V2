"""
M2 — stop-2 touch strategy on an active (non-broken) structure.

Arms while stop 2 is forming (after exactly 2 confirmed legs), once BOTH
conditions are simultaneously true:
  1. stop2.n_candles  >=  0.5 * stop1.n_candles
  2. (stop2.high - stop2.low)  >=  0.5 * (stop1.high - stop1.low)

The key level is FROZEN at the first moment conditions are met and does
NOT update as stop 2 continues to form.

Entry (real-time touch within a candle — not candle close):
  Bull structure → BUY  when candle.high >= key_level  (= stop2.high at arm)
  Bear structure → SELL when candle.low  <= key_level  (= stop2.low  at arm)

SL:
  Bull: stop1.low
  Bear: stop1.high
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from captainpips.core.definitions import (
    Candle,
    Direction,
    Segment,
    SegmentKind,
    Signal,
)
from captainpips.core.structure_state import EventKind

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Watch object  —  carries all mutable state for one watch cycle
# ---------------------------------------------------------------------------

@dataclass
class M2Watch:
    """
    Carried by main.py's active_watch dict while M2 is monitoring stop 2.
    Compatible with the BaseStrategy / active_watch interface.
    """
    mode:           str            # "WATCHING" | "DONE" | "CANCELLED"
    direction:      Direction      # structure direction (BULL → buy at top; BEAR → sell at bottom)
    sl_level:       float          # stop1.low (bull) or stop1.high (bear)
    stop1_size:     float          # stop1.high - stop1.low
    stop1_candles:  int            # stop1.n_candles
    key_level:      float | None = None   # frozen when conditions first met
    conditions_met: bool          = False


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------

class M2Strategy:
    """
    M2 — enter at the live stop-2 boundary while the structure is intact.

    Implements the same on_structure_event / on_candle interface as
    BaseStrategy subclasses so it slots directly into active_watch.
    """

    name = "M2"

    # ------------------------------------------------------------------
    # Hook 1: arm when stop 2 starts
    # ------------------------------------------------------------------

    def on_structure_event(
        self,
        event,
        segments: list[Segment],
        confirmed_legs: int,
        candle: Candle,
    ) -> M2Watch | None:
        """Return an M2Watch when stop 2 just started (STOP_STARTED, confirmed_legs == 2)."""
        if event.kind != EventKind.STOP_STARTED or confirmed_legs != 2:
            return None

        legs  = [s for s in segments if s.kind == SegmentKind.LEG]
        stops = [s for s in segments if s.kind == SegmentKind.STOP]

        if len(legs) < 2 or len(stops) < 2:
            return None

        stop1     = stops[0]
        direction = legs[-1].direction   # BULL if structure going up

        sl_level      = stop1.low  if direction == Direction.BULL else stop1.high
        stop1_size    = stop1.high - stop1.low
        stop1_candles = stop1.n_candles

        log.info(
            "M2 armed: direction=%s  stop1=(H=%.5f L=%.5f n=%d)  sl=%.5f",
            direction.value, stop1.high, stop1.low, stop1_candles, sl_level,
        )
        return M2Watch(
            mode          = "WATCHING",
            direction     = direction,
            sl_level      = sl_level,
            stop1_size    = stop1_size,
            stop1_candles = stop1_candles,
        )

    # ------------------------------------------------------------------
    # Hook 2: per-candle — check conditions, then check entry
    # ------------------------------------------------------------------

    def on_candle(
        self,
        candle: Candle,
        watch: M2Watch,
        segments: list[Segment],
    ) -> Signal | None:
        if watch.mode != "WATCHING":
            return None

        legs  = [s for s in segments if s.kind == SegmentKind.LEG]
        stops = [s for s in segments if s.kind == SegmentKind.STOP]

        # Cancel if structure has moved beyond stop 2
        if len(legs) != 2 or len(stops) < 2:
            log.info("M2: structure advanced past stop-2 — cancelling watch")
            watch.mode = "CANCELLED"
            return None

        stop2 = stops[-1]   # live stop 2

        # ── Check conditions; freeze key_level on first arm ───────────
        if not watch.conditions_met:
            size_ok    = (stop2.high - stop2.low) >= 0.5 * watch.stop1_size
            candles_ok = stop2.n_candles >= 0.5 * watch.stop1_candles

            if size_ok and candles_ok:
                watch.conditions_met = True
                watch.key_level = (
                    stop2.high if watch.direction == Direction.BULL
                    else stop2.low
                )
                log.info(
                    "M2 conditions met: direction=%s  key_level=%.5f  "
                    "(stop2 size=%.5f n=%d  vs  stop1 size=%.5f n=%d)",
                    watch.direction.value, watch.key_level,
                    stop2.high - stop2.low, stop2.n_candles,
                    watch.stop1_size, watch.stop1_candles,
                )

        # ── Entry check (only when conditions have been met) ─────────
        if not watch.conditions_met or watch.key_level is None:
            return None

        if watch.direction == Direction.BULL and candle.high >= watch.key_level:
            watch.mode = "DONE"
            log.info(
                "M2 BUY: candle.high=%.5f >= key=%.5f  sl=%.5f",
                candle.high, watch.key_level, watch.sl_level,
            )
            return Signal(
                symbol      = "",
                direction   = Direction.BULL,
                entry_price = watch.key_level,
                reason      = (
                    f"M2: candle.high {candle.high:.5f} >= stop2.high "
                    f"{watch.key_level:.5f} (bull structure touch)"
                ),
                strategy    = "M2",
                leg_number  = 2,
                sl_override = watch.sl_level,
            )

        if watch.direction == Direction.BEAR and candle.low <= watch.key_level:
            watch.mode = "DONE"
            log.info(
                "M2 SELL: candle.low=%.5f <= key=%.5f  sl=%.5f",
                candle.low, watch.key_level, watch.sl_level,
            )
            return Signal(
                symbol      = "",
                direction   = Direction.BEAR,
                entry_price = watch.key_level,
                reason      = (
                    f"M2: candle.low {candle.low:.5f} <= stop2.low "
                    f"{watch.key_level:.5f} (bear structure touch)"
                ),
                strategy    = "M2",
                leg_number  = 2,
                sl_override = watch.sl_level,
            )

        return None
