"""
Backtest engine — runs the full CP Robot pipeline on historical data.

Same logic as main.py but offline: no ZMQ, no WebSocket, no broker.
Positions are simulated using candle highs/lows for SL/TP checks.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from captainpips.config import Config, SymbolConfig
from captainpips.core.definitions import (
    Candle,
    Direction,
    Segment,
    SegmentKind,
    Signal,
)
from captainpips.core.strategies.base import BaseStrategy, EntryWatch
from captainpips.core.strategies.leg2chain import Leg2Chain
from captainpips.core.strategies.leg3drive import Leg3Drive
from captainpips.core.strategies.paradoxical import Paradoxical
from captainpips.core.structure_state import EventKind, StructureBuilder
from captainpips.position.manager import _sl_price, _tp_price
from captainpips.position.sizing import PositionSizer
from captainpips.position.state_machine import TradeEvent, TradeState, TradeStateMachine

log = logging.getLogger(__name__)

_EMA_PERIOD = 60


@dataclass
class SimTrade:
    """A simulated trade from entry to exit."""

    symbol: str
    strategy: str
    direction: Direction
    entry_price: float
    entry_time: str
    sl_price: float
    tp_price: float
    lot: float
    exit_price: float | None = None
    exit_time: str | None = None
    exit_reason: str | None = None
    pnl: float = 0.0


class BacktestEngine:
    """
    Runs the full CP Robot pipeline on historical CSV data.
    Same logic as main.py but offline.
    """

    def __init__(self, config: Config) -> None:
        self.config = config
        self.builder = StructureBuilder()
        self.paradox = Paradoxical()
        self.strategies: list[BaseStrategy] = [Leg2Chain(), Leg3Drive()]
        self.active_watch: tuple[EntryWatch, BaseStrategy] | None = None
        self.ema: list[float] = []
        self._ema_prev: float | None = None
        self.candle_index: int = 0
        self.trades: list[SimTrade] = []
        self.signals: list[Signal] = []
        self.equity_curve: list[tuple[str, float]] = []
        self.balance: float = 10_000.0
        self.equity: float = 10_000.0
        self._open_trade: dict | None = None
        self._sizer = PositionSizer()

    def run(
        self,
        candles: list[dict],
        symbol: str | None = None,
        from_time: str = "00:00",
        to_time: str = "23:59",
    ) -> BacktestResult:
        """
        Feed candles one by one through the full pipeline.

        candles:   list of dicts with keys: time, open, high, low, close
        symbol:    override symbol name (default: first enabled symbol in config)
        from_time: HH:MM — only candles at or after this time are processed
        to_time:   HH:MM — only candles at or before this time are processed
        """
        self._symbol = symbol or self._default_symbol()
        filtered = [c for c in candles if from_time <= c["time"][11:16] <= to_time]
        for raw in filtered:
            self._process_candle(raw)

        if self._open_trade and filtered:
            t = self._open_trade
            last_close = filtered[-1]["close"]
            last_time = filtered[-1].get("time", "")
            if not t.get("pos1_closed"):
                self._record_close(last_close, last_time, "end_of_data")
            elif t.get("reentry_active"):
                self._record_close(last_close, last_time, "end_of_data",
                                   lot=t.get("reentry_lot", t["lot"]))
            self._open_trade = None

        return BacktestResult(
            trades=self.trades,
            signals=self.signals,
            equity_curve=self.equity_curve,
            starting_balance=10_000.0,
            final_balance=self.balance,
        )

    def _default_symbol(self) -> str:
        enabled = self.config.enabled_symbols()
        return enabled[0].symbol if enabled else "UNKNOWN"

    def _symbol_config(self) -> SymbolConfig:
        for sc in self.config.symbols:
            if sc.symbol == self._symbol:
                return sc
        return SymbolConfig(symbol=self._symbol)

    # ------------------------------------------------------------------
    # Core pipeline (mirrors main.py._process_candle)
    # ------------------------------------------------------------------

    def _process_candle(self, raw: dict) -> None:
        time_input = raw.get("time", "")
        try:
            dt = datetime.strptime(time_input, "%Y.%m.%d %H:%M:%S")
            raw_time = int(dt.timestamp())
            time_str = dt.strftime("%H:%M")
        except (ValueError, TypeError):
            raw_time = 0
            time_str = time_input[:5] if len(str(time_input)) >= 5 else "00:00"

        index = self.candle_index
        self.candle_index += 1

        candle = Candle.from_raw(
            index=index,
            time=time_str,
            raw_time=raw_time,
            open=raw["open"],
            high=raw["high"],
            low=raw["low"],
            close=raw["close"],
        )

        # 1. Update EMA
        self._update_ema(candle.close)

        # 2. Tick state machine for open position
        if self._open_trade:
            self._tick_trade(candle)

        # 3. Paradoxical check
        signal = self.paradox.on_candle(candle)
        if signal:
            log.debug("[BT] Paradoxical boundary crossed → reset structure")
            self.builder.reset(from_candle_index=candle.index)
            self.builder.process(candle, ema_values=self.ema)
            self.paradox = Paradoxical()
            self.active_watch = None
            self._record_equity(time_str)
            return

        # 4. Structure processing
        events = self.builder.process(candle, ema_values=self.ema)

        # 5. Handle events
        broke = False
        for event in events:
            if event.kind in (EventKind.STRUCTURE_BROKEN, EventKind.SINGLE_LEG_REVERSED):
                self._on_structure_break(candle)
                broke = True
                break
            elif event.kind in (EventKind.LEG_CONFIRMED, EventKind.STOP_STARTED):
                self._on_structure_update(event, candle)

        # 6. Tick active watch
        if not broke and self.active_watch is not None:
            self._tick_watch(candle)

        # 7. Record equity
        self._record_equity(time_str)

    # ------------------------------------------------------------------
    # Structure break
    # ------------------------------------------------------------------

    def _on_structure_break(self, candle: Candle) -> None:
        builder = self.builder
        segments = builder.segments
        direction = builder.direction or Direction.NONE
        stops = [s for s in segments if s.kind == SegmentKind.STOP]

        structure_high = max(s.high for s in segments) if segments else candle.high
        structure_low = min(s.low for s in segments) if segments else candle.low

        self.paradox.on_break(direction, structure_high, structure_low)
        if self.paradox.is_active():
            self.paradox.set_activation_index(candle.index)

        self.active_watch = None

        replay_candles = list(stops[-1].candles) if stops else []
        start_idx = replay_candles[0].index if replay_candles else candle.index
        builder.reset(from_candle_index=start_idx)
        for rc in replay_candles:
            builder.process(rc, ema_values=self.ema)

    # ------------------------------------------------------------------
    # Structure update → arm watch
    # ------------------------------------------------------------------

    def _on_structure_update(self, event, candle: Candle) -> None:
        if self.active_watch is not None:
            return

        builder = self.builder
        for strategy in self.strategies:
            watch = strategy.on_structure_event(
                event, builder.segments, builder.confirmed_legs, candle,
            )
            if watch:
                log.debug(
                    "[BT] Watch armed: %s mode=%s legs=%d",
                    strategy.name, watch.mode, builder.confirmed_legs,
                )
                self.active_watch = (watch, strategy)
                break

    # ------------------------------------------------------------------
    # Watch tick → signal generation
    # ------------------------------------------------------------------

    def _tick_watch(self, candle: Candle) -> None:
        watch, strategy = self.active_watch
        signal = strategy.on_candle(candle, watch, self.builder.segments)

        if signal:
            signal = Signal(
                symbol=self._symbol,
                direction=signal.direction,
                entry_price=signal.entry_price,
                reason=signal.reason,
                strategy=signal.strategy,
                leg_number=signal.leg_number,
            )
            log.info(
                "[BT SIGNAL] %s %s @ %.5f reason=%s",
                signal.strategy, signal.direction.value,
                signal.entry_price, signal.reason,
            )
            self.signals.append(signal)
            self._execute(signal, candle)
            self.active_watch = None
            return

        if watch.mode in ("DONE", "CANCELLED"):
            self.active_watch = None

    # ------------------------------------------------------------------
    # Execution (simulated)
    # ------------------------------------------------------------------

    def _execute(self, signal: Signal, candle: Candle) -> None:
        if self._open_trade is not None:
            log.debug("[BT] Already in trade — skipping signal")
            return

        sc = self._symbol_config()
        # SL: uses config.risk.sl (fixed or structural)
        sl = _sl_price(signal, sc, self.config.risk, self.builder.segments)
        sl_distance = abs(signal.entry_price - sl)
        # TP: uses config.risk.tp (fixed pips or r_based multiplier)
        tp = _tp_price(signal, sc, self.config.risk, sl_distance)

        try:
            lot = self._sizer.calculate(
                account_balance=self.balance,
                risk_pct=self.config.risk.risk_pct,
                sl_distance=sl_distance,
                tick_size=sc.tick_size,
                tick_value=sc.tick_value,
                lot_step=sc.lot_step,
                min_lot=sc.min_lot,
                max_lot=sc.max_lot,
                multiplier=self.config.position.position1_multiplier,
            )
        except ValueError:
            return

        if lot == 0.0:
            return

        # Pos2 lot (sized if position2 is enabled in config)
        pos2_lot = 0.0
        if self.config.risk.position2.enabled:
            try:
                pos2_lot = self._sizer.calculate(
                    account_balance=self.balance,
                    risk_pct=self.config.risk.position2.risk_pct,
                    sl_distance=sl_distance,
                    tick_size=sc.tick_size,
                    tick_value=sc.tick_value,
                    lot_step=sc.lot_step,
                    min_lot=sc.min_lot,
                    max_lot=sc.max_lot,
                    multiplier=self.config.position.position2_multiplier,
                )
            except ValueError:
                pos2_lot = 0.0

        sm = TradeStateMachine(
            entry_price=signal.entry_price,
            main_sl_price=sl,
            direction=signal.direction,
            enable_pos2_loss_floor=self.config.position.enable_pos2_loss_floor,
        )

        self._open_trade = {
            "entry": signal.entry_price,
            "sl": sl,
            "tp": tp,
            "risk": sl_distance,
            "sm": sm,
            "lot": lot,
            "pos2_lot": pos2_lot,
            "entry_time": candle.time,
            "strategy": signal.strategy,
            "direction": signal.direction,
            "pos2_opened": False,
            "pos1_closed": False,
            "reentry_active": False,
            "reentry_lot": 0.0,
        }

        log.info(
            "[BT OPEN] %s %s @ %.5f SL=%.5f risk=%.5f lot=%.2f",
            signal.strategy, signal.direction.value,
            signal.entry_price, sl, sl_distance, lot,
        )

    # ------------------------------------------------------------------
    # Position simulation (state-machine driven)
    # ------------------------------------------------------------------

    def _tick_trade(self, candle: Candle) -> None:
        t = self._open_trade
        if not t:
            return

        sm = t["sm"]
        prev_state = sm.state
        events = sm.update_bar(candle.low, candle.high)

        for event in events:
            if event == TradeEvent.POSITION2_ADDED:
                t["pos2_opened"] = True
                log.info("[BT] Pos2 added at +0.25R, lot=%.2f", t["pos2_lot"])

            elif event == TradeEvent.POSITION2_CLOSE_BE:
                log.info("[BT] Pos2 closed at BE (0R)")

            elif event == TradeEvent.POSITION1_CLOSE_TP:
                exit_price = self._r_to_price(0.5)
                self._record_close(exit_price, candle.time, "tp")
                self._open_trade = None
                return

            elif event == TradeEvent.FULL_FAIL_EXIT:
                if prev_state == TradeState.POS1_ONLY_POST_BE:
                    exit_r = -1.0
                else:
                    exit_r = -0.5
                exit_price = self._r_to_price(exit_r)
                self._record_close(exit_price, candle.time, "sl")
                t["pos1_closed"] = True

            elif event == TradeEvent.REENTRY_TRIGGERED:
                sc = self._symbol_config()
                try:
                    re_lot = self._sizer.calculate(
                        account_balance=self.balance,
                        risk_pct=self.config.risk.risk_pct,
                        sl_distance=t["risk"],
                        tick_size=sc.tick_size,
                        tick_value=sc.tick_value,
                        lot_step=sc.lot_step,
                        min_lot=sc.min_lot,
                        max_lot=sc.max_lot,
                        multiplier=self.config.position.reentry_multiplier,
                    )
                except ValueError:
                    re_lot = 0.0
                t["reentry_active"] = True
                t["reentry_lot"] = re_lot
                log.info("[BT] Re-entry triggered, lot=%.2f", re_lot)

            elif event == TradeEvent.REENTRY_CLOSE_TP:
                exit_price = self._r_to_price(0.5)
                self._record_close(exit_price, candle.time, "reentry_tp",
                                   lot=t["reentry_lot"])
                self._open_trade = None
                return

            elif event == TradeEvent.REENTRY_CLOSE_SL:
                exit_price = self._r_to_price(-1.0)
                self._record_close(exit_price, candle.time, "reentry_sl",
                                   lot=t["reentry_lot"])
                self._open_trade = None
                return

    def _r_to_price(self, r_mult: float) -> float:
        t = self._open_trade
        if t["direction"] == Direction.BULL:
            return t["entry"] + r_mult * t["risk"]
        return t["entry"] - r_mult * t["risk"]

    def _record_close(self, exit_price: float, exit_time: str,
                      reason: str, lot: float | None = None) -> None:
        t = self._open_trade
        sc = self._symbol_config()
        use_lot = lot if lot is not None else t["lot"]

        if t["direction"] == Direction.BULL:
            price_diff = exit_price - t["entry"]
        else:
            price_diff = t["entry"] - exit_price

        ticks = price_diff / sc.tick_size
        pnl = ticks * sc.tick_value * use_lot

        trade = SimTrade(
            symbol=self._symbol,
            strategy=t["strategy"],
            direction=t["direction"],
            entry_price=t["entry"],
            entry_time=t["entry_time"],
            sl_price=t["sl"],
            tp_price=t["tp"],
            lot=use_lot,
            exit_price=exit_price,
            exit_time=exit_time,
            exit_reason=reason,
            pnl=pnl,
        )

        self.balance += pnl
        self.equity = self.balance
        self.trades.append(trade)

        log.info(
            "[BT CLOSE] %s %s @ %.5f pnl=%.2f reason=%s balance=%.2f",
            t["strategy"], t["direction"].value,
            exit_price, pnl, reason, self.balance,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _update_ema(self, close: float) -> None:
        if self._ema_prev is None:
            self._ema_prev = close
        else:
            k = 2.0 / (_EMA_PERIOD + 1)
            self._ema_prev = close * k + self._ema_prev * (1.0 - k)
        self.ema.append(self._ema_prev)

    def _record_equity(self, time_str: str) -> None:
        unrealised = 0.0
        t = self._open_trade
        if t and not t.get("pos1_closed"):
            sc = self._symbol_config()
            last_close = self.ema[-1] if self.ema else t["entry"]
            if t["direction"] == Direction.BULL:
                diff = last_close - t["entry"]
            else:
                diff = t["entry"] - last_close
            ticks = diff / sc.tick_size
            unrealised = ticks * sc.tick_value * t["lot"]
        self.equity = self.balance + unrealised
        self.equity_curve.append((time_str, self.equity))


class BacktestResult:
    """Aggregated results from a backtest run."""

    def __init__(
        self,
        trades: list[SimTrade],
        signals: list[Signal],
        equity_curve: list[tuple[str, float]],
        starting_balance: float,
        final_balance: float,
    ) -> None:
        self.trades = trades
        self.signals = signals
        self.equity_curve = equity_curve
        self.starting_balance = starting_balance
        self.final_balance = final_balance

    @property
    def total_trades(self) -> int:
        return len(self.trades)

    @property
    def winning_trades(self) -> int:
        return sum(1 for t in self.trades if t.pnl > 0)

    @property
    def losing_trades(self) -> int:
        return sum(1 for t in self.trades if t.pnl <= 0)

    @property
    def win_rate(self) -> float:
        if not self.trades:
            return 0.0
        return self.winning_trades / len(self.trades) * 100

    @property
    def total_pnl(self) -> float:
        return sum(t.pnl for t in self.trades)

    @property
    def max_drawdown(self) -> float:
        if not self.equity_curve:
            return 0.0
        peak = self.equity_curve[0][1]
        max_dd = 0.0
        for _, eq in self.equity_curve:
            if eq > peak:
                peak = eq
            dd = (peak - eq) / peak * 100
            if dd > max_dd:
                max_dd = dd
        return max_dd

    @property
    def profit_factor(self) -> float:
        gross_profit = sum(t.pnl for t in self.trades if t.pnl > 0)
        gross_loss = abs(sum(t.pnl for t in self.trades if t.pnl < 0))
        if gross_loss == 0:
            return float("inf") if gross_profit > 0 else 0.0
        return gross_profit / gross_loss

    def summary(self) -> str:
        return (
            f"Trades: {self.total_trades} | "
            f"Win: {self.winning_trades} | "
            f"Loss: {self.losing_trades} | "
            f"Win%: {self.win_rate:.1f}% | "
            f"PnL: {self.total_pnl:.2f} | "
            f"PF: {self.profit_factor:.2f} | "
            f"MaxDD: {self.max_drawdown:.1f}% | "
            f"Final: {self.final_balance:.2f}"
        )
