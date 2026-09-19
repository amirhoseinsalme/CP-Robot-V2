"""
ZeroMQ connector for MT5 EA communication.

Socket roles
------------
PUSH  → port 5555  Send orders / requests to the EA
PULL  → port 5556  Receive direct replies from the EA (dispatcher thread)
SUB   → port 5557  Receive live-stream messages (candles, account, positions)

The dispatcher thread reads from PULL and routes each message to the
waiting caller via a per-request queue keyed by req_id.

One known EA quirk: HISTORY_REPLY arrives on the PUB/SUB channel
instead of PULL. Callers must call route_external_reply() when they
see a HISTORY_REPLY on the SUB socket so it lands in the correct queue.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from collections import defaultdict
from queue import Empty, Queue
from typing import Callable

import zmq

from captainpips.config import ZmqConfig

log = logging.getLogger(__name__)

_SENTINEL = object()


class ZmqConnector:
    """Manages the three ZMQ sockets used to talk to the MT5 EA."""

    def __init__(self, config: ZmqConfig) -> None:
        self._cfg = config
        self._ctx = zmq.Context()

        # PUSH — fire-and-forget to EA
        self._push = self._ctx.socket(zmq.PUSH)
        self._push.setsockopt(zmq.SNDTIMEO, config.send_timeout_ms)
        self._push.connect(f"tcp://127.0.0.1:{config.push_port}")

        # PULL — direct EA replies
        self._pull = self._ctx.socket(zmq.PULL)
        self._pull.setsockopt(zmq.RCVTIMEO, 200)  # short poll; dispatcher loops
        self._pull.connect(f"tcp://127.0.0.1:{config.pull_port}")

        # SUB — live broadcast from EA
        self._sub = self._ctx.socket(zmq.SUB)
        self._sub.setsockopt(zmq.RCVTIMEO, 5000)  # 5 second timeout for debugging
        self._sub.setsockopt_string(zmq.SUBSCRIBE, "")
        self._sub.connect(f"tcp://127.0.0.1:{config.sub_port}")
        log.info("ZMQ SUB subscribed to all topics on tcp://127.0.0.1:%d", config.sub_port)

        # Thread-safety
        self._push_lock = threading.Lock()
        self._pull_lock = threading.Lock()

        # req_id → Queue for waiting callers
        self._reply_queues: dict[str, Queue] = defaultdict(Queue)
        self._reply_lock = threading.Lock()

        # Dispatcher thread
        self._running = False
        self._dispatcher = threading.Thread(
            target=self._dispatch_loop, daemon=True, name="zmq-dispatcher"
        )
        self._running = True
        self._dispatcher.start()

        log.info("ZMQ PUSH connected to port %d", config.push_port)
        log.info("ZMQ PULL connected to port %d", config.pull_port)
        log.info("ZMQ SUB connected to port %d", config.sub_port)

    # -----------------------------------------------------------------------
    # Order / request sending
    # -----------------------------------------------------------------------

    def send_order(self, action: str, **kwargs) -> dict | None:
        """
        Send a request to the EA via PUSH and wait for the reply on PULL.

        Returns the reply dict, or None on timeout or send error.
        """
        req_id = str(uuid.uuid4())
        payload = {"action": action, "req_id": req_id, **kwargs}

        with self._reply_lock:
            q: Queue = self._reply_queues[req_id]

        with self._push_lock:
            try:
                self._push.send_string(json.dumps(payload))
            except zmq.error.Again:
                log.warning("PUSH send timeout for action=%s", action)
                self._remove_queue(req_id)
                return None

        try:
            reply = q.get(timeout=self._cfg.recv_timeout_ms / 1000.0)
        except Empty:
            log.warning("Reply timeout for action=%s req_id=%s", action, req_id)
            reply = None
        finally:
            self._remove_queue(req_id)

        return reply

    def request_historical(
        self,
        symbol: str,
        from_ts: int,
        to_ts: int,
        timeframe: int = 1,
    ) -> list[dict]:
        """
        Request historical bars from the EA.

        Waits up to history_timeout_ms for HISTORY_REPLY (which may arrive
        via SUB — see route_external_reply).
        """
        req_id = str(uuid.uuid4())
        payload = {
            "action": "HISTORY",
            "req_id": req_id,
            "symbol": symbol,
            "from_ts": from_ts,
            "to_ts": to_ts,
            "timeframe": timeframe,
        }

        with self._reply_lock:
            q: Queue = self._reply_queues[req_id]

        with self._push_lock:
            try:
                self._push.send_string(json.dumps(payload))
            except zmq.error.Again:
                log.warning("PUSH send timeout for HISTORY request")
                self._remove_queue(req_id)
                return []

        try:
            reply = q.get(timeout=self._cfg.history_timeout_ms / 1000.0)
        except Empty:
            log.warning("History timeout for symbol=%s", symbol)
            reply = None
        finally:
            self._remove_queue(req_id)

        if not reply:
            return []
        return reply.get("bars", [])

    def send_draw(self, command: dict) -> None:
        """Send a DRAW command to the EA via PUSH (fire-and-forget)."""
        payload = json.dumps({"action": "DRAW", "command": command})
        with self._push_lock:
            try:
                self._push.send_string(payload)
                log.debug("DRAW sent: %s %s", command.get("type"), command.get("name", ""))
            except Exception as e:
                log.error("DRAW send failed: %s", e)

    # -----------------------------------------------------------------------
    # Stream listener
    # -----------------------------------------------------------------------

    def listen_stream(
        self,
        on_candle: Callable,
        on_account: Callable,
        on_positions: Callable,
        on_history_reply: Callable,
        on_idle: Callable | None = None,
    ) -> None:
        """
        Blocking loop that reads the SUB socket and routes messages.

        Message type dispatch:
          CANDLE         → on_candle(data)
          ACCOUNT        → on_account(data)
          POSITIONS      → on_positions(data)
          HISTORY_REPLY  → route_external_reply(data) + on_history_reply(data)

        on_idle() is called whenever the socket has been quiet for ~1 second.
        """
        log.info("ZMQ listen_stream starting...")
        last_msg_time = time.monotonic()
        poll_count = 0

        while self._running:
            poll_count += 1
            log.debug("ZMQ poll iteration starting... (poll #%d)", poll_count)
            try:
                raw = self._sub.recv_string()
                log.debug("ZMQ SUB has data! (received %d bytes)", len(raw))
            except zmq.error.Again:
                log.debug("ZMQ SUB timeout (no message in 5s)")
                if on_idle and (time.monotonic() - last_msg_time) >= 1.0:
                    on_idle()
                    last_msg_time = time.monotonic()
                continue
            except Exception as e:
                log.error("ZMQ SUB error: %s", e)
                break

            last_msg_time = time.monotonic()
            try:
                data: dict = json.loads(raw)
            except json.JSONDecodeError:
                log.warning("Malformed SUB message: %r", raw[:120])
                continue

            msg_type: str = data.get("type", "")
            log.info("Stream message received: type=%s", msg_type)

            if msg_type == "CANDLE":
                on_candle(data)
            elif msg_type == "ACCOUNT":
                on_account(data)
            elif msg_type == "POSITIONS":
                on_positions(data)
            elif msg_type == "HISTORY_REPLY":
                # EA sends HISTORY_REPLY on PUSH (5556), not PUB (5557).
                # The dispatcher thread already routed it to the caller's queue.
                # route_external_reply() is not needed here.
                on_history_reply(data)
            else:
                log.debug("Unknown stream message type: %s", msg_type)

    # -----------------------------------------------------------------------
    # External reply routing (EA quirk fix)
    # -----------------------------------------------------------------------

    def route_external_reply(self, data: dict) -> None:
        """
        Inject a reply dict into the correct per-request queue.

        The MT5 EA sends HISTORY_REPLY on PUB instead of PULL, so the
        listen_stream caller must forward those messages here so the
        request_historical() caller unblocks correctly.
        """
        req_id = data.get("req_id")
        if not req_id:
            log.debug("route_external_reply: no req_id in message")
            return

        with self._reply_lock:
            q = self._reply_queues.get(req_id)

        if q is not None:
            q.put(data)
        else:
            log.debug("route_external_reply: no waiting caller for req_id=%s", req_id)

    # -----------------------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------------------

    def close(self) -> None:
        """Stop the dispatcher thread and close all sockets."""
        self._running = False
        self._dispatcher.join(timeout=2.0)
        self._push.close()
        self._pull.close()
        self._sub.close()
        self._ctx.term()
        log.info("ZmqConnector closed")

    # -----------------------------------------------------------------------
    # Internal
    # -----------------------------------------------------------------------

    def _dispatch_loop(self) -> None:
        """Background thread: read PULL replies and route to waiting callers."""
        while self._running:
            try:
                raw = self._pull.recv_string()
            except zmq.error.Again:
                continue
            except zmq.ZMQError as exc:
                if self._running:
                    log.error("PULL receive error: %s", exc)
                break

            try:
                data: dict = json.loads(raw)
            except json.JSONDecodeError:
                log.warning("Malformed PULL message: %r", raw[:120])
                continue

            req_id = data.get("req_id")
            if not req_id:
                log.debug("PULL message has no req_id: %s", data)
                continue

            with self._reply_lock:
                q = self._reply_queues.get(req_id)

            if q is not None:
                q.put(data)
            else:
                log.debug("No waiting caller for req_id=%s", req_id)

    def _remove_queue(self, req_id: str) -> None:
        with self._reply_lock:
            self._reply_queues.pop(req_id, None)
