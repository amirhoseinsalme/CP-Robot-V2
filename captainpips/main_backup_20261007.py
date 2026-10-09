"""
CaptainPips V2 — main entry point.

Wires all modules together: structure building, strategies, risk gating,
position management, and the panel API.  No business logic here, only
coordination.
"""

from __future__ import annotations

import asyncio
import calendar
import logging
import logging.handlers
import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone

import uvicorn

from captainpips.api.main import BotState, ConnectionManager, app, get_state, _manager
from captainpips.chart.drawer import ChartDrawer
from captainpips.config import Config, SymbolConfig
from captainpips.connectors.mt5_zmq import ZmqConnector
from captainpips.core.definitions import (
    Candle,
    Direction,
    Segment,
    SegmentKind,
    Signal,
)
from captainpips.core.shift_scorer import ShiftScorer
from captainpips.core.strategies.base import BaseStrategy, EntryWatch
from captainpips.core.strategies.leg2chain import Leg2Chain
from captainpips.core.strategies.leg3drive import Leg3Drive, _d_beyond_b
from captainpips.core.strategies.paradoxical import Paradoxical
from captainpips.core.structure_rules import StructurePoints
from captainpips.core.structure_state import EventKind, StructureBuilder, StructureEvent
from captainpips.position.manager import PositionManager
from captainpips.risk.risk_manager import RiskManager
from captainpips.supervisor.memory import SupervisorMemory

log = logging.getLogger(__name__)

_EMA_PERIOD = 60


class MemoryLogHandler(logging.Handler):
    """Keeps the last N log records in memory for the panel log viewer."""

    def __init__(self, maxlen: int = 200) -> None:
        super().__init__()
        self.logs: deque[dict] = deque(maxlen=maxlen)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.logs.append({
                "time": datetime.fromtimestamp(record.created).strftime("%H:%M:%S"),
                "level": record.levelname,
                "module": record.module,
                "message": record.getMessage(),
            })
        except Exception:
            pass


