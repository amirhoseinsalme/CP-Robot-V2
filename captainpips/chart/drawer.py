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

    def _send_draw(self, command: dict) -> None:
        """Dispatch a draw command dict; the connector wraps it in the DRAW envelope."""
        log.info(f"_send_draw called: {command.get('type')} {command.get('name','')}")
        try:
            self._zmq.send_draw(command)
        except Exception as exc:
            log.error("ChartDrawer send failed: %s", exc)
