"""
WebSocket connection manager for real-time panel updates.

Routes all bot events (candles, structure, signals, positions, account)
through connected WebSocket clients. Dead connections are silently pruned
on each broadcast.
"""

from __future__ import annotations

import logging

from fastapi import WebSocket

log = logging.getLogger(__name__)


class ConnectionManager:
    """Manages active WebSocket connections and broadcasts to all."""

    def __init__(self) -> None:
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        """Accept a connection and add it to the active list."""
        await ws.accept()
        self.active.append(ws)
        log.debug("WebSocket connected (%d active)", len(self.active))

    def disconnect(self, ws: WebSocket) -> None:
        """Remove a connection from the active list."""
        if ws in self.active:
            self.active.remove(ws)
        log.debug("WebSocket disconnected (%d active)", len(self.active))

    async def broadcast(self, data: dict) -> None:
        """
        Broadcast a message to all connected clients.

        Dead connections are removed silently. The data dict should
        have a "type" field identifying the message kind.
        """
        dead: list[WebSocket] = []

        for ws in list(self.active):
            try:
                await ws.send_json(data)
            except Exception as exc:
                log.debug("Send error on WS: %s", exc)
                dead.append(ws)

        for ws in dead:
            self.disconnect(ws)

    def count(self) -> int:
        """Number of currently connected clients."""
        return len(self.active)
