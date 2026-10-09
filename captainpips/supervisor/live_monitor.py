"""
Live monitor — records robot events into Supervisor memory.

Subscribes to the robot's monitor WebSocket and persists the events that
matter for later review. High-frequency traffic (candle, watch_update,
account) is deliberately dropped so the record files stay readable.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading

import websockets

from captainpips.supervisor.memory import SupervisorMemory

log = logging.getLogger(__name__)

DEFAULT_WS_URL = "ws://localhost:8000/ws/monitor"
RECONNECT_S = 3

# Event types worth keeping. Everything else is per-candle noise.
_OBSERVED = ("signal", "shift_score", "trade", "trade_closed", "trade_opened")


class LiveMonitor:
    """Background WebSocket listener that feeds SupervisorMemory."""

    def __init__(
        self,
        memory: SupervisorMemory,
        ws_url: str = DEFAULT_WS_URL,
    ) -> None:
        self.memory = memory
        self.ws_url = ws_url
        self._running = False
        self._connected = False
        self._events = 0
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Listen until stop() is called, reconnecting on dropout."""
        self._running = True
        log.info("live monitor connecting to %s", self.ws_url)

        while self._running:
            try:
                async with websockets.connect(self.ws_url) as ws:
                    self._connected = True
                    log.info("live monitor connected")
                    async for raw in ws:
                        if not self._running:
                            break
                        self._handle(raw)
            except asyncio.CancelledError:
                self._connected = False
                raise
            except Exception as exc:
                log.warning("live monitor disconnected: %s", exc)
            finally:
                self._connected = False

            if self._running:
                await asyncio.sleep(RECONNECT_S)

        log.info("live monitor stopped")

    def start_background(self) -> threading.Thread:
        """Run start() on its own event loop in a daemon thread."""
        if self._thread and self._thread.is_alive():
            log.info("live monitor already running")
            return self._thread

        self._thread = threading.Thread(
            target=self._run_loop, name="supervisor-monitor", daemon=True,
        )
        self._thread.start()
        return self._thread

    def stop(self) -> None:
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def event_count(self) -> int:
        """Events persisted since this monitor started."""
        return self._events

    def _run_loop(self) -> None:
        asyncio.run(self.start())

    # ------------------------------------------------------------------
    # Event handling
    # ------------------------------------------------------------------

    def _handle(self, raw: str | bytes) -> None:
        try:
            message = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            log.debug("live monitor: non-JSON frame ignored")
            return

        if not isinstance(message, dict):
            return

        kind = str(message.get("type") or "").lower()

        if kind in ("trade", "trade_closed"):
            self.memory.save_trade(message)
            self._events += 1
            log.debug("live monitor: trade recorded")
            return

        if kind == "structure" and message.get("event") == "STRUCTURE_BROKEN":
            self.memory.save_observation(message)
            self._events += 1
            log.debug("live monitor: structure break recorded")
            return

        if kind in _OBSERVED:
            self.memory.save_observation(message)
            self._events += 1
            log.debug("live monitor: %s recorded", kind)