class CaptainPipsBot:
    """Multi-symbol orchestrator — one instance runs the whole robot."""

    def __init__(self, config: Config) -> None:
        self.config = config

        # --- per-symbol state ---
        self.builders: dict[str, StructureBuilder] = {}
        self.paradox: dict[str, Paradoxical] = {}
        self.ema: dict[str, list[float]] = {}
        self._ema_prev: dict[str, float | None] = {}
        self.active_watch: dict[str, tuple[EntryWatch, BaseStrategy]] = {}
        self._backfill_end_time: dict[str, int] = {}
        self._is_backfilling: dict[str, bool] = {}

        for sc in config.enabled_symbols():
            sym = sc.symbol
            self.builders[sym] = StructureBuilder()
            self.paradox[sym] = Paradoxical()
            self.ema[sym] = []
            self._ema_prev[sym] = None

        # --- shared modules ---
        self.strategies: list[BaseStrategy] = [Leg2Chain(), Leg3Drive()]
        self.shift_scorer = ShiftScorer()
        self.zmq = ZmqConnector(config.zmq)
        self.risk = RiskManager(config.risk)
        self.positions = PositionManager(self.zmq, config)
        self._drawer = ChartDrawer(self.zmq)
        self._supervisor_memory = SupervisorMemory()

        # --- account state ---
        self.account_balance: float = 0.0
        self.daily_loss_pct: float = 0.0
        self._session_start_balance: float | None = None

        # --- per-symbol candle index counters ---
        self._candle_index: dict[str, int] = {
            sc.symbol: 0 for sc in config.enabled_symbols()
        }

        # --- per-symbol stop candle tracking (for drawing updates) ---
        self._stop_candle_count: dict[str, int] = {}

        # --- recent candle cache (last 100 per symbol, for /api/candles) ---
        self._recent_candles: dict[str, deque] = {}

        # --- broker time (captured from first EA message) ---
        self._broker_time_event = threading.Event()
        self._broker_now_ts: int | None = None
        self._broker_offset: int = 0  # broker_ts - local_ts at first sync

        # --- API plumbing ---
        self._api_thread: threading.Thread | None = None
        self._stream_thread: threading.Thread | None = None
        self._ws_manager: ConnectionManager = _manager
        self._bot_state: BotState = get_state()
        self._loop: asyncio.AbstractEventLoop | None = None

        # --- in-memory log handler (shared with API) ---
        self._log_handler = MemoryLogHandler(maxlen=200)
        logging.getLogger().addHandler(self._log_handler)

        # --- file logging (timestamped per session) ---
        self._log_dir = os.path.join(os.path.dirname(__file__), '..', 'logs')
        os.makedirs(self._log_dir, exist_ok=True)
        self._file_handler: logging.handlers.FileHandler | None = None
        self._rotate_session_log()  # create first session log file

        self._wire_bot_state()

    # ------------------------------------------------------------------
    # Session logging
    # ------------------------------------------------------------------

    def _rotate_session_log(self) -> None:
        """Create a new timestamped log file for this session, clean up old logs."""
        # Remove old file handler if it exists
        if self._file_handler:
            logging.getLogger().removeHandler(self._file_handler)
            self._file_handler.close()

        # Create new timestamped log file
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        log_file = os.path.join(self._log_dir, f'robot_{timestamp}.log')
        self._file_handler = logging.FileHandler(log_file, encoding='utf-8')
        self._file_handler.setLevel(logging.DEBUG)
        self._file_handler.setFormatter(logging.Formatter(
            '%(asctime)s  %(name)-30s %(levelname)-6s %(message)s'
        ))
        logging.getLogger().addHandler(self._file_handler)
        log.info(f"Session log: {log_file}")

        # Clean up old session logs, keep last 10
        self._cleanup_old_logs()

    def _cleanup_old_logs(self) -> None:
        """Remove session logs older than the most recent 10."""
        import glob
        pattern = os.path.join(self._log_dir, 'robot_*.log')
        logs = sorted(glob.glob(pattern), reverse=True)
        if len(logs) > 10:
            for old_log in logs[10:]:
                try:
                    os.remove(old_log)
                    log.debug(f"Cleaned up old log: {os.path.basename(old_log)}")
                except OSError as e:
                    log.warning(f"Failed to delete old log {os.path.basename(old_log)}: {e}")

    # ------------------------------------------------------------------
    # Startup
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Boot sequence: API server → ZMQ stream (background) → backfill → keep-alive."""
        # Migration: ensure risk section has sl_pips and tp_pips
        if not hasattr(self.config.risk, 'sl_pips') or self.config.risk.sl_pips == 0:
            self.config.risk.sl_pips = 60.0
            self.config.risk.tp_pips = 30.0
            self.config.save("config.json")
            log.info("Config migrated: added sl_pips=60.0, tp_pips=30.0 to risk section")

        self._start_api()

        self._stream_thread = threading.Thread(
            target=self._start_stream, daemon=True, name="zmq-stream"
        )
        self._stream_thread.start()
        time.sleep(0.5)  # let the SUB socket connect before backfill sends REQ messages

        self._backfill()

        # Replay post-backfill structure to the ChartDrawer so objects appear on connect
        for symbol, builder in self.builders.items():
            for i, seg in enumerate(builder.segments):
                if seg.kind == SegmentKind.LEG:
                    leg_num = sum(1 for s in builder.segments[:i + 1] if s.kind == SegmentKind.LEG)
                    self._drawer.on_leg_confirmed(seg, leg_num, symbol)
                elif seg.kind == SegmentKind.STOP:
                    stop_num = sum(1 for s in builder.segments[:i + 1] if s.kind == SegmentKind.STOP)
                    self._drawer.on_stop_started(seg, stop_num, symbol)

        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            log.info("Shutting down...")
            self.zmq.close()

    def _start_api(self) -> None:
        """Run FastAPI/uvicorn in a daemon thread."""
        cfg = self.config.app

        def _run() -> None:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            uv_config = uvicorn.Config(
                app,
                host=cfg.api_host,
                port=cfg.api_port,
                log_level=cfg.log_level.lower(),
                loop="asyncio",
            )
            server = uvicorn.Server(uv_config)
            self._loop.run_until_complete(server.serve())

        self._api_thread = threading.Thread(target=_run, daemon=True, name="api-server")
        self._api_thread.start()
        log.info("API server starting on %s:%d", cfg.api_host, cfg.api_port)
        time.sleep(1)  # wait for uvicorn to start and self._loop to be assigned

    def _backfill(self) -> None:
        """Request historical candles from session_start to now for each symbol."""
        broker_ts = self._wait_for_broker_time(timeout=15.0)
        if broker_ts is None:
            log.warning("Broker time not available — skipping backfill")
            return

        symbols = self.config.enabled_symbols()
        if not symbols:
            log.info("No symbols configured — backfill skipped")
            return

        for sc in symbols:
            self._backfill_symbol(sc, broker_ts)

        # Broadcast current structure state so panel gets it immediately on connect
        for sym, builder in self.builders.items():
            self._broadcast({
                "type": "structure",
                "symbol": sym,
                "confirmed_legs": builder.confirmed_legs,
                "direction": builder.direction.value if builder.direction else None,
                "segments": len(builder.segments),
                "candles_processed": self._candle_index.get(sym, 0),
            })
        log.info("Post-backfill structure snapshot broadcast for %d symbols", len(self.builders))

    def _get_structure_state(self) -> dict:
        """Return current structure state for all tracked symbols."""
        result = {}
        for sym, builder in self.builders.items():
            leg_num = stop_num = 0
            segments_data = []
            for seg in builder.segments:
                if seg.kind == SegmentKind.LEG:
                    leg_num += 1
                    num = leg_num
                    confirmed = leg_num <= builder.confirmed_legs
                else:
                    stop_num += 1
                    num = stop_num
                    confirmed = True
                segments_data.append({
                    "kind": seg.kind.value,
                    "direction": seg.direction.value,
                    "n_candles": seg.n_candles,
                    "high": seg.high,
                    "low": seg.low,
                    "num": num,
                    "confirmed": confirmed,
                })
            result[sym] = {
                "confirmed_legs": builder.confirmed_legs,
                "direction": builder.direction.value if builder.direction else None,
                "segments": segments_data,
                "candles_processed": self._candle_index.get(sym, 0),
            }
        return result

    def _retag_recent_candles(self, symbol: str, builder) -> None:
        seg_by_raw: dict[int, str] = {}
        leg_n = stop_n = 0
        for seg in builder.segments:
            if seg.kind == SegmentKind.LEG:
                leg_n += 1
                tag = f"L{leg_n}"
            else:
                stop_n += 1
                tag = f"S{stop_n}"
            for c in seg.candles:
                seg_by_raw[c.raw_time] = tag
        for rec in self._recent_candles.get(symbol, []):
            rec["seg"] = seg_by_raw.get(rec.get("raw_time", -1), "")

    def _get_recent_candles(self, symbol: str | None = None) -> dict:
        """Return recent candles for one symbol or the first tracked symbol."""
        if symbol is None:
            symbol = next(iter(self._recent_candles), None)
        if symbol is None or symbol not in self._recent_candles:
            return {"symbol": symbol, "candles": []}
        return {"symbol": symbol, "candles": list(self._recent_candles[symbol])}

    def _wait_for_broker_time(self, timeout: float = 15.0) -> int | None:
        """Wait for the stream thread to capture broker time from an EA message."""
        if self._broker_now_ts is not None:
            return self._broker_now_ts
        log.info("Waiting for first EA message to establish broker time...")
        if self._broker_time_event.wait(timeout=timeout):
            log.info(
                "Broker time established: %s",
                datetime.utcfromtimestamp(self._broker_now_ts).strftime("%Y-%m-%d %H:%M:%S"),
            )
            return self._broker_now_ts
        log.error("Broker time not received within %.0fs — cannot compute session timestamps", timeout)
        return None

    @staticmethod
    def _session_ts_from_broker(broker_now_ts: int, session_hhmm: str) -> int | None:
        """Convert 'HH:MM' session_start into a Unix timestamp in broker-clock space."""
        try:
            h, m = session_hhmm.strip().split(":")
            h, m = int(h), int(m)
            if not (0 <= h <= 23 and 0 <= m <= 59):
                raise ValueError("out of range")
        except Exception:
            log.error("Invalid session_start: %r", session_hhmm)
            return None
        broker_clock = datetime.utcfromtimestamp(broker_now_ts)
        session_clock = broker_clock.replace(hour=h, minute=m, second=0, microsecond=0)
        return int(calendar.timegm(session_clock.timetuple()))

    def _backfill_symbol(self, sc: SymbolConfig, broker_ts: int) -> None:
        """Backfill one symbol from its session_start to broker_ts.

        Requests 60 extra minutes before session_start for EMA warm-up.
        Pre-session candles only feed the EMA — structure building starts
        at session_start.
        """
        sym = sc.symbol
        self._ensure_symbol(sym)

        to_ts = int(time.time()) + self._broker_offset
        from_ts = self._session_ts_from_broker(to_ts, sc.session_start)
        log.info(
            "Backfill range: from_ts=%s session_start=%s to_ts=%s broker_now=%s",
            from_ts,
            sc.session_start,
            datetime.utcfromtimestamp(to_ts).strftime("%H:%M"),
            datetime.utcfromtimestamp(broker_ts).strftime("%H:%M"),
        )
        if from_ts is None:
            log.warning("Cannot compute backfill range for %s — skipping", sym)
            return

        if from_ts >= to_ts:
            log.info("%s: session %s has not started yet — no backfill needed", sym, sc.session_start)
            return

        # Request 60 extra minutes before session_start for EMA warm-up
        ema_from_ts = from_ts - (60 * 60)

        log.info(
            "Backfilling %s: EMA warm-up from %s, structure from %s to %s",
            sym,
            datetime.utcfromtimestamp(ema_from_ts).strftime("%H:%M"),
            datetime.utcfromtimestamp(from_ts).strftime("%H:%M"),
            datetime.utcfromtimestamp(to_ts).strftime("%H:%M"),
        )

        bars = self.zmq.request_historical(sym, from_ts=ema_from_ts, to_ts=to_ts, timeframe=1)
        log.info("Got %d bars for %s", len(bars) if bars else 0, sym)

        if not bars:
            log.warning("No historical bars returned for %s", sym)
            return

        log.info("Backfill %s: received %d bars", sym, len(bars))
        builder = self.builders.get(sym)
        if builder:
            log.info("Builder reset for %s (was %d segments)", sym, len(builder.segments))
            builder.reset(from_candle_index=0)

        self._is_backfilling[sym] = True
        for raw in bars:
            raw_time = int(raw.get("time", 0))
            if raw_time < from_ts:
                # Pre-session candle: EMA warm-up only, no structure building
                self._update_ema(sym, float(raw["close"]))
            else:
                self._process_candle(sym, raw, broadcast=False)
        self._is_backfilling[sym] = False

        # Draw all current segments after backfill
        builder = self.builders[sym]
        leg_num = 0
        stop_num = 0
        for i, seg in enumerate(builder.segments):
            if seg.kind == SegmentKind.LEG:
                leg_num += 1
                self._drawer.on_leg_confirmed(seg, leg_num, sym)
            elif seg.kind == SegmentKind.STOP:
                stop_num += 1
                self._drawer.on_stop_started(seg, stop_num, sym)
        if bars:
            last_raw_time = int(bars[-1].get("time", 0))
            self._backfill_end_time[sym] = last_raw_time
            log.info("Backfill end time for %s: %d", sym, last_raw_time)
        if builder.segments:
            log.info("Backfill complete for %s: drew %d legs, %d stops", sym, leg_num, stop_num)

    def _start_stream(self) -> None:
        """Blocking ZMQ stream listener — runs on the zmq-stream daemon thread."""
        log.info("Starting ZMQ stream listener")
        try:
            self.zmq.listen_stream(
                on_candle=self._on_candle_message,
                on_account=self._on_account_message,
                on_positions=self._on_positions_message,
                on_history_reply=self._on_history_reply,
                on_idle=self._on_idle,
            )
        except Exception as e:
            log.exception("listen_stream crashed: %s", e)

    # ------------------------------------------------------------------
    # Inbound message handlers
    # ------------------------------------------------------------------

    def _ensure_symbol(self, symbol: str) -> None:
        """Lazily register per-symbol state for any symbol the EA streams."""
        if symbol in self.builders:
            return
        log.info("Auto-registering streamed symbol: %s", symbol)
        self.builders[symbol] = StructureBuilder()
        self.paradox[symbol] = Paradoxical()
        self.ema[symbol] = []
        self._ema_prev[symbol] = None
        self._candle_index[symbol] = 0

    def _on_candle_message(self, data: dict) -> None:
        """Route an incoming CANDLE message to the right symbol pipeline."""
        log.info("CANDLE received: symbol=%s close=%s", data.get("symbol"), data.get("close"))

        # Capture broker time from the first candle the EA sends.
        ts = data.get("time", 0)
        if ts and self._broker_now_ts is None:
            self._broker_now_ts = int(ts)
            self._broker_offset = int(ts) - int(time.time())
            self._broker_time_event.set()

        symbol: str = data.get("symbol", "")
        if not symbol:
            return

        self._ensure_symbol(symbol)

        # Trigger backfill on first candle if not yet backfilled
        if symbol in self._candle_index and self._candle_index[symbol] == 0:
            if self._broker_now_ts is not None:
                sc = self._symbol_config(symbol)
                if sc:
                    log.info(f"First candle for {symbol} — triggering backfill")
                    self._backfill_symbol(sc, self._broker_now_ts)

        # EA sends "time" as a Unix timestamp (int); convert to HH:MM for
        # session filtering and Candle storage.
        time_str = datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%H:%M")

        sc = self._symbol_config(symbol)
        if sc and not self._in_session(time_str, sc):
            log.debug("CANDLE skipped (outside session): symbol=%s time=%s", symbol, time_str)
            return

        self._process_candle(symbol, data, time_str=time_str, broadcast=True)

    def _on_account_message(self, data: dict) -> None:
        """Update cached account state from EA broadcast."""
        balance = data.get("balance", self.account_balance)
        if self._session_start_balance is None and balance > 0:
            self._session_start_balance = balance
        self.account_balance = balance
        self.positions.update_balance(balance)  # keep position manager in sync
        if self._session_start_balance and self._session_start_balance > 0:
            loss = self._session_start_balance - balance
            self.daily_loss_pct = max(0.0, loss / self._session_start_balance * 100.0)
        self._broadcast({
            "type": "account",
            "balance": data.get("balance"),
            "equity": data.get("equity"),
            "open_positions": data.get("open_positions"),
        })

    def _on_positions_message(self, data: dict) -> None:
        self._broadcast({"type": "positions", **data})

    def _on_history_reply(self, data: dict) -> None:
        log.debug("History reply received: %s", data.get("symbol"))

    def _on_idle(self) -> None:
        pass

    # ------------------------------------------------------------------
    # Core pipeline
    # ------------------------------------------------------------------

    def _process_candle(
        self, symbol: str, raw: dict, *, broadcast: bool, time_str: str = "",
    ) -> None:
        # Resolve time string: live path passes it already converted;
        # backfill path converts here from the Unix timestamp in the bar.
        raw_time = int(raw.get("time", 0))
        if not time_str:
            time_str = datetime.fromtimestamp(raw_time, tz=timezone.utc).strftime("%H:%M")

        # Assign and advance a stable per-symbol bar index.
        index = self._candle_index.get(symbol, 0)
        self._candle_index[symbol] = index + 1

        candle = Candle.from_raw(
            index=index,
            time=time_str,
            raw_time=raw_time,
            open=raw["open"],
            high=raw["high"],
            low=raw["low"],
            close=raw["close"],
        )

        # Persist to recent candle cache
        candle_record = {
            "index": index,
            "time": time_str,
            "raw_time": raw_time,
            "label": candle.label.value,
            "direction": candle.direction.value,
            "open": raw.get("open"),
            "high": raw.get("high"),
            "low": raw.get("low"),
            "close": raw.get("close"),
            "seg": "",
        }
        if symbol not in self._recent_candles:
            self._recent_candles[symbol] = deque(maxlen=100)
        self._recent_candles[symbol].append(candle_record)

        # Broadcast candle immediately
        self._broadcast({
            "type": "candle",
            "symbol": symbol,
            "index": index,
            "time": time_str,
            "label": candle.label.value,
            "direction": candle.direction.value,
            "open": raw.get("open"),
            "high": raw.get("high"),
            "low": raw.get("low"),
            "close": raw.get("close"),
        })

        self._update_ema(symbol, candle.close)

        # 1. Paradoxical check (highest priority)
        signal = self.paradox[symbol].on_candle(candle)
        if signal:
            # Paradoxical boundary crossed: structure direction reverses
            # NOT a trade entry — just reset structure
            new_direction = signal.direction
            log.info(
                "[%s] Paradoxical boundary crossed → reset structure direction=%s",
                symbol,
                new_direction.value,
            )

            # Reset builder and replay breaking candle
            builder = self.builders[symbol]
            builder.reset(from_candle_index=candle.index)
            builder.process(candle, ema_values=self.ema.get(symbol, []))

            # Reset paradoxical state
            self.paradox[symbol] = Paradoxical()

            # Clear active watch
            self.active_watch.pop(symbol, None)

            # Broadcast structure reset
            self._broadcast({
                "type": "structure",
                "symbol": symbol,
                "confirmed_legs": 0,
                "direction": None,
                "segments": [],
                "candles_processed": candle.index,
            })
            self._retag_recent_candles(symbol, builder)
            return

        # 2. Structure processing
        builder = self.builders[symbol]
        events = builder.process(candle, ema_values=self.ema[symbol])
        log.info("Events for %s: %s", symbol, [e.kind for e in events])

        # Broadcast structure state — segments with separate leg/stop numbering
        confirmed_legs = builder.confirmed_legs
        leg_num = 0
        stop_num = 0
        segments_data = []
        all_segs = builder.segments
        for i, seg in enumerate(all_segs):
            endpoint = None
            leg_start = None
            if seg.kind == SegmentKind.LEG:
                leg_num += 1
                num = leg_num
                confirmed = leg_num <= confirmed_legs
                prev_stop = None
                if i - 1 >= 0 and all_segs[i - 1].kind == SegmentKind.STOP:
                    prev_stop = all_segs[i - 1]
                endpoint = StructurePoints.leg_endpoint(seg)
                leg_start = StructurePoints.leg_start(seg, prev_stop)
            else:
                stop_num += 1
                num = stop_num
                confirmed = True
            segments_data.append({
                "kind": seg.kind.value,
                "direction": seg.direction.value,
                "n_candles": seg.n_candles,
                "high": seg.high,
                "low": seg.low,
                "endpoint": endpoint,
                "leg_start": leg_start,
                "num": num,
                "confirmed": confirmed,
            })

        log.debug(f"Broadcasting structure: symbol={symbol} "
                  f"confirmed_legs={builder.confirmed_legs} "
                  f"segments={len(builder.segments)} "
                  f"direction={builder.direction}")

        d_beyond_b: bool | None = None
        if builder.confirmed_legs >= 2 and builder.direction:
            d_beyond_b = _d_beyond_b(builder.segments, builder.direction)

        self._broadcast({
            "type": "structure",
            "symbol": symbol,
            "confirmed_legs": builder.confirmed_legs,
            "direction": builder.direction.value if builder.direction else None,
            "segments": segments_data,
            "candles_processed": index,
            "d_beyond_b": d_beyond_b,
        })
        self._drawer.on_fail_zone(builder, symbol)
        self._retag_recent_candles(symbol, builder)

        # 2b. Real-time shift score on every candle while in a stop
        segments = builder.segments
        if segments and segments[-1].kind == SegmentKind.STOP:
            stops = [s for s in segments if s.kind == SegmentKind.STOP]
            legs = [s for s in segments if s.kind == SegmentKind.LEG]
            breaking_stop = stops[-1]
            log.debug(f"Shift calc: symbol={symbol} "
                      f"stop_dir={breaking_stop.direction} "
                      f"segments={len(segments)} "
                      f"legs={len(legs)} stops={len(stops)}")
            shift = self.shift_scorer.score(
                breaking_stop=breaking_stop,
                segments=segments,
                ema_values=self.ema.get(symbol, []),
                all_candles=builder.all_candles,
            )
            self._broadcast({
                "type": "shift_score",
                "symbol": symbol,
                "true_count": shift.true_count,
                "applicable_count": shift.applicable_count,
                "confirmed": shift.confirmed,
                "elements": {
                    "a": shift.a_outbreak,
                    "b": shift.b_double,
                    "c": shift.c_structure_break,
                    "d": shift.d_ema60,
                    "e": shift.e_followthrough,
                    "f": shift.f_closegap,
                    "g": shift.g_size,
                    "h": shift.h_gapfill,
                    "i": shift.i_3cgap,
                },
            })

        # 3. Handle events
        broke = False
        for event in events:
            if event.kind in (EventKind.STRUCTURE_BROKEN, EventKind.SINGLE_LEG_REVERSED):
                self._drawer.on_structure_reset(symbol)
                self._stop_candle_count[symbol] = 0  # reset stop tracking on structure break
                self._on_structure_break(symbol, event, candle)
                broke = True
                break  # builder was reset — discard remaining stale events
            elif event.kind == EventKind.LEG_CONFIRMED:
                leg = builder.segments[-2]  # stop was appended after leg in _promote_and_stop
                self._drawer.on_leg_confirmed(leg, builder.confirmed_legs, symbol)
                self._on_structure_update(symbol, event, candle)
            elif event.kind == EventKind.STOP_STARTED:
                stop = builder.segments[-1]
                stop_num = sum(1 for s in builder.segments if s.kind == SegmentKind.STOP)
                self._drawer.on_stop_started(stop, stop_num, symbol)
                self._stop_candle_count[symbol] = stop.n_candles  # track initial stop size
                self._on_structure_update(symbol, event, candle)

        # 3b. Update stop rectangle if the active stop grew (only when no break)
        if not broke:
            if builder.segments and builder.segments[-1].kind == SegmentKind.STOP:
                last_stop = builder.segments[-1]
                stop_num = sum(1 for s in builder.segments if s.kind == SegmentKind.STOP)
                prev_count = self._stop_candle_count.get(symbol, 0)
                if last_stop.n_candles > prev_count:
                    self._drawer.on_stop_candle(last_stop, stop_num, symbol)
                    self._stop_candle_count[symbol] = last_stop.n_candles
            else:
                self._stop_candle_count[symbol] = 0

        # 4. Active watch tick — skip if a break reset the builder this candle
        if not broke and symbol in self.active_watch:
            self._tick_watch(symbol, candle)

        # 5. Position management
        had_position = self.positions.has_active_trade(symbol)
        trade_before = self.positions.get_active_trade(symbol) if had_position else None
        self.positions.on_price(symbol, candle.low, candle.high)

        # 6. Re-evaluate strategies if position just closed
        if had_position and not self.positions.has_active_trade(symbol):
            log.info("[POSITION CLOSED] %s — re-evaluating strategies", symbol)

            # Update trade with exit info
            if trade_before:
                pnl = self._calculate_pnl(
                    trade_before.direction,
                    trade_before.entry_price,
                    candle.close,
                    trade_before.main_sl_price,
                )
                status = "win" if pnl > 0 else ("loss" if pnl < 0 else "breakeven")
                self._supervisor_memory.update_trade(
                    symbol=symbol,
                    exit_price=candle.close,
                    exit_time=candle.time,
                    pnl=pnl,
                    status=status,
                )
            if not broke and symbol not in self.active_watch:
                builder = self.builders[symbol]
                for strategy in self.strategies:
                    for event_kind in (EventKind.LEG_CONFIRMED, EventKind.STOP_STARTED):
                        probe = StructureEvent(
                            kind=event_kind,
                            confirmed_legs=builder.confirmed_legs,
                        )
                        watch = strategy.on_structure_event(
                            probe,
                            builder.segments,
                            builder.confirmed_legs,
                            candle,
                        )
                        if watch:
                            log.info(
                                "[WATCH ARMED POST-CLOSE] %s strategy=%s mode=%s legs=%d",
                                symbol, strategy.name, watch.mode, builder.confirmed_legs,
                            )
                            self.active_watch[symbol] = (watch, strategy)
                            break
                    if symbol in self.active_watch:
                        break

    # ------------------------------------------------------------------
    # Structure break handling
    # ------------------------------------------------------------------

    def _on_structure_break(
        self, symbol: str, event: StructureEvent, candle: Candle,
    ) -> None:
        builder = self.builders[symbol]
        direction = builder.direction or Direction.NONE
        segments = builder.segments
        legs = [s for s in segments if s.kind == SegmentKind.LEG]
        stops = [s for s in segments if s.kind == SegmentKind.STOP]

        log.info(f"Structure break: symbol={symbol} "
                 f"stops_count={len(stops)} "
                 f"breaking_stop_candles={len(stops[-1].candles) if stops else 0} "
                 f"event={event.kind} "
                 f"backfilling={self._is_backfilling.get(symbol, False)}")

        # Always reset chart first (both backfill and live breaks)
        self._drawer.on_structure_reset(symbol)

        # During backfill: just reset and replay, skip side effects
        if self._is_backfilling.get(symbol, False):
            replay_candles = list(stops[-1].candles) if stops else []
            start_idx = replay_candles[0].index if replay_candles else candle.index
            builder.reset(from_candle_index=start_idx)
            for rc in replay_candles:
                builder.process(rc, ema_values=self.ema.get(symbol, []))
            return

        structure_high = max(s.high for s in segments) if segments else candle.high
        structure_low = min(s.low for s in segments) if segments else candle.low

        self.paradox[symbol].on_break(direction, structure_high, structure_low)
        if self.paradox[symbol].is_active():
            self.paradox[symbol].set_activation_index(candle.index)

        self.active_watch.pop(symbol, None)

        if stops:
            score = self.shift_scorer.score(
                breaking_stop=stops[-1],
                segments=segments,
                ema_values=self.ema[symbol],
                all_candles=self._all_candles(segments),
            )
            log.info(
                "[%s] Shift score: %d/%d (confirmed=%s)",
                symbol,
                score.true_count,
                score.applicable_count,
                score.confirmed,
            )
            self._broadcast({
                "type": "shift_score",
                "symbol": symbol,
                "true_count": score.true_count,
                "applicable_count": score.applicable_count,
                "confirmed": score.confirmed,
            })

        # Reset builder and replay the breaking movement
        replay_candles = list(stops[-1].candles) if stops else []
        log.info(f"Replay: {len(replay_candles)} candles "
                 f"from {replay_candles[0].time if replay_candles else 'none'}")
        start_idx = replay_candles[0].index if replay_candles else candle.index
        builder.reset(from_candle_index=start_idx)
        for rc in replay_candles:
            builder.process(rc, ema_values=self.ema.get(symbol, []))

    # ------------------------------------------------------------------
    # Structure update → try arming a watch
    # ------------------------------------------------------------------

    def _on_structure_update(
        self, symbol: str, event: StructureEvent, candle: Candle,
    ) -> None:
        if self.positions.has_active_trade(symbol):
            log.debug("Skipping watch arm: active position on %s", symbol)
            return

        builder = self.builders[symbol]

        if event.kind == EventKind.LEG_CONFIRMED and builder.confirmed_legs >= 2:
            existing = self.active_watch.get(symbol)
            if existing and existing[1].name == "Leg2Chain":
                log.info("[WATCH REPLACED] %s cancelling stale Leg2Chain "
                         "(confirmed_legs=%d, Leg3Drive gets priority)",
                         symbol, builder.confirmed_legs)
                self.active_watch.pop(symbol)

        if symbol in self.active_watch:
            return

        log.debug(f"Checking strategies for arm: "
                  f"confirmed_legs={builder.confirmed_legs} "
                  f"event={event.kind.value}")
        for strategy in self.strategies:
            watch = strategy.on_structure_event(
                event,
                builder.segments,
                builder.confirmed_legs,
                candle,
            )
            log.debug(f"  {strategy.name}: watch={watch}")
            if watch:
                log.info(
                    "[WATCH ARMED] %s strategy=%s mode=%s legs=%d",
                    symbol,
                    strategy.name,
                    watch.mode,
                    builder.confirmed_legs,
                )
                self.active_watch[symbol] = (watch, strategy)
                break

    # ------------------------------------------------------------------
    # Watch tick
    # ------------------------------------------------------------------

    def _tick_watch(self, symbol: str, candle: Candle) -> None:
        watch, strategy = self.active_watch[symbol]
        old_mode = watch.mode
        signal = strategy.on_candle(candle, watch, self.builders[symbol].segments)

        if signal:
            backfill_end = self._backfill_end_time.get(symbol, 0)
            if candle.raw_time <= backfill_end:
                log.debug("[SIGNAL SKIPPED] %s candle_time=%d <= backfill_end=%d "
                          "keeping watch alive",
                          symbol, candle.raw_time, backfill_end)
                return

            signal = Signal(
                symbol=symbol,
                direction=signal.direction,
                entry_price=signal.entry_price,
                reason=signal.reason,
                strategy=signal.strategy,
                leg_number=signal.leg_number,
            )
            log.info(
                "[SIGNAL] %s %s %s @ %.5f reason=%s watch_mode=%s",
                symbol,
                signal.strategy,
                signal.direction.value,
                signal.entry_price,
                signal.reason,
                watch.mode,
            )
            self._execute(signal, symbol, candle)
            self.active_watch.pop(symbol, None)
            return

        if watch.mode in ("DONE", "CANCELLED"):
            log.info(
                "[WATCH %s] %s strategy=%s",
                watch.mode,
                symbol,
                strategy.name,
            )
            self.active_watch.pop(symbol, None)

        if old_mode != watch.mode:
            self._broadcast({
                "type": "watch_update",
                "symbol": symbol,
                "strategy": strategy.name,
                "mode": watch.mode,
            })

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def _execute(self, signal: Signal, symbol: str, candle: Candle) -> None:
        log.info(
            "[%s] Signal: %s %s @ %.5f [%s]",
            symbol,
            signal.strategy,
            signal.direction.value,
            signal.entry_price,
            signal.reason,
        )

        active_count = sum(
            1 for s in self.builders if self.positions.has_active_trade(s)
        )
        ok, reason = self.risk.can_trade(
            symbol=symbol,
            active_trade_count=active_count,
            daily_loss_pct=self.daily_loss_pct,
            robot_running=self._bot_state.robot_running,
        )
        if not ok:
            log.info(f"[RISK BLOCKED] {symbol} reason={reason} "
                     f"robot_running={self._bot_state.robot_running} "
                     f"active_count={active_count} "
                     f"daily_loss={self.daily_loss_pct}")
            return

        sc = self._symbol_config(symbol)
        if sc is None:
            log.error("[%s] No symbol config — cannot open trade", symbol)
            return

        segments = self.builders[symbol].segments if symbol in self.builders else None
        if segments:
            kinds = [s.kind for s in segments]
            for j in range(len(kinds) - 1):
                if kinds[j] == kinds[j + 1]:
                    log.warning(f"[{symbol}] Non-alternating segments detected")
                    break

        opened = self.positions.open_trade(signal, sc, self.account_balance, segments)

        if opened:
            log.info(f"[POS1 OPENED] {symbol} direction={signal.direction.value} "
                     f"entry={signal.entry_price} strategy={signal.strategy}")

            # Record trade to supervisor memory
            trade = self.positions.get_active_trade(symbol)
            if trade:
                self._supervisor_memory.save_trade({
                    "symbol": symbol,
                    "direction": signal.direction.value,
                    "entry_price": signal.entry_price,
                    "sl_price": trade.main_sl_price,
                    "strategy": signal.strategy,
                    "entry_time": candle.time,
                    "status": "open",
                })

            # Clear watch from HUD
            self._broadcast({
                "type": "watch_update",
                "symbol": symbol,
                "strategy": None,
                "mode": "DONE",
            })

        self._broadcast({
            "type": "signal",
            "symbol": symbol,
            "strategy": signal.strategy,
            "direction": signal.direction.value,
            "entry_price": signal.entry_price,
            "reason": signal.reason,
            "executed": opened,
        })

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _update_ema(self, symbol: str, close: float) -> None:
        prev = self._ema_prev[symbol]
        if prev is None:
            self._ema_prev[symbol] = close
        else:
            k = 2.0 / (_EMA_PERIOD + 1)
            self._ema_prev[symbol] = close * k + prev * (1.0 - k)
        self.ema[symbol].append(self._ema_prev[symbol])

    @staticmethod
    def _calculate_pnl(direction: Direction, entry: float, exit: float, sl: float) -> float:
        """Calculate PnL in pips (positive for win, negative for loss)."""
        risk_distance = abs(entry - sl)
        if risk_distance == 0:
            return 0.0
        if direction == Direction.BULL:
            return (exit - entry) / risk_distance
        return (entry - exit) / risk_distance

    def _symbol_config(self, symbol: str) -> SymbolConfig | None:
        for sc in self.config.symbols:
            if sc.symbol == symbol:
                return sc
        return None

    @staticmethod
    def _in_session(time_str: str, sc: SymbolConfig) -> bool:
        """True if time_str (HH:MM) falls within the symbol's session window."""
        if not time_str:
            return True
        return sc.session_start <= time_str <= sc.session_end

    @staticmethod
    def _all_candles(segments: list[Segment]) -> list[Candle]:
        return [c for seg in segments for c in seg.candles]

    def _wire_bot_state(self) -> None:
        """Connect live bot references into the API's BotState singleton."""
        self._bot_state.config = self.config
        self._bot_state.robot_running = self.config.risk.robot_running
        self._bot_state.log_handler = self._log_handler
        self._bot_state.get_structure = self._get_structure_state
        self._bot_state.get_candles = self._get_recent_candles
        self._bot_state.on_config_changed.append(self._on_config_reload)

    def _on_config_reload(self, new_config: Config) -> None:
        """Hot-reload callback from the API when config is saved."""
        # Capture old session windows before replacing config
        old_sessions = {sc.symbol: sc.session_start for sc in self.config.enabled_symbols()}

        self.config = new_config
        self.risk.update_config(new_config.risk)
        log.info("Config hot-reloaded from API")

        # Remove builders for symbols that are now disabled / removed
        enabled = {sc.symbol for sc in new_config.enabled_symbols()}
        for sym in list(self.builders.keys()):
            if sym not in enabled:
                log.info("Symbol %s removed from config — dropping builder", sym)
                self.builders.pop(sym, None)
                self.paradox.pop(sym, None)
                self.ema.pop(sym, None)
                self._ema_prev.pop(sym, None)
                self._candle_index.pop(sym, None)

        # Full reset for symbols whose session_start changed
        session_changed = False
        for sc in new_config.enabled_symbols():
            sym = sc.symbol
            if old_sessions.get(sym) != sc.session_start:
                log.info(
                    "Session start changed for %s (%s → %s) — full state reset",
                    sym, old_sessions.get(sym, "?"), sc.session_start,
                )
                self.builders[sym] = StructureBuilder()
                self.paradox[sym] = Paradoxical()
                self.ema[sym] = []
                self._ema_prev[sym] = None
                self._candle_index.pop(sym, None)
                self.active_watch.pop(sym, None)
                session_changed = True

        # Create new session log if session_start changed
        if session_changed:
            self._rotate_session_log()

        # Clear candle cache so panel fetches fresh data after config change
        self._recent_candles = {}
        log.info("Candle cache cleared on config reload")

        # Re-backfill with updated session times if broker time is known
        if self._broker_now_ts is not None:
            time.sleep(0.5)  # wait for config to propagate
            log.info("Starting backfill after config reload")
            for sc in new_config.enabled_symbols():
                self._backfill_symbol(sc, self._broker_now_ts)

    def _broadcast(self, payload: dict) -> None:
        """Thread-safe broadcast to all WebSocket clients."""
        log.info(
            "Broadcast: loop=%s clients=%d type=%s",
            self._loop is not None,
            len(self._ws_manager),
            payload.get("type", "?"),
        )
        if self._loop is None:
            log.warning("Broadcast skipped — event loop not ready yet")
            return
        try:
            asyncio.run_coroutine_threadsafe(
                self._ws_manager.broadcast(payload), self._loop,
            )
        except RuntimeError as e:
            log.warning("Broadcast failed: %s", e)


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s  %(name)-24s  %(levelname)-5s  %(message)s",
        datefmt="%H:%M:%S",
    )
    config = Config.load("config.json")
    bot = CaptainPipsBot(config)
    bot.start()
