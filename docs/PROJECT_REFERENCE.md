# CaptainPips V2 — Project Reference

Generated 2026-10-09 from the working tree (branch `main`, uncommitted changes included).
Everything here describes **what the code does now**, not intended behaviour. Contradictions and bugs are listed in §14.

Paths are relative to `D:\CP Robot V2` unless stated otherwise.

> **Pending prompts not yet applied** (checked by grep): `StructureBuilder._can_promote`, drawer `prev_stop=` / `prev_leg=` keyword arguments, `.upper()` symbol normalisation, and the `M2Strategy` wiring in `main.py`. None of them exist in the code yet, and this document does not assume them.

---

## 1. Overview

CaptainPips V2 is a Python trading robot for MetaTrader 5. An MT5 Expert Advisor (EA) acts as a ZeroMQ bridge: it streams closed M1 candles, account and positions data to Python, and executes orders and chart-draw commands that Python sends back. Python classifies each candle, builds a leg/stop market structure, arms entry "watches" (strategies), gates them through risk checks, opens and manages positions with an R-based state machine, draws the structure on the MT5 chart (EA → GlobalVariables → indicator), and streams everything to a web panel and a desktop HUD over WebSocket.

### Run commands

| What | Command / URL | Notes |
|---|---|---|
| Robot + API + panel | `python -m captainpips.main` (from repo root) | CWD matters: `config.json`, `captainpips/panel/static` and template paths are relative. `Launch_CaptainPips.bat` does the same, then opens `http://localhost:8000`. |
| Web panel | `http://127.0.0.1:8000/` (monitor), `/settings`, `/logs`, `/hud`, `/backtest`, `/supervisor` | Served by the same FastAPI process. |
| Desktop HUD (tkinter) | `python captainpips/hud/app.py`, or `POST /api/hud/launch` (button in monitor.html) | Connects to `ws://localhost:8000/ws/monitor`. |
| Tests | `python -m pytest captainpips/tests` | 33 tests pass (18 in `test_position.py`, 15 in `test_structure_rules.py`). `test_scenarios.py` and `test_strategies.py` collect 0 tests. |
| ZMQ smoke test | `python test_zmq.py` | Waits 30 s for one SUB message on port 5557. |
| MT5 side | EA `CP EA V3.mq5` on a chart + indicator `CP Structure V11.mq5` | Both live **outside the repo** in `C:\Users\Asus\AppData\Roaming\MetaQuotes\Terminal\E6E3D0917DD641581E4779524EB3B1AA\MQL5\{Experts,Indicators}\`. The EA needs `<Zmq/Zmq.mqh>` (mql-zmq) and `libzmq.dll`. EA input `WatchSymbols` (default `"US30.U26"`) decides which symbols are streamed. |

### Dependencies (`captainpips/requirements.txt`)
`pyzmq`, `pydantic>=2`, `pydantic-settings`, `fastapi`, `uvicorn[standard]`, `websockets`, `pandas`, `numpy`, `python-dateutil`, `tzdata`. The HUD also needs `tkinter` (stdlib). `pandas`, `numpy` and `pydantic-settings` are listed but not imported by any module.

---

## 2. File map

### Python (`captainpips/`)
| File | Purpose |
|---|---|
| `main.py` | `CaptainPipsBot`: orchestrator. Threads, backfill, per-candle pipeline, strategy wiring, broadcasts. |
| `main_backup_20261007.py` | Older copy of `main.py` (lacks `_compute_active_targets`, `_broadcast_paradox`). Not imported. |
| `config.py` | Pydantic config models + `Config.load/save/enabled_symbols`. |
| `connectors/candle_classifier.py` | Port of the MQL5 decision-tree classifier → `(label, direction)`. |
| `connectors/mt5_zmq.py` | `ZmqConnector`: PUSH/PULL/SUB sockets, request/reply dispatcher, stream listener. |
| `core/definitions.py` | Enums (`CandleLabel`, `Direction`, `SegmentKind`), dataclasses (`Candle`, `Segment`, `Signal`), label sets. |
| `core/structure_rules.py` | `LegValidator`, `StructurePoints`, `LegCounter`, `BreakDetector`. |
| `core/structure_state.py` | `StructureBuilder` state machine, `EventKind`, `StructureEvent`. |
| `core/shift_scorer.py` | `ShiftScorer` / `ShiftScore`: 9-element "Legs Shift" scoring. |
| `core/strategies/base.py` | `EntryWatch` dataclass, `BaseStrategy` ABC. |
| `core/strategies/leg2chain.py` | `Leg2Chain` (arms after leg 1). |
| `core/strategies/leg3drive.py` | `Leg3Drive` (arms after leg 2, D > B); `_d_beyond_b` helper (also used by main). |
| `core/strategies/paradoxical.py` | `Paradoxical`: alternating-break decision zone. |
| `core/strategies/M2Bo.py` | `M2BO`: post-break stop-2 close breakout. File name is `M2Bo.py` (import must match case). |
| `core/strategies/M2.py` | `M2Strategy` / `M2Watch`: stop-2 touch strategy. **Not wired into main.py.** |
| `position/manager.py` | `PositionManager`, `ActiveTrade`, `_sl_price`, `_tp_price`. |
| `position/state_machine.py` | `TradeStateMachine`, `TradeState`, `TradeEvent`, R thresholds. |
| `position/sizing.py` | `PositionSizer.calculate`. |
| `risk/risk_manager.py` | `RiskManager.can_trade`. |
| `chart/drawer.py` | `ChartDrawer`: builds DRAW commands for the EA. |
| `api/main.py` | FastAPI app, `BotState`, `ConnectionManager`, page routes, config/symbol/status/robot/candles/structure/logs routes, `/ws/monitor`. |
| `api/backtest.py` | Router: `POST /api/backtest/run` (CSV upload → `BacktestEngine`). |
| `api/supervisor.py` | Router: `/api/supervisor/*` (AI supervisor config, lessons, analyze, chat, status). |
| `api/websocket.py` | Second `ConnectionManager` class. Not imported anywhere. |
| `backtest/engine.py` | `BacktestEngine`, `SimTrade`, `BacktestResult`. |
| `hud/app.py` | tkinter always-on-top HUD (WebSocket client). |
| `supervisor/memory.py` | `SupervisorMemory`: JSON record store in `supervisor_data/`. |
| `supervisor/live_monitor.py` | `LiveMonitor`: WS client of `/ws/monitor` that persists selected events. |
| `supervisor/connector.py` | `AIConnector`: Anthropic Messages API via urllib. |
| `supervisor/analyzer.py` | `Supervisor.analyze_session / suggest_optimization`, `summarize_trades`, `_parse_report`. |
| `supervisor/trainer.py` | `Trainer`: lessons CRUD/summary. |
| `panel/templates/*.html` | `monitor`, `settings`, `logs`, `hud`, `backtest`, `supervisor` pages. |
| `panel/static/{css,js}` | Mounted at `/static` (directories exist, contents not used by code paths read). |
| `tests/test_structure_rules.py`, `tests/test_position.py` | Pytest suites (15 + 18 tests). |
| `tests/test_scenarios.py` | Scenario harness (no `test_*` functions collected). |
| `tests/test_strategies.py` | Empty (1 line). |
| `tests/debug_scenario3.py`, `debug_scenario5.py` | Ad-hoc scripts that replay hard-coded candles through `StructureBuilder`. |
| `mql5/CaptainPipsStructure.mq5` | **Stale** indicator copy (no paradox zone, no timer, `CP_` object prefix). The deployed one is `CP Structure V11.mq5`. |
| `README.md` | Generic, partly garbled (escape sequences ate characters). |

### Root
| File | Purpose |
|---|---|
| `config.json` | Live config (see §4.4). |
| `Launch_CaptainPips.bat` | Starts bot in a new console, opens panel. |
| `test_zmq.py` | SUB smoke test. |
| `supervisor_data/{api_config,lessons,observations,trades}.json` | Supervisor store (`patterns.json` / `suggestions.json` created on demand). |
| `logs/robot_*.log` | Session logs. |
| `{[e.kind.value` | Stray 33-byte file (accidental shell redirect). |

### MT5 (outside repo)
| File | Purpose |
|---|---|
| `MQL5/Experts/CP EA V3.mq5` | ZMQ bridge: commands, history, candle/account/positions stream, DRAW → GlobalVariables. Header says "EA v2". |
| `MQL5/Indicators/CP Structure V11.mq5` | Reads GlobalVariables every 500 ms and draws `CPR_*` objects. Header says "v5". |
| `MQL5/Experts/Free Robots/CP EA.mq5`, `Captainpips.mq5` | Older EAs that also speak the protocol. Not documented. |

---

## 3. Runtime flow (`main.py`)

### 3.1 Startup — `CaptainPipsBot.__init__` → `start()`
1. `__init__`: for each **enabled config symbol**, creates `builders[sym]`, `paradox[sym]`, `ema[sym]=[]`, `_ema_prev[sym]=None`, `_candle_index[sym]=0`. **Does not create `m2bo[sym]`** (see §14 #1). Creates shared `strategies=[Leg2Chain(), Leg3Drive()]`, `ShiftScorer`, `ZmqConnector`, `RiskManager`, `PositionManager`, `ChartDrawer`, `SupervisorMemory`. Installs `MemoryLogHandler(200)` and a per-session file handler; `_wire_bot_state()` injects config, `robot_running`, log handler and `get_structure` / `get_candles` callables into the API's `BotState`.
2. `start()`:
   1. Migration: if `config.risk.sl_pips` is missing or 0, sets 60/30 and **writes `config.json`**.
   2. `_start_api()`: uvicorn on `app.api_host:api_port` in thread `api-server` (own asyncio loop stored in `self._loop`), then `sleep(1)`.
   3. Thread `zmq-stream` → `_start_stream()` (retry loop around `zmq.listen_stream`, 3 s backoff), then `sleep(0.5)`.
   4. `_backfill()` (blocking, main thread) → §3.3.
   5. Re-sends `on_leg_confirmed` / `on_stop_started` for every segment of every builder.
   6. `while True: sleep(1)` until Ctrl-C → `zmq.close()`.

### 3.2 Threads
| Thread | Started by | Runs |
|---|---|---|
| main | — | `start()`, startup backfill, then idle |
| `api-server` | `_start_api` | uvicorn/FastAPI; sync endpoints run in its thread pool; `_on_config_reload` runs here |
| `zmq-stream` | `start()` | `listen_stream` → `_on_candle_message` → `_process_candle` (live pipeline) |
| `zmq-dispatcher` | `ZmqConnector.__init__` | Reads PULL (5556), routes replies by `req_id` |
| `backfill-{symbol}` | `_on_candle_message` | `_backfill_symbol` when first candle arrives with `_candle_index==0` |
| `supervisor-monitor` | FastAPI `lifespan` → `LiveMonitor.start_background()` | WS client of this same server |

There is **no lock** around `_process_candle` / builders; the stream thread and a backfill thread can touch the same symbol state (see §14).

### 3.3 Backfill — `_backfill()` / `_backfill_symbol(sc, broker_ts)`
- **Broker time**: `_wait_for_broker_time(15 s)` waits for `_broker_time_event`, which is set **only** in `_on_candle_message` (first CANDLE: `_broker_now_ts = data.time`, `_broker_offset = data.time − time.time()`). The `_on_account_message` docstring says it captures broker time; the code does not. On timeout, startup backfill is skipped ("Broker time not available — skipping backfill") and the first live CANDLE triggers a per-symbol backfill thread instead.
- `_backfill_symbol`:
  1. `_ensure_symbol(sym)`; `drawer.on_structure_reset(sym)`.
  2. `to_ts = time.time() + _broker_offset`; `from_ts = _session_ts_from_broker(to_ts, sc.session_start)` (broker clock treated as UTC: `utcfromtimestamp` → replace H:M → `timegm`). Returns early if invalid or `from_ts >= to_ts`.
  3. `ema_from_ts = from_ts − 3600`; `zmq.request_historical(sym, ema_from_ts, to_ts, timeframe=1)` (waits up to `history_timeout_ms`).
  4. `builder.reset(0)`; `_is_backfilling[sym]=True`; bars with `time < from_ts` → `_update_ema` only; others → `_process_candle(sym, raw, broadcast=False)`. `session_end` is **not** applied.
  5. `_is_backfilling=False`; draws every segment; `_backfill_end_time[sym] = last bar time`; draws paradox zone if active; replays `_recent_candles` to WS as `candle` messages.
- `_backfill()` then broadcasts one `structure` snapshot per builder.
- Signals are suppressed for candles with `raw_time <= _backfill_end_time` (in `_tick_watch` and the M2BO block). Structure breaks during backfill take the short path in `_on_structure_break` (§3.6).

### 3.4 Config watching / resync
- No file watcher. The only reload path is the API: `POST /api/config`, `POST /api/symbols`, `DELETE /api/symbols/{name}` → `state.config.save()` → `BotState.notify_config_changed()` → `CaptainPipsBot._on_config_reload(new_config)`:
  1. Replaces `self.config`; `risk.update_config(new.risk)`.
  2. Drops builders/paradox/ema/candle_index for symbols no longer enabled (not `m2bo`, not `active_watch`).
  3. For symbols whose `session_start` changed: fresh `StructureBuilder`, `Paradoxical`, EMA, pops `_candle_index` and `active_watch`, and rotates the session log. `m2bo` is not reset.
  4. Clears `_recent_candles` (all symbols).
  5. If broker time known: `sleep(0.5)` then `_backfill_symbol` for **every** enabled symbol, synchronously, inside the API request.
- `robot_running` is **not** propagated by reload (see §14).
- `POST /api/refresh/{symbol}` only logs "Resync requested" and returns OK; it triggers nothing.

### 3.5 Inbound handlers
- `_on_candle_message(data)`: log → capture broker time on first CANDLE → `_ensure_symbol(symbol)` (exact-case string) → if `_candle_index==0` and not backfilling and a `SymbolConfig` matches (exact case), start a `backfill-{symbol}` thread → `time_str = UTC HH:MM of data.time` → if a `SymbolConfig` exists and `not (session_start <= time_str <= session_end)` (string compare) skip → `_process_candle(..., broadcast=True)`.
- `_on_account_message`: updates `account_balance`, `positions.update_balance`, `_session_start_balance` (first message), `daily_loss_pct = max(0, (start − balance)/start × 100)`; broadcasts `account`.
- `_on_positions_message`: broadcasts `{"type":"positions", **data}`.
- `_on_history_reply`: debug log only (replies are routed by the dispatcher).

### 3.6 Per-candle pipeline — `_process_candle(symbol, raw, broadcast, time_str)` in exact order
1. `time_str` (from arg or UTC of `raw.time`); `index = _candle_index[symbol]++`.
2. **Classifier**: `Candle.from_raw(...)` → `candle_classifier.classify_candle`.
3. Append record to `_recent_candles[symbol]` (deque 100); broadcast `candle` (always, even during backfill — the `broadcast` flag is ignored).
4. `_update_ema(symbol, close)` (EMA-60, `k = 2/61`, seeded with first close).
5. **Paradoxical check**: `paradox[symbol].on_candle(candle)`. If it returns a Signal: take `start = paradox.activation_index or candle.index`, collect `builder.all_candles` with `index >= start`, `builder.reset(start)`, re-`process` them, replace `paradox[symbol]` and `m2bo[symbol]` with fresh instances, `_broadcast_paradox`, pop `active_watch`, broadcast an empty `structure`, retag cache, **return**. No order is placed.
6. **StructureBuilder**: `events = builder.process(candle, ema_values=ema[symbol])`.
7. Build `segments_data` (per segment: kind, direction, n_candles, high, low, `endpoint`=`leg_endpoint`, `leg_start`=`leg_start(seg, preceding STOP)`, num, confirmed); compute `d_beyond_b` when `confirmed_legs >= 2`; broadcast `structure`; `drawer.on_fail_zone(builder, symbol)`; `_retag_recent_candles`.
8. If last segment is a STOP: `shift_scorer.score(stops[-1], segments, ema, builder.all_candles)` → broadcast `shift_score` (with `elements`).
9. **Events** (in order emitted):
   - `STRUCTURE_BROKEN` / `SINGLE_LEG_REVERSED` → `drawer.on_structure_reset`, `_stop_candle_count=0`, `_on_structure_break(...)`, `broke=True`, stop processing further events.
   - `LEG_CONFIRMED` → `drawer.on_leg_confirmed(segments[-2], builder.confirmed_legs, symbol)` → `_on_structure_update`.
   - `STOP_STARTED` → `drawer.on_stop_started(segments[-1], stop_count, symbol)`, record stop size → `_on_structure_update`.
10. If not broke: if last stop grew → `drawer.on_stop_candle`.
11. If not broke and `symbol in m2bo`: `m2bo.on_candle(candle)`; signal and `candle.raw_time > backfill_end` → rebuild Signal with symbol → `_execute`.
12. If not broke and `symbol in active_watch`: `_tick_watch` (strategy `on_candle`; signal after backfill → `_execute`, pop watch; DONE/CANCELLED → pop; mode change → broadcast `watch_update`).
13. **Position manager**: `positions.on_price(symbol, candle.low, candle.high)` — runs **after** step 11/12, so a trade opened on this candle is immediately ticked with this candle's range (§14).
14. If a position existed before step 13 and is gone after: `SupervisorMemory.update_trade(...)` with `_calculate_pnl` (R multiple, not money), then, if not broke and no watch, probe every strategy with synthetic `LEG_CONFIRMED` and `STOP_STARTED` events at the current `confirmed_legs` and arm the first watch returned ("[WATCH ARMED POST-CLOSE]").

### 3.7 `_on_structure_break(symbol, event, candle)`
- Reads `direction = builder.direction`, `segments`, `legs`, `stops`; logs; `drawer.on_structure_reset`.
- **Backfill path**: replay `stops[-1].candles` into a reset builder; paradox `on_break(direction, high/low over segments[1:])` + `set_activation_index`; `m2bo[symbol].on_break(direction, stops, event.confirmed_legs)` + `set_activation_index`; return.
- **Live path**: paradox `on_break` (same zone inputs) + `set_activation_index`; `_broadcast_paradox`; M2BO `on_break` + `set_activation_index`; pop `active_watch`; `shift_scorer.score(stops[-1], segments, ema, _all_candles(segments))` → log + broadcast `shift_score` (no `elements`); replay `stops[-1].candles`; `_broadcast_paradox` again.
- The breaking stop's candles always seed the new structure.

### 3.8 `_on_structure_update(symbol, event, candle)`
1. Return if the symbol has an active trade.
2. If `LEG_CONFIRMED` and `confirmed_legs >= 2` and the current watch is `Leg2Chain` → drop it ("[WATCH REPLACED]").
3. Return if a watch is still active.
4. Loop `self.strategies` (`Leg2Chain`, `Leg3Drive`); first non-None `EntryWatch` becomes `active_watch[symbol]` ("[WATCH ARMED]").

### 3.9 `_execute(signal, symbol, candle)`
`risk.can_trade(symbol, active_count, daily_loss_pct, _bot_state.robot_running)` → if blocked log "[RISK BLOCKED]" and return → `_symbol_config(symbol)` (exact-case match; None → "No symbol config — cannot open trade") → warn on non-alternating segments → `positions.open_trade(signal, sc, account_balance, segments)` → on success: `SupervisorMemory.save_trade({... status:"open"})` and broadcast `watch_update` (DONE, strategy None) → always broadcast `signal` with `executed`.

---

## 4. Communication

### 4.1 ZeroMQ

| Port | EA socket (bind) | Python socket (connect) | Direction | Content |
|---|---|---|---|---|
| 5555 | `PULL tcp://*:5555` | `PUSH` (`SNDTIMEO = send_timeout_ms` 200) | Py → EA | Commands |
| 5556 | `PUSH tcp://*:5556` (SNDHWM 1000, SNDTIMEO 200, LINGER 0) | `PULL` (RCVTIMEO 200, polled by `zmq-dispatcher`) | EA → Py | Replies to commands (incl. HISTORY_REPLY) |
| 5557 | `PUB tcp://*:5557` (SNDHWM 1000, SNDTIMEO 200) | `SUB ""` (RCVTIMEO 5000) | EA → Py | Stream |

The EA runs `OnTimer` every `PollIntervalMs` (100 ms): `CheckForCommands()` reads **at most one** command per tick, then `StreamNewCandles()`. Replies get `,"req_id":"…"` spliced before the last `}` if the request had a `req_id`. JSON is parsed with naive string search (`GetJsonStringValue` / `GetJsonDoubleValue` / `GetJsonObjectValue`).

**Python → EA commands** (all JSON strings on 5555):
| action | Fields | Sent by | EA reply (on 5556) |
|---|---|---|---|
| `OPEN` | `req_id, symbol, order_type ("BUY"/"SELL"), sl, tp, lot, magic` | `ZmqConnector.send_order` from `PositionManager` | `{"status":"success","ticket":<ResultOrder>}` or `{"status":"error","message","retcode"}`. Market order (`price=0`), comment `"cp"`. |
| `CLOSE` | `req_id, symbol, ticket` | `PositionManager._close_ticket` | `{"status":"success"}` or `{"status":"error","message"}` |
| `CLOSE_ALL` | `req_id` | `PositionManager.close_all` (never called) | `{"status":"success"}` — closes **all** account positions |
| `HISTORY` | `req_id, symbol, from_ts, to_ts, timeframe (minutes)` | `ZmqConnector.request_historical` | `{"action":"HISTORY_REPLY","symbol","bars":[{time,open,high,low,close,volume}]}`. Bars whose `time + period > TimeTradeServer()` are excluded. Arrives on 5556, routed by `req_id`. |
| `DRAW` | `command: {...}` (no `req_id`) | `ZmqConnector.send_draw` from `ChartDrawer` | `{"status":...}` without `req_id` → dispatcher drops it ("PULL message has no req_id") |

`send_order` timing: OPEN → timeout `recv_timeout_ms × 2.5 / 1000` = 5 s, up to **3 attempts**, each with a new `req_id` (0.2 s sleep after send timeout, 0.3 s after reply timeout). Other actions: 2 s, 1 attempt. Returns `None` if all fail. A late reply to an earlier `req_id` is discarded, and the EA does not deduplicate (see §14 #3).

**EA → Python stream** (5557, dispatched in `listen_stream` by `type`):
| type | Fields | Frequency |
|---|---|---|
| `CANDLE` | `symbol, time (bar open, broker epoch), open, high, low, close, volume` | Once per newly closed bar (`rates[1]`) per `WatchSymbols` entry, timeframe `CandleTimeframe` (M1) |
| `ACCOUNT` | `balance, equity, open_positions` | Every timer tick (≈ 10/s) |
| `POSITIONS` | `positions: [{ticket, symbol, type BUY/SELL, volume, price_open, sl, tp, price_current, profit, magic}]` (watched symbols only) | Every `PositionsStreamEveryNTicks` (5) ticks |
| `HISTORY_REPLY` | — | Handled if it ever arrives on SUB (`on_history_reply` logs only). `route_external_reply` exists but is never called. |

### 4.2 EA ↔ Indicator GlobalVariables
The EA translates DRAW commands into terminal-wide GlobalVariables. Indicator V11 polls them (timer 500 ms + every tick) and draws. **Names carry no symbol**, so all charts and symbols share one set.

| GlobalVariable | Written by EA on | Meaning / read by V11 |
|---|---|---|
| `CP_LEG_{n}_t1`, `_t2` | `rectangle` with name `CP_LEG_{n}_*` | Rectangle start/end time (epoch) |
| `CP_LEG_{n}_p1`, `_p2` | `rectangle`, `update_rectangle` | Rectangle prices |
| `CP_LEG_{n}_dir` | `rectangle` | `+1` if color `#00FF00`/`#22c55e`, `−1` if `#FF0000`/`#ef4444`, else `0`. V11: `dir >= 0` → bull color. |
| `CP_STOP_{n}_t1/_t2/_p1/_p2/_dir` | same, name `CP_STOP_{n}_*` | Stop rectangle (`dir` is 0 for gray) |
| `CP_COUNT_LEG`, `CP_COUNT_STOP` | `rectangle` (raised to `max(cur, n)`); `delete` sets 0 | How many legs/stops V11 draws |
| `CP_FAILZONE_p1`, `_p2`, `_price`, `_t1`, `_t2`, `_dir`, `_active` | `fail_zone` (`active=1`); `delete`/`clear_failzone` set `active=0` | Fail-zone rectangle; if `p1=p2=0` EA uses `price ± 0.5`; V11 falls back to `price ± tick/2` |
| `CP_ENTRY_t1`, `_t2` (=t1+60), `_price`, `_sl`, `_strategy`, `_active` | `entry` / `entry_close`; `delete` → active 0 | Entry rectangle. Strategy 1 = "Leg2Chain", else "Leg3Drive". **Python never sends `entry`.** |
| `CP_PARADOX_upper`, `_lower`, `_active` | `paradox_zone` (active 1); `clear_paradox_zone` / `delete` → 0 | Two yellow `OBJ_HLINE`s |
| `CP_RESET` | `delete` sets 1; V11 sets 0 after `DeleteAll()` (and on init) | Clear all `CPR_*` objects |

`delete` also deletes every `CP_LEG_*` / `CP_STOP_*` GlobalVariable. It ignores the command's `prefix` and symbol. `arrow` draws an `OBJ_ARROW_DOWN` directly (no GV).

Indicator V11 object names: `CPR_LEG_{n}`, `CPR_STOP_{n}` (+ `_lbl` text labels "L{n}"/"S{n}"), `CPR_ENTRY`, `CPR_FAILZONE` (orange-red, width 2, unfilled), `CPR_PARADOX_UPPER`, `CPR_PARADOX_LOWER`.

### 4.3 Panel / HUD

**WebSocket `/ws/monitor`** (`api/main.py:ws_monitor`): on connect sends `{"type":"hello","session_start":<first enabled symbol>}`; then the server only reads text (keep-alive). Bot messages are pushed by `CaptainPipsBot._broadcast` → `asyncio.run_coroutine_threadsafe(_manager.broadcast(payload), self._loop)` (skipped until the API loop exists).

| type | Emitted in | Fields |
|---|---|---|
| `hello` | `ws_monitor` | `session_start` |
| `candle` | `_process_candle` step 3; backfill replay | `symbol, index, time, label, direction, open, high, low, close` |
| `structure` | `_process_candle` step 7 | `symbol, confirmed_legs, direction, segments[{kind, direction, n_candles, high, low, endpoint, leg_start, num, confirmed}], candles_processed, d_beyond_b, active_targets, paradox_active` |
| `structure` | `_backfill` snapshot | same but `segments` is an **int** (count), no `d_beyond_b` |
| `structure` | paradox reset | `confirmed_legs:0, direction:null, segments:[], candles_processed, active_targets:[], paradox_active:false` |
| `shift_score` | step 8 | `symbol, true_count, applicable_count, confirmed, elements{a..i}` |
| `shift_score` | `_on_structure_break` (live) | `symbol, true_count, applicable_count, confirmed` |
| `watch_update` | `_tick_watch` on mode change; `_execute` on open | `symbol, strategy, mode, active_targets` |
| `signal` | `_execute` | `symbol, strategy, direction, entry_price, reason, executed` |
| `account` | `_on_account_message` | `balance, equity, open_positions` |
| `positions` | `_on_positions_message` | EA `POSITIONS` payload (`type` overwritten to `positions`) |
| `paradox` | `_broadcast_paradox` | `symbol, active, upper, lower, breaks[direction values]` |
| `ROBOT_STATE` | `/api/robot/start|stop` | `running` |

`active_targets` (`_compute_active_targets`): `["St"]` if `confirmed_legs >= 1`, plus `"F"` if the watch mode is exactly `"B"`, else `"B"` whenever any watch exists.

Consumers:
- `monitor.html` (lowercases `type`): `structure, signal, shift_score, robot_state, positions, paradox, candle, account`; fetches `/api/structure`, `/api/candles`, polls `/api/status` every 5 s, `POST /api/hud/launch`.
- `hud.html`: `structure, signal, paradox, candle, account`; fetches `/api/structure`, `/api/config`.
- `hud/app.py`: `structure, signal, account, candle, watch_update, config/session`; reads `session_start` from any message; fetches `/api/structure`, `/api/config`. Shows only one symbol (last iterated).
- `settings.html`: `GET/POST /api/config`, `POST /api/candles/clear`.
- `logs.html`: `GET /api/logs`. `backtest.html`: `POST /api/backtest/run`. `supervisor.html`: `/api/supervisor/*`.
- `LiveMonitor`: persists `trade`/`trade_closed` → trades; `structure` with `event=="STRUCTURE_BROKEN"` and `signal, shift_score, trade, trade_closed, trade_opened` → observations.

**HTTP endpoints**
| Method & path | Handler | Behaviour |
|---|---|---|
| `GET /`, `/settings`, `/logs`, `/hud`, `/backtest`, `/supervisor` | `api/main.py` | Serve templates (relative paths) |
| `POST /api/hud/launch` | `launch_hud` | `subprocess.Popen([python, hud/app.py])` |
| `GET /api/config` | `get_config` | `config.model_dump()` + `session_start` of first enabled symbol |
| `POST /api/config` | `update_config` | Replace + save + notify |
| `GET /api/symbols`; `POST /api/symbols`; `DELETE /api/symbols/{name}` | | List; upsert by exact `symbol`; delete (404 if missing). Save + notify. |
| `GET /api/status` | `get_status` | `robot_running`, `active_trade_count/active_symbols` from `BotState.active_trades` (never populated → always 0/[]), `connected_clients` |
| `POST /api/robot/start`, `/api/robot/stop` | | Set `BotState.robot_running` + `config.risk.robot_running`, save, broadcast `ROBOT_STATE`. No page calls these. |
| `POST /api/refresh/{symbol}` | | Log only |
| `POST /api/backtest/run` | `api/backtest.py` router (registered first, wins) | Multipart `file` (tab-separated MT5 export: date, time, o, h, l, c, vol, x, y) + `symbol` → engine result JSON |
| `POST /api/backtest/run` (app-level), `GET /api/backtest/list`, `GET /api/backtest/{run_id}` | `api/main.py` | Shadowed / operate on `BotState.backtest_runs`, which nothing fills |
| `GET /api/candles?symbol=` | | `_get_recent_candles` (up to 100, with `seg` tag L{n}/S{n}) |
| `POST /api/candles/clear` | | Returns OK only |
| `GET /api/structure` | | `{"symbols": _get_structure_state()}` |
| `GET /api/logs` | | Last 200 log records |
| `GET/POST /api/supervisor/config` | | Provider/model/base_url; key write-only (`key_set`, `key_from_env`) |
| `GET /api/supervisor/lessons`, `POST /api/supervisor/lesson`, `POST/DELETE /api/supervisor/lesson/{id}` | | Lessons CRUD |
| `POST /api/supervisor/analyze` (`mode: session|optimize`) | | AI analysis |
| `POST /api/supervisor/suggestion/{id}/{apply|reject}` | | Mark suggestion (never edits config) |
| `GET /api/supervisor/status`, `POST /api/supervisor/chat` | | Monitor status; chat with lessons + today's trades |

CORS allows `http://localhost:8000` and `http://127.0.0.1:8000`. `/static` mounts `captainpips/panel/static`.

### 4.4 Config files

**`config.json`** (`config.py`, Pydantic; extra keys ignored)
| Key | Type / default | Current | Read where |
|---|---|---|---|
| `zmq.push_port / pull_port / sub_port` | int 5555/5556/5557 | same | `ZmqConnector.__init__` |
| `zmq.history_timeout_ms` | int 30000 | 30000 | `request_historical` |
| `zmq.send_timeout_ms` | int 200 | 200 | PUSH `SNDTIMEO` |
| `zmq.recv_timeout_ms` | int 2000 | 2000 | `send_order` (×2.5 for OPEN) |
| `symbols[].symbol` | str | `"xauusd@"` | everywhere, **exact-case** match |
| `symbols[].enabled` | bool true | true | `enabled_symbols()` |
| `symbols[].session_start / session_end` | "HH:MM" 16:30/18:30 | 10:51 / 23:00 | backfill start, `_in_session`, `/api/config`, WS hello, backtest window |
| `symbols[].sl_pips / tp_pips` | float 60/30 | 60/30 | Not read (fixed SL/TP use `risk.sl.fixed_pips` / `risk.tp.fixed_pips`) |
| `symbols[].pip_value` | float 1.0 | 1.0 | fixed SL/TP distance, pos2 SL offset |
| `symbols[].tick_size / tick_value / min_lot / lot_step / max_lot` | floats | 1/1/0.01/0.01/50 | `PositionSizer`, backtest P&L |
| `risk.risk_pct` | float 1.0 | 1.0 | sizing (pos1, pos2 live, re-entry) |
| `risk.max_open_positions` | int 5 | 5 | `RiskManager` |
| `risk.max_daily_loss_pct` | float 5.0 | 5.0 | `RiskManager` |
| `risk.robot_running` | bool | true | `_wire_bot_state` at startup; `/api/robot/*`; settings page |
| `risk.sl_pips / tp_pips` | float 60/30 | 60/30 | Legacy; only the startup migration |
| `risk.sl.type` | "fixed"/"structural" | structural | `_sl_price` |
| `risk.sl.fixed_pips` | float 60 | 60 | `_sl_price` |
| `risk.sl.structural_leg2chain` | "leg1_extreme"/"stop1_extreme" | leg1_extreme | `_sl_price` when strategy contains "Leg2Chain" |
| `risk.sl.structural_leg3drive` | same | stop1_extreme | `_sl_price` for every other strategy |
| `risk.tp.type` | "fixed"/"r_based" | r_based | `_tp_price` |
| `risk.tp.fixed_pips / r_multiplier` | 30 / 0.5 | 30 / 0.5 | `_tp_price` |
| `risk.position2.enabled` | bool | true | Backtest only |
| `risk.position2.risk_pct` | float | 1.0 | Backtest only |
| `risk.position2.sl_pips` | float (0 = BE) | 0.0 | `PositionManager._on_pos2_added` |
| `risk.position2.tp_r` | float (0, 2] | 0.5 | Validated only; not passed to the state machine |
| `risk.position2.entry_r` | float | 0.25 | Not read (state machine hard-codes 0.25) |
| `position.position1_multiplier / position2_multiplier / reentry_multiplier` | 0.5 / 3.0 / 0.5 | same | sizing |
| `position.enable_pos2_loss_floor` | bool | false | `TradeStateMachine` |
| `position.direct_fail_exit` | bool | true | `TradeStateMachine` |
| `app.api_port / api_host / log_level` | 8000 / 127.0.0.1 / INFO | same | uvicorn |
| `app.db_path` | str | captainpips.db | Not read |

**`supervisor_data/api_config.json`**: `provider` (only `anthropic` works), `model`, `base_url` (default `https://api.anthropic.com`), `api_key` (falls back to `ANTHROPIC_API_KEY`), optional `max_tokens` (4096), `timeout_s` (120). Read by `AIConnector.from_file` and the supervisor config routes. The current file contains a plaintext key and a non-default `base_url`; the folder is untracked and **not** in `.gitignore`.

---

## 5. Candle classifier

`connectors/candle_classifier.py::classify_candle(open, high, low, close)` — port of `CandleClassifier-V1.mq5` (decision tree v9; "DO NOT MODIFY").

- Features (percent of range `rng = high − low`): `body = |c−o|/rng·100`, `upper`, `lower` shadows, `bull = close >= open`, `dom = max(upper, lower)`, `counter = min(upper, lower)`, `ratio = dom/(counter+0.1)`, `p_dir = (bull and lower>upper) or (not bull and upper>lower)`, `r_dir = not p_dir`.
- `rng <= 0` → `("J","NONE")`. `body < 0.5` → `J`. Otherwise `_classify_tree` (thresholds on body 69.03 / 49.56 / …, dom 39.21 / 25.87 / …, counter 26.01 …) returns one of **C N B P J R L S WR WB**. `D` exists in `CandleLabel` but is never produced.
- Direction: `"NONE"` if label J, else `"BULL"` if `close >= open` else `"BEAR"`.

`Candle` (`definitions.py`, frozen): `index, time (HH:MM), raw_time, label, direction, OHLC`.
- `structure_direction`: for **P/R**, direction from the longer shadow (lower > upper → BULL, upper > lower → BEAR, equal → NONE); all other labels use `direction`. Used by the structure engine and shift element b.
- `close_direction`: close vs open only.

| Set (`definitions.py`) | Labels | Used by |
|---|---|---|
| `ALWAYS_STOP_LABELS` | S, L, D, J | `LegValidator.no_always_stop`, builder (never part of a leg) |
| `FRESH_STOP_LABELS` | S, D, J | `_handle_fresh` first branch. It is a subset of ALWAYS_STOP, so the ALWAYS_STOP branch in `_handle_fresh` is only reached by **L**. |
| `BASE_LABELS` | C, N, B | Defined only; `has_valid_base` uses its own sets |
| `PARTICIPANT_LABELS` | P, R, WR, WB | Defined only |
| `shift_scorer._SKIP_LABELS` | S, L, D, J | Element b |

---

## 6. Structure engine

### 6.1 `StructureBuilder` (`core/structure_state.py`)
State: `segments`, `candidate` (candles not yet promoted), `all_candles` (append-only, **never cleared**, also receives replayed candles again), `direction`, `confirmed_legs`, `start_index`.

`process(candle, ema_values)`: append to `all_candles` → dispatch on state → `_check_breaks(ema_values)` → return events.

| State | Condition | Handler |
|---|---|---|
| Fresh | `segments == []` | `_handle_fresh` |
| Leg active | `segments[-1].kind == LEG` | `_handle_leg_active` — **unreachable**: segments are only appended by `_promote_and_stop` (LEG then STOP) and by this handler itself, so the last segment is always a STOP |
| Stop active | otherwise | `_handle_stop_active` |

**`_handle_fresh(candle)`**
1. Label in FRESH_STOP (S/D/J): if `candidate` and `is_valid_leg(candidate)` → `_promote_and_stop(candle)`; else clear candidate, `direction=None`, return [].
2. Label in ALWAYS_STOP (only L reaches here): same, with a debug log.
3. If `direction is None`: `direction = candle.structure_direction`, `start_index = index`.
4. Same `structure_direction` as `direction` → append to candidate.
5. Otherwise: if valid candidate → promote; else clear, start a new candidate with this candle (direction = its `structure_direction`).

**`_handle_stop_active(candle)`** (last segment is the current stop)
1. ALWAYS_STOP label: valid candidate → promote; else append candle to stop, clear candidate.
2. `structure_direction == direction` → append to candidate (the stop does not grow).
3. Opposite/NONE: valid candidate → promote; else append to stop, clear candidate.

Same-direction candles after a stop always form a **new candidate leg**; an existing leg is never extended.

**`_promote_and_stop(trigger)`**
- Staircase pre-check if a previous leg exists: `leg_endpoint(candidate) > leg_endpoint(prev_leg)` (BULL) / `<` (BEAR). On failure the trigger is appended to the current stop and **nothing** is emitted (the candidate is kept).
- Append LEG(candidate), clear candidate, `confirmed_legs = LegCounter.count(segments)`, append STOP([trigger]).
- Emits `[LEG_CONFIRMED(leg, confirmed_legs), STOP_STARTED(stop, confirmed_legs)]`. `LEG_CONFIRMED` is emitted even when `LegCounter` does not raise `confirmed_legs`.

**`_can_promote`** does **not** exist yet (pending prompt).

**`_check_breaks(ema_values)`** (after every candle, only if a STOP exists):
1. `confirmed_legs == 1`: `cond1 = BreakDetector.is_single_leg_reverse(legs[0], stops[-1], stops[0] if len(stops)>=2 else None)`; `cond2 = ShiftScorer.score(stops[-1], segments, ema_values, all_candles).true_count >= 6` (only if `ema_values` given). Either → `SINGLE_LEG_REVERSED(stops[-1])`.
2. `len(stops) >= 2` and `is_geometric_break(stops)` → `STRUCTURE_BROKEN(stops[-1])`.
3. If not already broken, `len(stops) >= 2` and a candidate exists: any candidate candle with `low < stop_breach(stops[-2])` (BULL) / `high > …` (BEAR) → `STRUCTURE_BROKEN(stops[-2])`.

`reset(from_candle_index)` clears segments, candidate, direction, confirmed_legs (not `all_candles`). `replay(candles)` exists but is unused.

### 6.2 Events
| EventKind | Fires when | main.py reaction |
|---|---|---|
| `LEG_CONFIRMED` | Every successful `_promote_and_stop` | draw leg, `_on_structure_update` |
| `STOP_STARTED` | Same call (stop opened with trigger); `_handle_leg_active` (unreachable) | draw stop, `_on_structure_update` |
| `SINGLE_LEG_REVERSED` | §6.1 `_check_breaks` 1 | `_on_structure_break` |
| `STRUCTURE_BROKEN` | §6.1 `_check_breaks` 2 or 3 | `_on_structure_break` |

### 6.3 `LegValidator` (`structure_rules.py`)
| Method | Rule |
|---|---|
| `has_valid_base` | ≥ 1 candle labelled C or N, **or** ≥ 2 labelled B |
| `no_always_stop` | No S/L/D/J |
| `all_same_direction` | Non-empty and every `structure_direction` equals the first |
| `is_valid_leg` | All three |

### 6.4 `StructurePoints`
| Method | BULL | BEAR |
|---|---|---|
| `leg_start(leg, prev_stop=None)` (A/C/E) | `leg.candles[0].low`, folded with `min(…, prev_stop.low)` | `leg.candles[0].high`, `max(…, prev_stop.high)` |
| `leg_endpoint(leg)` (B/D) | max high | min low |
| `stop_breach(stop)` | `stop.low` | `stop.high` (by `stop.direction`, i.e. structure direction) |

### 6.5 `LegCounter.count(segments)`
Leg 1 always counts. For leg *i* ≥ 2 (stops[i−1] between): confirmed if **staircase** (`end_i > end_{i−1}` BULL / `<` BEAR) **and nested** (`between_stop.low >= leg_start(prev_leg, stops[i−2])` BULL / `between_stop.high <= …` BEAR). Stops at the first failure.

### 6.6 `BreakDetector`
- `is_single_leg_reverse(leg, stop, prev_stop)`: BULL → `stop.low < min(prev_stop.low, leg_start(leg))`; BEAR → `stop.high > max(prev_stop.high, leg_start(leg))`.
- `is_geometric_break(stops)`: BULL → `stop_breach(stops[-1]) < stop_breach(stops[-2])`; BEAR → `>`.

---

## 7. Legs Shift — `core/shift_scorer.py`

`ShiftScorer.score(breaking_stop, segments, ema_values, all_candles)`. The **check direction** is the opposite of `breaking_stop.direction` (stops carry the structure direction): BEAR structure → check BULL.

| El. | Field | Exact condition (check = BULL; BEAR mirrored) |
|---|---|---|
| a | `a_outbreak` | Some stop candle *i ≥ 1* has `high[i] > high[i−1]` |
| b | `b_double` | In `all_candles` within `[stop.first.raw_time, stop.last.raw_time]`, excluding S/L/D/J, the `structure_direction` sequence contains the **subsequence** BEAR → BULL → BEAR (check BEAR: BULL → BEAR → BULL). Needs ≥ 3 candles. |
| c | `c_structure_break` | Last *other* stop exists and `stop.high > prev.high` (BEAR: `stop.low < prev.low`) |
| d | `d_ema60` | Map `candle.index → ema` by `zip(all_candles, ema_values)`; within stop candles, close switches side of EMA at least once |
| e | `e_followthrough` | After the first a-type BO at index *k*, one of the next 2 stop candles closes in check direction (`close > open`) |
| f | `f_closegap` | Some stop candle `close[i] > high[i−1]` (BEAR: `close < low[i−1]`) |
| g | `g_size` | `(stop.high − stop.low) >= 1.5 × mean range of all other stops` (needs ≥ 1 other stop) |
| h | `h_gapfill` | In the last leg, a gap between consecutive candles (or candles *i* and *i+2*) in the leg direction, and the stop reaches back into it (`stop.low <= prev.high` for an up-gap; mirrored) |
| i | `i_3cgap` | In `all_candles` within the stop's time range, some triple with `c0.high < c2.low` (BEAR: `c0.low > c2.high`) |

- `true_count` = number of True elements (all 9 always counted).
- `applicable_count` = 9, or 8 when `len(legs) <= 1` (`c_applicable=False`). It only affects display; **c is still computed and counted**.
- `confirmed = true_count >= 6`. There is **no mandatory element** in the code.
- Uses: per-candle HUD broadcast (step 8), break log/broadcast, and `SINGLE_LEG_REVERSED` cond2 (where the comment says "C excluded" but c is included).

---

## 8. Strategies

### 8.1 Common watch mechanics (`Leg2Chain`, `Leg3Drive`)
`EntryWatch(strategy, leg_number, mode, trigger_price=0.0, trigger_direction, pending_bo_index, prev_candle)`. `prev_candle` is set to every candle seen by `on_candle`.

| Mode | Behaviour on each candle |
|---|---|
| `WAIT_CORRECTION` | If the target stop has a **correction** (some candle closes below the previous candle's low for a BULL leg / above the previous high for BEAR) → `MONITOR`, counter 0 |
| `MONITOR` / `MONITOR_BEAR` | counter += 1; `is_bo = close > prev.high` (BEAR: `< prev.low`); `is_opp = close < open` (BEAR: `>`). `is_opp` → `MONITOR_BEAR`. `is_bo` without opposite flag → **Mode A signal**, `DONE`. `is_bo` with flag → `B_POST_BO`, counter 0. No BO and counter ≥ 2 → `B` if flagged else `DONE` (no entry). |
| `B` | Waits **indefinitely** for `is_bo` → `B_POST_BO` |
| `B_POST_BO` | counter += 1; FT (`close > open`, BEAR `<`) → **Mode B signal**, `DONE`; counter ≥ 2 → back to `B` |

Neither strategy sets `CANCELLED`. A watch ends only by signal, `DONE`, structure break, paradox reset, or Leg2Chain → Leg3Drive replacement. Direction = direction of the **first** LEG in `segments`.

### 8.2 Strategy table
| | Leg2Chain | Leg3Drive | Paradoxical | M2BO | M2 (`M2Strategy`) |
|---|---|---|---|---|---|
| File | `leg2chain.py` | `leg3drive.py` | `paradoxical.py` | `M2Bo.py` | `M2.py` |
| Type | `BaseStrategy` | `BaseStrategy` | own API | own API | BaseStrategy-shaped, own `M2Watch` |
| Activation gate | `STOP_STARTED` and `confirmed_legs == 1` | `STOP_STARTED`, `confirmed_legs == 2`, `_d_beyond_b` (leg2 endpoint beyond leg1 endpoint) | `on_break` ×3 with strictly alternating directions; zone `upper = max(high of BEAR breaks in last 2)`, `lower = min(low of BULL breaks in last 2)`, armed only if `upper > lower` | `on_break(direction, stops, confirmed_legs)` with `confirmed_legs >= 3` and ≥ 2 stops | `STOP_STARTED` and `confirmed_legs == 2` |
| Correction target | Stop 1 (`stops[0]`) | Stop 2 (`stops[1]`) | — | — | — |
| States | §8.1 | §8.1 | `active`, `upper`, `lower`, `activation_index`, `break_history` (last 3) | `active`, `entry_direction`, `key_level`, `sl_level`, `activation_index` | `WATCHING` → `DONE` / `CANCELLED`; `conditions_met`, frozen `key_level` |
| Entry trigger | Mode A / Mode B (§8.1) | same | `high > upper` → BULL, `low < lower` → BEAR (skips activation candle) | Close beyond key level: broken BEAR → BUY if `close > stop2.high`; broken BULL → SELL if `close < stop2.low` (skips activation candle) | After `stop2.range >= 0.5×stop1.range` **and** `stop2.n >= 0.5×stop1.n` (freezes key = stop2.high BULL / low BEAR): BULL `high >= key`, BEAR `low <= key` |
| Entry price | `candle.close` | `candle.close` | `candle.high` / `candle.low` | `candle.close` | `key_level` |
| SL | `_sl_price`: structural `leg1_extreme` (legs[0].low/high) per config | structural `stop1_extreme` (stops[0].low/high) per config | (n/a, not traded) | `sl_override` = breaking (last) stop low (BUY) / high (SELL) | `sl_override` = stop1.low (BULL) / stop1.high (BEAR) |
| TP | `_tp_price` (r_based 0.5 R → EA TP) + state machine | same | — | same | same |
| Cancel | §8.1 | §8.1 | New `on_break` resets the zone | New `on_break`; one-shot after trigger | Structure no longer has exactly 2 legs / ≥ 2 stops → `CANCELLED` |
| `leg_number` | 1 | 3 | 0 | 0 | 2 |

### 8.3 How `main.py` wires them
- **Leg2Chain / Leg3Drive**: `self.strategies` order `[Leg2Chain, Leg3Drive]`, single `active_watch[symbol]` slot, armed in `_on_structure_update`, ticked in `_tick_watch`, rebuilt into a symbol-stamped `Signal` (no `sl_override`), executed only after `backfill_end`.
- **Paradoxical**: `on_break` in `_on_structure_break` with `direction = builder.direction` and high/low over `segments[1:]` (excludes leg 1); `set_activation_index(break candle)`. `on_candle` runs **first** every candle. Its Signal is **not traded**: it triggers a structure reset/replay from `activation_index` and resets paradox + M2BO (§3.6 step 5). Zone drawn via `_broadcast_paradox` → `on_paradox_zone` / `on_paradox_clear`.
- **M2BO**: `self.m2bo[symbol]` — created only in `_ensure_symbol` (new symbols) and on paradox reset. `on_break` in both break paths; `on_candle` every candle after events (step 11), independent of `active_watch`; executes via `_execute` with `sl_override`.
- **M2Strategy**: imported nowhere in `main.py`. Not active.

### 8.4 Handoff / priority rules
1. Paradoxical zone break pre-empts everything on that candle (return before structure processing).
2. A structure break clears `active_watch` and re-arms paradox/M2BO.
3. Only one `active_watch` per symbol; first strategy in list order wins.
4. On `LEG_CONFIRMED` with `confirmed_legs >= 2`, a Leg2Chain watch is dropped so Leg3Drive can arm on the following `STOP_STARTED`.
5. No arming while the symbol has an active trade. After a trade closes, strategies are re-probed with synthetic events.
6. M2BO runs in parallel with any watch; `PositionManager.open_trade` rejects any second trade on the same symbol, so whichever signal executes first wins.
7. Every execution passes `RiskManager`.

---

## 9. Position management

### 9.1 Sizing — `PositionSizer.calculate`
`risk_amount = balance × risk_pct/100`; `ticks = sl_distance / tick_size`; `base = risk_amount / (ticks × tick_value)`; `lot = floor(base × multiplier / lot_step) × lot_step` (rounded to step decimals); `< min_lot` → 0.0 (trade skipped); capped at `max_lot`. `sl_distance <= 0` raises `ValueError`.

### 9.2 SL / TP (`position/manager.py`)
- `_sl_price`: `signal.sl_override` wins → else `risk.sl.type == "structural"` with segments: key per strategy (`"Leg2Chain" in strategy` → `structural_leg2chain`, else `structural_leg3drive`); `leg1_extreme` → `legs[0].low` (BULL) / `.high`; `stop1_extreme` → `stops[0].low/.high`; missing data → warn and fall through → fixed `entry ∓ fixed_pips × pip_value`.
- `_tp_price`: `r_based` → `sl_distance × r_multiplier`; `fixed` → `fixed_pips × pip_value`.

### 9.3 Orders
| Position | Trigger | Lot multiplier | SL | TP |
|---|---|---|---|---|
| Pos 1 | `open_trade` | `position1_multiplier` (0.5) | `_sl_price` | `_tp_price` |
| Pos 2 | `POSITION2_ADDED` | `position2_multiplier` (3.0) on pos 1's SL distance and `risk.risk_pct`, with cached balance | entry (if `position2.sl_pips == 0`) else entry ∓ `sl_pips × pip_value` | 0 |
| Re-entry | `REENTRY_TRIGGERED` | `reentry_multiplier` (0.5) | `main_sl_price` | 0 |

All three share the trade's `magic` (`88000 + counter`). Opening needs `reply.status == "success"`. Closing (`_close_ticket`) checks `reply.status == "OK"` — the EA answers `"success"`, so every close is logged as "EA close failed" (§14 #6); the ticket is cleared either way.

### 9.4 `TradeStateMachine` (`position/state_machine.py`)
`R(price) = (price − entry)/risk` for BULL (mirrored for BEAR), `risk = |entry − main_sl|`. `fav` / `adv` use the bar's high/low.

| Constant | Value |
|---|---|
| `_R_ADD_POS2` | +0.25 |
| `_R_CLOSE_POS2_BE` | 0.00 |
| `_R_TP` | +0.50 |
| `_R_NEGATIVE` | −0.25 |
| `_R_FULL_FAIL` | −0.50 |
| `_R_SL_POS1_ALONE` | −1.00 |

| State | Order | Transitions → events |
|---|---|---|
| `INITIAL_MONITORING` | adverse first | `fav > 0` sets `touched_positive`. `adv <= −0.5` → `FULL_FAIL_EXIT` and **FINISHED** if `direct_fail_exit` and never positive, else `FAILED_WAIT_REENTRY`. `adv <= −0.25` sets `adverse_before_pos2`. `fav >= 0.5` → `POSITION1_CLOSE_TP`, FINISHED. `fav >= 0.25` and no prior adverse → `POSITION2_ADDED`, `POS2_ACTIVE`. Else if adverse flag → `NEGATIVE_WATCH`. |
| `POS2_ACTIVE` | favorable first | `fav >= pos2_tp_r` (0.5, config not passed) → `POSITION1_CLOSE_TP` (closes pos1 + pos2), FINISHED. `enable_pos2_loss_floor` and `adv <= −0.5` → `FULL_FAIL_EXIT`, `FAILED_WAIT_REENTRY`. `adv <= 0` → `POSITION2_CLOSE_BE`, `POS1_ONLY_POST_BE`. |
| `POS1_ONLY_POST_BE` | favorable first | `fav >= 0.5` → TP, FINISHED; `adv <= −1` → `FULL_FAIL_EXIT`, `FAILED_WAIT_REENTRY` |
| `NEGATIVE_WATCH` | adverse first | `adv <= −0.5` → `FULL_FAIL_EXIT` (FINISHED or `FAILED_WAIT_REENTRY` as in INITIAL); `fav >= 0.5` → TP, FINISHED |
| `FAILED_WAIT_REENTRY` | — | `fav >= 0` (price back to entry) → `REENTRY_TRIGGERED`, `REENTRY_ACTIVE`. Waits indefinitely. |
| `REENTRY_ACTIVE` | favorable first | `fav >= 0.5` → `REENTRY_CLOSE_TP`; `adv <= −1` → `REENTRY_CLOSE_SL`; both FINISHED |

`trigger_reentry()` exists but is never called. **Direct fail exit** = `direct_fail_exit=True` and price never went above entry → the −0.5 R exit finishes the trade without re-entry.

Event handlers (`PositionManager._dispatch`): `POSITION2_ADDED` → open pos 2; `POSITION2_CLOSE_BE` → close pos 2; `POSITION1_CLOSE_TP` → close pos 1 and pos 2; `FULL_FAIL_EXIT` → close pos 1 and pos 2; `REENTRY_TRIGGERED` → open re-entry; `REENTRY_CLOSE_TP/SL` → close re-entry. The trade is removed from `_trades` when FINISHED. All exits are Python-initiated CLOSE orders at bar close, except broker-side SL/TP hits.

---

## 10. Risk manager — `risk/risk_manager.py`
`can_trade(symbol, active_trade_count, daily_loss_pct, robot_running)`, first failure wins:
1. `not robot_running` → "Robot is paused" (value is `BotState.robot_running`).
2. `active_trade_count >= max_open_positions` → "Max positions reached" (count = symbols with an `ActiveTrade`, including ones waiting for re-entry).
3. `daily_loss_pct >= max_daily_loss_pct` → "Daily loss limit hit" (balance drop since the first ACCOUNT message of the process; never reset daily; ignores equity).

Per-symbol one-trade limit is enforced separately in `PositionManager.open_trade`. `symbol` is unused.

---

## 11. Chart drawing — `chart/drawer.py`

All commands go through `ZmqConnector.send_draw` (fire-and-forget DRAW).

| Method | Command | Name | Prices / times |
|---|---|---|---|
| `on_leg_confirmed(leg, number, symbol)` | `rectangle` | `CP_LEG_{number}_{symbol}` | `time1 = first.raw_time`, `time2 = last.raw_time+60`, `price1 = leg.high`, `price2 = leg.low`, color `#00FF00`/`#FF0000`, label |
| `on_stop_started(stop, number, symbol)` | `rectangle` | `CP_STOP_{number}_{symbol}` | high/low, `#808080` |
| `on_stop_candle(stop, number, symbol)` | `update_rectangle` | same | `time2`, high/low |
| `on_structure_reset(symbol)` | `delete` (`prefix: "CP_"`) | — | EA wipes all leg/stop GVs and sets `CP_RESET` |
| `on_double_detected(candle, symbol)` | `arrow` | `CP_DBL_{raw_time}` | Never called |
| `on_fail_zone(builder, symbol)` | `fail_zone` | `CP_FAILZONE_{symbol}` | 1 leg: `leg_start(legs[0])`; else `leg_start(legs[-1], stops[-2] or stops[-1])`. `price = price1 = price2 = fail`; `time1` = first candle, `time2` = last+60+7200; `direction`. Sent **every candle** (step 7). |
| `on_paradox_zone(upper, lower, symbol)` | `paradox_zone` | `CP_PARADOX_{symbol}` | upper/lower |
| `on_paradox_clear(symbol)` | `clear_paradox_zone` | same | — |

The EA parses the name with `StringSplit(name,'_')` and uses only `parts[1]` (LEG/STOP) and `parts[2]` (number); the symbol suffix is dropped. Leg numbers come from `builder.confirmed_legs`, stop numbers from the stop count.

What the indicator draws (V11): leg rectangles (green/red, filled by default) labelled L{n}; stop rectangles (gray, dotted fill off) labelled S{n}; entry rectangle (never activated); fail zone as an orange-red unfilled rectangle; paradox zone as two yellow horizontal lines.

---

## 12. Backtest engine — `backtest/engine.py`

Entry point: `POST /api/backtest/run` → `BacktestEngine(Config.load()).run(candles, symbol, from_time=session_start, to_time=session_end)` of the first enabled symbol. Starting balance 10 000.

| Aspect | Live (`main.py`) | Backtest |
|---|---|---|
| Time | Broker epoch, formatted as UTC | `"%Y.%m.%d %H:%M:%S"` parsed as **naive local time** (`dt.timestamp()`); session filter on string `time[11:16]` |
| EMA warm-up | 60 min before session | All CSV rows before `from_time` |
| Order of steps | classify → EMA → paradox → structure → events → M2BO → watch → **positions** | classify → EMA → **positions** → paradox → structure → events → watch |
| Paradox reset | Replay from `activation_index`, reset paradox + M2BO, return | `builder.reset(candle.index)` and process **only the current candle**; reset paradox |
| Paradox zone input | `segments[1:]` | **all** segments |
| M2BO / M2 | M2BO wired | Neither |
| Watch arming gate | no active trade | no open simulated trade; same Leg2Chain → Leg3Drive replacement |
| Post-close re-arm probe | Yes | No |
| Risk manager | Yes | No |
| SL/TP | `_sl_price` / `_tp_price` | Same functions |
| Pos 2 size | `risk.risk_pct` × `position2_multiplier` | `position2.risk_pct` × `position2_multiplier`, only if `position2.enabled` |
| Exits | Real orders; broker SL/TP can hit first | Fixed R prices: TP +0.5 R, fail −0.5 R (−1 R from `POS1_ONLY_POST_BE`), BE for pos 2, re-entry ±. No spread/slippage. |
| P&L | — | `price_diff / tick_size × tick_value × lot` |
| Equity curve | — | Unrealised P&L uses **EMA** as the "last close" |
| End of data | — | Open trade closed at last close (`end_of_data`) |

`BacktestResult`: `total_trades, winning_trades, losing_trades, win_rate, total_pnl, max_drawdown (% from equity curve), profit_factor, by_strategy (position1/position2/all), summary()`.

---

## 13. Logging

- **Files**: `logs/robot_YYYYMMDD_HHMMSS.log` (path `captainpips/../logs`), created in `__init__` and on `session_start` change (`_rotate_session_log`); keeps the newest 10. File level DEBUG, format `%(asctime)s  %(name)-30s %(levelname)-6s %(message)s`. Console (`__main__`) `basicConfig(DEBUG)`. In-memory last 200 records → `/api/logs`.
- **Volume**: INFO lines for every `Broadcast:` call, every `Stream message received` (ACCOUNT ≈ 10/s), every `CANDLE received`, and every `_send_draw`. DEBUG adds every ZMQ poll and SUB timeout.

| Grep for | Meaning |
|---|---|
| `Backfill range:` / `Backfilling` / `Got N bars` / `Backfill complete` / `Backfill end time` | Backfill progress and the signal cut-off timestamp |
| `Broker time not available` / `Broker time established` | Startup backfill skipped or run |
| `Events for` | Events emitted per candle |
| `Direction changed:` | Builder direction flips |
| `Structure break:` / `Replay:` / `Shift score:` | Break handling |
| `Paradoxical boundary crossed` / `on_paradox_zone` | Paradox reset / zone drawn |
| `M2BO armed` / `M2BO BUY triggered` / `M2BO SELL triggered` | M2BO |
| `[WATCH ARMED]` / `[WATCH REPLACED]` / `[WATCH DONE]` / `[WATCH ARMED POST-CLOSE]` | Watch lifecycle |
| `[SIGNAL]` / `[SIGNAL SKIPPED]` / `Signal:` | Signals (skipped = during backfill) |
| `[RISK BLOCKED]` | Risk gate |
| `_sl_price: using sl_override` / `Structural SL:` | SL source |
| `[POS1 OPENED]` / `EA rejected order` | Entry result |
| `[POS2 TRIGGER HIT]` / `[POS2 ORDER SENT]` / `[POS2 OPENED]` / `Re-entry opened` | Additional positions |
| `EA close failed` | Close replies (currently also logged on success) |
| `Reply timeout for action=` / `PUSH send timeout` | ZMQ order problems |
| `[POSITION CLOSED]` / `Trade finished` | Trade end |
| `Traceback` / `KeyError` | Crashes in the stream thread (logged by `_start_stream`) |

---

## 14. Open issues / inconsistencies

Severity: **H** = can lose trades or money or crash the pipeline; **M** = wrong data or behaviour; **L** = cleanup.

| # | Sev | Issue | Evidence |
|---|---|---|---|
| 1 | H | **`KeyError` in `_on_structure_break`**: `m2bo[sym]` is created only in `_ensure_symbol` (which returns early for config symbols already in `builders`) and on paradox reset. The `__init__` loop and `_on_config_reload` never create it. Any break for a config symbol raises at `self.m2bo[symbol].on_break(...)` after the builder was already used but **before** the builder reset/replay. | Logs: 43 tracebacks at `main.py:945` (backfill path) and `:959` (live path) for `US30.Z26` and `XAUUSD@`. |
| 2 | H | **Symbol case mismatch**: config `"xauusd@"`, EA streams `"XAUUSD@"`. All matching is exact-case (`_symbol_config`, `_ensure_symbol`, `/api/symbols`, backtest). Result: backfill builds state under `xauusd@`; live candles auto-register a separate `XAUUSD@` state with **no SymbolConfig** → no session filter, no backfill trigger, and `_execute` refuses ("No symbol config — cannot open trade"). The `.upper()` prompt is not applied. | Logs: `Backfilling xauusd@` ×5, `CANDLE received: symbol=XAUUSD@`. |
| 3 | H | **Duplicate OPEN risk**: on a 5 s reply timeout, `send_order` resends OPEN with a new `req_id`; the EA has no idempotency, so a slow first order plus a retry gives two positions. Python ignores the late reply, so the first position is untracked. Timeouts are likely because the EA processes **one command per 100 ms tick** and every candle queues DRAW commands (fail zone every candle, plus leg/stop/update/delete), so OPEN waits behind them. | `mt5_zmq.py:send_order`; EA `CheckForCommands`. |
| 4 | H | **Same-bar tick after entry (live only)**: `positions.on_price(candle.low, candle.high)` runs after `_execute` on the same candle, so the range that happened **before** the entry (at close) can fire +0.25 R / +0.5 R / −0.5 R events immediately. The backtest ticks positions before signals and does not have this. | `_process_candle` steps 11–13. |
| 5 | M | **Shift-score inputs misaligned**: `StructureBuilder.all_candles` is never cleared and gets replayed candles appended again after every break/paradox replay, while `ema[symbol]` gets one value per candle **plus** 60 pre-session warm-up values. Element d zips the two lists from index 0, so EMA values are offset; elements b and i scan `all_candles` by time and can see duplicate candles. This also feeds `SINGLE_LEG_REVERSED` cond2. | `structure_state.process`, `shift_scorer._check_ema60`, `_backfill_symbol`. |
| 6 | M | **CLOSE status mismatch**: `_close_ticket` expects `"OK"`, EA returns `"success"` → every close logged as an error; real failures are indistinguishable. | Logs: `EA close failed ... reply={'status': 'success'...}`. |
| 7 | M | **EA-side TP vs state machine**: pos 1 is sent with a broker TP (`r_based` 0.5 R); the broker can close it before Python's `POSITION1_CLOSE_TP`, and Python then sends CLOSE for a closed ticket. Re-entry and pos 2 have TP 0. Entries are market orders at bar close, but the state machine uses `signal.entry_price` (close, or key level for M2/Paradox), so R levels differ from the fill. | Logs: one CLOSE with `status: error`. |
| 8 | M | **Concurrency**: `_process_candle` runs from `zmq-stream` and `backfill-*` threads (and `_on_config_reload` in the API thread) with no lock. Startup backfill and first-candle backfill can overlap because `_is_backfilling` is set only after history arrives (up to 30 s later). | `_on_candle_message`, `_backfill_symbol`. |
| 9 | M | **`robot_running` from Settings has no effect until restart**: the settings page saves `risk.robot_running` via `/api/config`, but `_on_config_reload` never updates `BotState.robot_running` (which `_execute` reads). `/api/robot/start|stop` do update it, but no page calls them. | `api/main.py`, `settings.html:560`. |
| 10 | M | Broker time is captured only from CANDLE messages, so startup backfill normally times out after 15 s and runs later from the first live candle. The `_on_account_message` docstring claims otherwise. | `main.py:_on_account_message`. |
| 11 | M | Re-backfill (config reload) resets the builder but not `paradox`, `m2bo`, `ema`, or `_candle_index` (except on session change), so paradox `break_history` and EMA lists accumulate across runs. Backfill also ignores `session_end`. | `_on_config_reload`, `_backfill_symbol`. |
| 12 | M | `M2Strategy` is not wired; `_can_promote`, drawer `prev_stop`/`prev_leg`, and `.upper()` prompts are pending. | grep: no matches. |
| 13 | M | `FAILED_WAIT_REENTRY` waits indefinitely for price to return to entry; meanwhile the symbol counts as an active trade, blocking new watches/signals and counting toward `max_open_positions`. Mode B watches also wait indefinitely. | `state_machine._handle_failed_wait`. |
| 14 | M | Chart GlobalVariables are terminal-wide with no symbol; `delete` resets everything. Multi-symbol (or multi-chart) drawing will overwrite. Leg numbers come from `confirmed_legs`, so a promoted but unconfirmed leg overwrites the previous leg's rectangle. | EA `ProcessDraw`, `drawer.on_leg_confirmed`. |
| 15 | M | Live vs backtest: paradox reset logic, paradox zone input (`[1:]` vs all), no M2BO, pos 2 sizing (`risk_pct` vs `position2.risk_pct`), `position2.enabled` ignored live, step order, timezone parsing, equity curve uses EMA. | §12. |
| 16 | M | Unused config: `position2.tp_r`, `position2.entry_r` (hard-coded 0.25), `symbols[].sl_pips/tp_pips`, `app.db_path`; live ignores `position2.enabled`/`risk_pct`. `daily_loss_pct` never resets and uses balance only. | §4.4. |
| 17 | M | HUD / LiveMonitor expect messages the bot never sends: `structure.event` (`STRUCTURE_BROKEN`, `RESET`), `waiting_for`, `watch`, types `trade`/`trade_closed`/`trade_opened`. The tkinter HUD's BROKEN STRUCTURES list and target reset therefore never trigger, and active-target chips only accumulate. `hud.html` ignores `watch_update`. `_compute_active_targets` maps every mode except `"B"` to "B". | `hud/app.py`, `live_monitor.py`. |
| 18 | M | Supervisor `update_lesson` call passes `(id, title, content, category)` to a method declared `(id, title, category, content)` → content and category are swapped on edit. | `api/supervisor.py:147`, `memory.py:155`. |
| 19 | M | `supervisor_data/api_config.json` holds a plaintext API key; `supervisor_data/` is untracked but **not in `.gitignore`**, so a broad `git add` would commit it. | `.gitignore`. |
| 20 | L | `observations.json` has 3177 records, mostly `shift_score` (broadcast every candle while in a stop). | `live_monitor._OBSERVED`. |
| 21 | L | Log noise: INFO per broadcast, per ACCOUNT message (~10/s), per DRAW. | §13. |
| 22 | L | Dead / unreachable code: `_handle_leg_active`; `StructureBuilder.replay`; `route_external_reply`; `trigger_reentry`; `close_all`; `on_double_detected`; `get_zone`/`get_levels`; `_is_bull_by_close`/`_is_bear_by_close`; `api/websocket.py`; app-level `/api/backtest/run`, `/list`, `/{id}` (shadowed or never populated); `/api/refresh` (no-op); `/api/candles/clear` (no-op); `BotState.active_trades` (always empty → `/api/status` reports 0). | grep. |
| 23 | L | Stale comments/docs: `mt5_zmq.py` header says HISTORY_REPLY arrives on PUB (it arrives on 5556); `send_order` docstring says "retries up to 2 times" (3 attempts); Paradoxical docstring says it "trades" the zone (main only resets); `shift_scorer._check_double` docstring maps directions the other way round from the code; `structure_state` comment "C excluded" is false; `FRESH_STOP ⊂ ALWAYS_STOP` makes the ALWAYS_STOP branch in `_handle_fresh` L-only; EA header "v2", indicator header "v5". | — |
| 24 | L | `CP_ENTRY_*` is never written (Python never sends `entry`), and the indicator labels only Leg2Chain/Leg3Drive. | EA, V11. |
| 25 | L | Repo copy `captainpips/mql5/CaptainPipsStructure.mq5` is outdated vs deployed `CP Structure V11.mq5`; the EA source (`CP EA V3.mq5`) is not in the repo at all. | §2. |
| 26 | L | Session filter is a string compare: no sessions crossing midnight. | `_in_session`. |
| 27 | L | Stray root file `{[e.kind.value`; `main_backup_20261007.py`; `test_scenarios.py`/`test_strategies.py` collect no tests; README garbled; `pandas`/`numpy`/`pydantic-settings` unused. | — |
| 28 | L | Startup migration writes `config.json` if `risk.sl_pips` is 0. `ConnectionManager.disconnect` in `api/main.py` uses a convoluted conditional expression (works, via `remove`). | `start()`, `api/main.py:87`. |
