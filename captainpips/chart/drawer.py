"""
ChartDrawer — sends DRAW commands to the MT5 EA via ZMQ PUSH socket.

Receives structure events from the bot and translates them into
chart-object commands the EA renders on the MetaTrader 5 chart.
Completely independent of strategies and position management.

Time fields use Candle.raw_time (Unix epoch seconds). The EA casts
these directly to datetime: datetime t1 = (datetime)time1_value;
"""

from __future__ import annotations

import logging

from captainpips.core.definitions import Candle, Direction, Segment

log = logging.getLogger(__name__)


class ChartDrawer:
    """Translates structural events into MT5 chart-drawing commands."""

    def __init__(self, zmq_connector) -> None:
        self._zmq = zmq_connector
        self._objects: list[str] = []

    def on_leg_confirmed(self, leg: Segment, number: int, symbol: str) -> None:
        """Draw a rectangle spanning the full leg candle range."""
        log.info(f"on_leg_confirmed called: leg={number} symbol={symbol}")
        name = f"CP_LEG_{number}_{symbol}"
        color = "#00FF00" if leg.direction == Direction.BULL else "#FF0000"
        self._send_draw({
            "type": "rectangle",
            "name": name,
            "symbol": symbol,
            "time1": leg.first_candle.raw_time,
            "time2": leg.last_candle.raw_time + 60,
            "price1": leg.high,
            "price2": leg.low,
            "color": color,
            "label": f"Leg {number}",
        })
        self._objects.append(name)

    def on_stop_started(self, stop: Segment, number: int, symbol: str) -> None:
        """Draw a rectangle spanning the stop's candles so far."""
        name = f"CP_STOP_{number}_{symbol}"
        self._send_draw({
            "type": "rectangle",
            "name": name,
            "symbol": symbol,
            "time1": stop.first_candle.raw_time,
            "time2": stop.last_candle.raw_time + 60,
            "price1": stop.high,
            "price2": stop.low,
            "color": "#808080",
            "label": f"Stop {number}",
        })
        self._objects.append(name)

    def on_stop_candle(self, stop: Segment, number: int, symbol: str) -> None:
        """Update stop rectangle's time2 as a new candle is added to the stop."""
        name = f"CP_STOP_{number}_{symbol}"
        self._send_draw({
            "type": "update_rectangle",
            "name": name,
            "symbol": symbol,
            "time2": stop.last_candle.raw_time + 60,
            "price1": stop.high,
            "price2": stop.low,
        })
        log.debug(f"on_stop_candle: updated {name} time2={stop.last_candle.raw_time + 60}")

    def on_structure_reset(self, symbol: str) -> None:
        """Delete all CP_ objects from the chart and clear the local tracking list."""
        self._send_draw({
            "type": "delete",
            "prefix": "CP_",
        })
        self._objects.clear()

    def on_double_detected(self, candle: Candle, symbol: str) -> None:
        """Draw an arrow at the candle marking a double pattern."""
        name = f"CP_DBL_{candle.raw_time}"
        self._send_draw({
            "type": "arrow",
            "name": name,
            "symbol": symbol,
            "time": candle.raw_time,
            "price": candle.low,
            "color": "#FFD700",
            "label": "Double",
        })
        self._objects.append(name)

    def on_fail_zone(self, builder, symbol: str) -> None:
        """Draw fail zone — the price level that invalidates the structure."""
        from captainpips.core.structure_rules import StructurePoints
        from captainpips.core.definitions import SegmentKind, Direction

        segs = builder.segments
        if not segs:
            return

        legs = [s for s in segs if s.kind == SegmentKind.LEG]
        stops = [s for s in segs if s.kind == SegmentKind.STOP]
        direction = builder.direction

        if not legs:
            return

        if len(legs) == 1:
            # Single leg: fail zone = leg_start (Point A)
            fail_price = StructurePoints.leg_start(legs[0])
        else:
            # Multi-leg: fail zone = start of the LATEST confirmed leg,
            # folding in the previous stop's extreme.
            # This gives the true invalidation level — if the current correction
            # retraces past where the latest leg began, the structure has failed.
            # legs[-1] = the most recent confirmed leg
            # stops[-2] = the last COMPLETED stop (stops[-1] is the current forming one)
            prev_stop = stops[-2] if len(stops) >= 2 else (stops[-1] if stops else None)
            fail_price = StructurePoints.leg_start(legs[-1], prev_stop)

        log.info(f"on_fail_zone: symbol={symbol} "
                 f"legs={len(legs)} stops={len(stops)} direction={direction} "
                 f"fail_price={fail_price}")

        # Get time range: from first candle to now
        first_time = segs[0].candles[0].raw_time
        last_time = segs[-1].candles[-1].raw_time + 60

        # NOTE: Do NOT send a "delete" command here before the fail_zone command.
        # The EA's delete handler is a FULL structure reset (resets CP_ENTRY_active,
        # CP_COUNT_LEG, etc.) which wipes the entry zone from the chart.
        # The fail_zone command already overwrites the CP_FAILZONE_* GlobalVariables
        # directly, so no intermediate delete is needed.
        name = f"CP_FAILZONE_{symbol}"

        log.info(f"_send_draw fail_zone: price={fail_price}")
        self._send_draw({
            "type": "fail_zone",
            "name": name,
            "symbol": symbol,
            "time1": first_time,
            "time2": last_time + 7200,
            "price1": fail_price,
            "price2": fail_price,
            "price": fail_price,
            "direction": direction.value if direction else "NONE",
        })

    def on_paradox_zone(self, upper: float, lower: float, symbol: str) -> None:
        """Draw paradox zone as two yellow horizontal lines on the MT5 chart."""
        name = f"CP_PARADOX_{symbol}"
        self._send_draw({
            "type": "paradox_zone",
            "name": name,
            "symbol": symbol,
            "upper": upper,
            "lower": lower,
        })
        log.info(
            "on_paradox_zone: symbol=%s upper=%s lower=%s",
            symbol, upper, lower,
        )

    def on_paradox_clear(self, symbol: str) -> None:
        """Remove paradox zone lines from the MT5 chart."""
        name = f"CP_PARADOX_{symbol}"
        self._send_draw({
            "type": "clear_paradox_zone",
            "name": name,
            "symbol": symbol,
        })
        log.info("on_paradox_clear: symbol=%s", symbol)

    def _send_draw(self, command: dict) -> None:
        """Dispatch a draw command dict; the connector wraps it in the DRAW envelope."""
        log.info(f"_send_draw called: {command.get('type')} {command.get('name','')}")
        try:
            self._zmq.send_draw(command)
        except Exception as exc:
            log.error("ChartDrawer send failed: %s", exc)
