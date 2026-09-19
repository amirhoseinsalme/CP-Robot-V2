"""
Tests for position sizing and R-based trade state machine.

Test 1  — Lot sizing from risk %
  balance=1000, risk=1%, sl_distance=60, tick_size/value=1.0
  raw = 1000 * 0.01 / (60 * 1.0) = 0.1666…

Test 2  — Position 2 trigger at +0.25R
  Entry=100, SL=40 (risk=60)
  +0.25R = 115, +0.50R (TP) = 130, -0.50R (fail) = 70
"""
from __future__ import annotations

import sys
sys.path.insert(0, "D:\\CP Robot V2")

import pytest

from captainpips.core.definitions import Direction
from captainpips.position.sizing import PositionSizer
from captainpips.position.state_machine import (
    TradeEvent,
    TradeState,
    TradeStateMachine,
)


# ---------------------------------------------------------------------------
# Test 1: Lot sizing
# ---------------------------------------------------------------------------

class TestPositionSizer:
    def setup_method(self):
        self.sizer = PositionSizer()

    def test_basic_calculation(self):
        # lot_step=0.001 so we can see the full 0.166 value
        lots = self.sizer.calculate(
            account_balance=1000.0,
            risk_pct=1.0,
            sl_distance=60.0,
            tick_size=1.0,
            tick_value=1.0,
            lot_step=0.001,
            min_lot=0.001,
            max_lot=50.0,
        )
        assert lots == pytest.approx(0.166, abs=0.001)

    def test_floored_to_lot_step(self):
        # lot_step=0.01 → floor(16.66) * 0.01 = 0.16
        lots = self.sizer.calculate(
            account_balance=1000.0,
            risk_pct=1.0,
            sl_distance=60.0,
            tick_size=1.0,
            tick_value=1.0,
            lot_step=0.01,
            min_lot=0.01,
            max_lot=50.0,
        )
        assert lots == 0.16

    def test_zero_sl_raises(self):
        with pytest.raises(ValueError):
            self.sizer.calculate(
                account_balance=1000.0,
                risk_pct=1.0,
                sl_distance=0.0,
                tick_size=1.0,
                tick_value=1.0,
                lot_step=0.01,
                min_lot=0.01,
                max_lot=50.0,
            )

    def test_below_min_lot_returns_zero(self):
        lots = self.sizer.calculate(
            account_balance=1.0,
            risk_pct=1.0,
            sl_distance=60.0,
            tick_size=1.0,
            tick_value=1.0,
            lot_step=0.01,
            min_lot=0.01,
            max_lot=50.0,
        )
        assert lots == 0.0

    def test_multiplier_doubles_lot(self):
        lots = self.sizer.calculate(
            account_balance=1000.0,
            risk_pct=1.0,
            sl_distance=60.0,
            tick_size=1.0,
            tick_value=1.0,
            lot_step=0.001,
            min_lot=0.001,
            max_lot=50.0,
            multiplier=2.0,
        )
        assert lots == pytest.approx(0.333, abs=0.001)

    def test_capped_at_max_lot(self):
        lots = self.sizer.calculate(
            account_balance=1_000_000.0,
            risk_pct=10.0,
            sl_distance=1.0,
            tick_size=1.0,
            tick_value=1.0,
            lot_step=0.01,
            min_lot=0.01,
            max_lot=5.0,
        )
        assert lots == 5.0


# ---------------------------------------------------------------------------
# Test 2: Trade state machine — Position 2 trigger at +0.25R
# ---------------------------------------------------------------------------
#
# Entry=100, SL=40, risk=60
# +0.25R = 115   → POSITION2_ADDED
# +0.50R = 130   → TP
# -0.25R =  85   → NEGATIVE_WATCH
# -0.50R =  70   → FULL_FAIL_EXIT
#
class TestTradeStateMachine:
    ENTRY = 100.0
    SL    =  40.0
    RISK  = ENTRY - SL          # 60
    R025  = ENTRY + 0.25 * RISK  # 115
    R050  = ENTRY + 0.50 * RISK  # 130
    FAIL  = ENTRY - 0.50 * RISK  # 70

    def _sm(self, **kw) -> TradeStateMachine:
        return TradeStateMachine(
            entry_price=self.ENTRY,
            main_sl_price=self.SL,
            direction=Direction.BULL,
            **kw,
        )

    def test_initial_state(self):
        assert self._sm().state == TradeState.INITIAL_MONITORING

    def test_r_value_calculations(self):
        sm = self._sm()
        assert sm.r_value(self.ENTRY) == pytest.approx(0.0)
        assert sm.r_value(self.R025)  == pytest.approx(0.25)
        assert sm.r_value(self.R050)  == pytest.approx(0.50)
        assert sm.r_value(self.FAIL)  == pytest.approx(-0.50)

    def test_pos2_triggers_at_025r(self):
        sm = self._sm()
        events = sm.update_bar(bar_low=99.0, bar_high=self.R025)
        assert TradeEvent.POSITION2_ADDED in events
        assert sm.state == TradeState.POS2_ACTIVE

    def test_pos2_not_triggered_just_below(self):
        sm = self._sm()
        events = sm.update_bar(bar_low=99.0, bar_high=self.R025 - 0.01)
        assert events == []
        assert sm.state == TradeState.INITIAL_MONITORING

    def test_full_fail_beats_pos2_same_bar(self):
        # Adverse checked first in INITIAL_MONITORING — wipeout wins
        sm = self._sm()
        events = sm.update_bar(bar_low=self.FAIL, bar_high=self.R025)
        assert TradeEvent.FULL_FAIL_EXIT in events
        assert TradeEvent.POSITION2_ADDED not in events
        assert sm.state == TradeState.FAILED_WAIT_REENTRY

    def test_direct_tp_without_pos2(self):
        sm = self._sm()
        events = sm.update_bar(bar_low=99.0, bar_high=self.R050)
        assert TradeEvent.POSITION1_CLOSE_TP in events
        assert sm.state == TradeState.FINISHED

    def test_pos2_then_tp(self):
        sm = self._sm()
        sm.update_bar(bar_low=99.0, bar_high=self.R025)
        assert sm.state == TradeState.POS2_ACTIVE
        events = sm.update_bar(bar_low=110.0, bar_high=self.R050)
        assert TradeEvent.POSITION1_CLOSE_TP in events
        assert sm.state == TradeState.FINISHED

    def test_pos2_close_at_breakeven(self):
        sm = self._sm()
        sm.update_bar(bar_low=99.0, bar_high=self.R025)
        assert sm.state == TradeState.POS2_ACTIVE
        # Pull back to entry level (0R)
        events = sm.update_bar(bar_low=self.ENTRY, bar_high=self.ENTRY + 1.0)
        assert TradeEvent.POSITION2_CLOSE_BE in events
        assert sm.state == TradeState.POS1_ONLY_POST_BE

    def test_negative_watch_transition(self):
        sm = self._sm()
        # Dip below -0.25R but stay above -0.50R
        events = sm.update_bar(bar_low=85.0, bar_high=99.0)
        assert events == []
        assert sm.state == TradeState.NEGATIVE_WATCH

    def test_reentry_after_fail(self):
        sm = self._sm()
        sm.update_bar(bar_low=self.FAIL, bar_high=self.FAIL + 1.0)
        assert sm.state == TradeState.FAILED_WAIT_REENTRY
        # Price returns to entry level → re-entry triggered
        events = sm.update_bar(bar_low=self.ENTRY, bar_high=self.ENTRY + 1.0)
        assert TradeEvent.REENTRY_TRIGGERED in events
        assert sm.state == TradeState.REENTRY_ACTIVE
