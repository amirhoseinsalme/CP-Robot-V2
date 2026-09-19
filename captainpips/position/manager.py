"""
Position manager — wires the R-based state machine to ZMQ order execution.

One ActiveTrade per symbol at a time. Rejects new signals when a trade
is already active on that symbol.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable

from captainpips.config import Config, RiskConfig, SymbolConfig
from captainpips.connectors.mt5_zmq import ZmqConnector
from captainpips.core.definitions import Direction, Segment, SegmentKind, Signal
from captainpips.position.sizing import PositionSizer
from captainpips.position.state_machine import TradeEvent, TradeStateMachine

log = logging.getLogger(__name__)

_MAGIC_BASE = 88_000


@dataclass
class ActiveTrade:
    """All runtime state for a single open trade."""

    symbol: str
    direction: Direction
    entry_price: float
    main_sl_price: float
    pos1_ticket: int | None
    pos2_ticket: int | None
    reentry_ticket: int | None
    state_machine: TradeStateMachine
    magic: int


class PositionManager:
    """Opens, manages, and closes positions in response to state-machine events."""

    def __init__(self, zmq: ZmqConnector, config: Config) -> None:
        self._zmq = zmq
        self._cfg = config
        self._sizer = PositionSizer()
        self._trades: dict[str, ActiveTrade] = {}
        self._magic_counter = 0

    # -----------------------------------------------------------------------
    # Trade opening
    # -----------------------------------------------------------------------

    def open_trade(
        self,
        signal: Signal,
        symbol_config: SymbolConfig,
        account_balance: float,
        segments: list[Segment] | None = None,
    ) -> bool:
        """
        Size and send Position 1, then register the state machine.

        Returns True if the order was accepted by the EA; False otherwise.
        """
        sym = signal.symbol

        if self.has_active_trade(sym):
            log.warning("open_trade: already active on %s — ignoring signal", sym)
            return False

        sc = symbol_config
        sl_price = _sl_price(signal, sc, self._cfg.risk, segments)
        sl_distance = abs(signal.entry_price - sl_price)
        tp_price = _tp_price(signal, sc, self._cfg.risk, sl_distance)

        try:
            lot = self._sizer.calculate(
                account_balance=account_balance,
                risk_pct=self._cfg.risk.risk_pct,
                sl_distance=sl_distance,
                tick_size=sc.tick_size,
                tick_value=sc.tick_value,
                lot_step=sc.lot_step,
                min_lot=sc.min_lot,
                max_lot=sc.max_lot,
                multiplier=self._cfg.position.position1_multiplier,
            )
        except ValueError as exc:
            log.error("open_trade: sizing error for %s — %s", sym, exc)
            return False

        if lot == 0.0:
            log.warning("open_trade: sized lot below minimum for %s", sym)
            return False

        magic = self._next_magic()
        order_type = "BUY" if signal.direction == Direction.BULL else "SELL"
        reply = self._zmq.send_order(
            action="OPEN",
            symbol=sym,
            order_type=order_type,
            sl=sl_price,
            tp=tp_price,
            lot=lot,
            magic=magic,
        )

        if not reply or reply.get("status") != "success":
            log.error("open_trade: EA rejected order for %s: %s", sym, reply)
            return False

        ticket = reply.get("ticket")
        log.info("Position 1 opened: %s ticket=%s lot=%.2f", sym, ticket, lot)

        sm = TradeStateMachine(
            entry_price=signal.entry_price,
            main_sl_price=sl_price,
            direction=signal.direction,
            enable_pos2_loss_floor=self._cfg.position.enable_pos2_loss_floor,
        )

        self._trades[sym] = ActiveTrade(
            symbol=sym,
            direction=signal.direction,
            entry_price=signal.entry_price,
            main_sl_price=sl_price,
            pos1_ticket=ticket,
            pos2_ticket=None,
            reentry_ticket=None,
            state_machine=sm,
            magic=magic,
        )
        return True

    # -----------------------------------------------------------------------
    # Price update
    # -----------------------------------------------------------------------

    def on_price(self, symbol: str, bar_low: float, bar_high: float) -> None:
        """Tick the state machine and dispatch each resulting event."""
        trade = self._trades.get(symbol)
        if trade is None:
            return

        events = trade.state_machine.update_bar(bar_low, bar_high)

        for event in events:
            self._dispatch(event, trade)

        if trade.state_machine.is_finished:
            log.info("Trade finished: %s", symbol)
            self._trades.pop(symbol, None)

    # -----------------------------------------------------------------------
    # Queries
    # -----------------------------------------------------------------------

    def has_active_trade(self, symbol: str) -> bool:
        return symbol in self._trades

    def get_active_trade(self, symbol: str) -> ActiveTrade | None:
        return self._trades.get(symbol)

    # -----------------------------------------------------------------------
    # Emergency close
    # -----------------------------------------------------------------------

    def close_all(self) -> None:
        """Emergency close — sends CLOSE_ALL to EA then clears local state."""
        log.warning("close_all: closing all positions")
        self._zmq.send_order(action="CLOSE_ALL")
        self._trades.clear()

    # -----------------------------------------------------------------------
    # Event dispatch
    # -----------------------------------------------------------------------

    def _dispatch(self, event: TradeEvent, trade: ActiveTrade) -> None:
        handlers: dict[TradeEvent, Callable] = {
            TradeEvent.POSITION2_ADDED:    self._on_pos2_added,
            TradeEvent.POSITION2_CLOSE_BE: self._on_pos2_close_be,
            TradeEvent.POSITION1_CLOSE_TP: self._on_pos1_close_tp,
            TradeEvent.FULL_FAIL_EXIT:     self._on_full_fail_exit,
            TradeEvent.REENTRY_TRIGGERED:  self._on_reentry_triggered,
            TradeEvent.REENTRY_CLOSE_TP:   self._on_reentry_close,
            TradeEvent.REENTRY_CLOSE_SL:   self._on_reentry_close,
        }
        handler = handlers.get(event)
        if handler:
            handler(trade)

    def _on_pos2_added(self, trade: ActiveTrade) -> None:
        sc = self._symbol_config(trade.symbol)
        if sc is None:
            return

        # Re-size using current balance estimate; use pos2 multiplier
        lot = self._size(trade, sc, self._cfg.position.position2_multiplier)
        if lot == 0.0:
            log.warning("pos2: sized to zero for %s — skipping", trade.symbol)
            return

        order_type = "BUY" if trade.direction == Direction.BULL else "SELL"
        reply = self._zmq.send_order(
            action="OPEN",
            symbol=trade.symbol,
            order_type=order_type,
            sl=trade.main_sl_price,
            tp=0,
            lot=lot,
            magic=trade.magic,
        )
        if reply and reply.get("status") == "success":
            trade.pos2_ticket = reply.get("ticket")
            log.info("Position 2 opened: %s ticket=%s", trade.symbol, trade.pos2_ticket)
        else:
            log.error("pos2: EA rejected order for %s: %s", trade.symbol, reply)

    def _on_pos2_close_be(self, trade: ActiveTrade) -> None:
        if trade.pos2_ticket is None:
            return
        self._close_ticket(trade.symbol, trade.pos2_ticket, "pos2 BE close")
        trade.pos2_ticket = None

    def _on_pos1_close_tp(self, trade: ActiveTrade) -> None:
        for ticket, label in [
            (trade.pos1_ticket, "pos1 TP"),
            (trade.pos2_ticket, "pos2 TP"),
        ]:
            if ticket is not None:
                self._close_ticket(trade.symbol, ticket, label)
        trade.pos1_ticket = None
        trade.pos2_ticket = None

    def _on_full_fail_exit(self, trade: ActiveTrade) -> None:
        if trade.pos1_ticket is not None:
            self._close_ticket(trade.symbol, trade.pos1_ticket, "pos1 fail exit")
            trade.pos1_ticket = None
        if trade.pos2_ticket is not None:
            self._close_ticket(trade.symbol, trade.pos2_ticket, "pos2 fail exit")
            trade.pos2_ticket = None

    def _on_reentry_triggered(self, trade: ActiveTrade) -> None:
        sc = self._symbol_config(trade.symbol)
        if sc is None:
            return

        lot = self._size(trade, sc, self._cfg.position.reentry_multiplier)
        if lot == 0.0:
            log.warning("reentry: sized to zero for %s — skipping", trade.symbol)
            return

        order_type = "BUY" if trade.direction == Direction.BULL else "SELL"
        reply = self._zmq.send_order(
            action="OPEN",
            symbol=trade.symbol,
            order_type=order_type,
            sl=trade.main_sl_price,
            tp=0,
            lot=lot,
            magic=trade.magic,
        )
        if reply and reply.get("status") == "success":
            trade.reentry_ticket = reply.get("ticket")
            log.info("Re-entry opened: %s ticket=%s", trade.symbol, trade.reentry_ticket)
        else:
            log.error("reentry: EA rejected order for %s: %s", trade.symbol, reply)

    def _on_reentry_close(self, trade: ActiveTrade) -> None:
        if trade.reentry_ticket is not None:
            self._close_ticket(trade.symbol, trade.reentry_ticket, "reentry close")
            trade.reentry_ticket = None

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _close_ticket(self, symbol: str, ticket: int, label: str) -> None:
        reply = self._zmq.send_order(
            action="CLOSE",
            symbol=symbol,
            ticket=ticket,
        )
        if reply and reply.get("status") == "OK":
            log.info("%s: closed ticket=%s", label, ticket)
        else:
            log.error("%s: EA close failed ticket=%s reply=%s", label, ticket, reply)

    def _size(
        self, trade: ActiveTrade, sc: SymbolConfig, multiplier: float
    ) -> float:
        sl_dist = abs(trade.entry_price - trade.main_sl_price)
        try:
            return self._sizer.calculate(
                account_balance=0.0,    # caller should inject current balance;
                risk_pct=self._cfg.risk.risk_pct,
                sl_distance=sl_dist,
                tick_size=sc.tick_size,
                tick_value=sc.tick_value,
                lot_step=sc.lot_step,
                min_lot=sc.min_lot,
                max_lot=sc.max_lot,
                multiplier=multiplier,
            )
        except ValueError as exc:
            log.error("_size error: %s", exc)
            return 0.0

    def _symbol_config(self, symbol: str) -> SymbolConfig | None:
        for sc in self._cfg.symbols:
            if sc.symbol == symbol:
                return sc
        log.error("No symbol config for %s", symbol)
        return None

    def _next_magic(self) -> int:
        self._magic_counter += 1
        return _MAGIC_BASE + self._magic_counter


# ---------------------------------------------------------------------------
# Module helper
# ---------------------------------------------------------------------------

def _sl_price(
    signal: Signal,
    sc: SymbolConfig,
    risk_cfg: RiskConfig,
    segments: list[Segment] | None = None,
) -> float:
    sl_cfg = risk_cfg.sl
    if sl_cfg.type == "structural" and segments:
        strategy = signal.strategy
        key = (
            sl_cfg.structural_leg2chain
            if "Leg2Chain" in strategy
            else sl_cfg.structural_leg3drive
        )
        legs  = [s for s in segments if s.kind == SegmentKind.LEG]
        stops = [s for s in segments if s.kind == SegmentKind.STOP]
        if key == "leg1_extreme" and legs:
            seg = legs[0]
            return seg.low if signal.direction == Direction.BULL else seg.high
        if key == "stop1_extreme" and stops:
            seg = stops[0]
            return seg.low if signal.direction == Direction.BULL else seg.high
        log.warning(
            "_sl_price: structural SL unavailable for %s (key=%s, legs=%d, stops=%d) — using fixed",
            signal.symbol, key, len(legs), len(stops),
        )

    distance = sl_cfg.fixed_pips * sc.pip_value
    if signal.direction == Direction.BULL:
        return signal.entry_price - distance
    return signal.entry_price + distance


def _tp_price(
    signal: Signal,
    sc: SymbolConfig,
    risk_cfg: RiskConfig,
    sl_distance: float = 0.0,
) -> float:
    tp_cfg = risk_cfg.tp
    if tp_cfg.type == "r_based":
        dist = sl_distance * tp_cfg.r_multiplier
    else:
        dist = tp_cfg.fixed_pips * sc.pip_value
    if signal.direction == Direction.BULL:
        return signal.entry_price + dist
    return signal.entry_price - dist
