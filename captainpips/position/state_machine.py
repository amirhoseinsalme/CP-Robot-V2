"""
R-based position state machine.

Manages the lifecycle of a single trade through position-1/position-2
logic, breakeven management, and re-entry, driven entirely by price
expressed in R-multiples.

Imports only from definitions.
"""

from __future__ import annotations

from enum import Enum, auto

from captainpips.core.definitions import Direction


# ---------------------------------------------------------------------------
# State and event enums
# ---------------------------------------------------------------------------

class TradeState(Enum):
    INITIAL_MONITORING  = auto()
    POS2_ACTIVE         = auto()
    POS1_ONLY_POST_BE   = auto()
    NEGATIVE_WATCH      = auto()
    FAILED_WAIT_REENTRY = auto()
    REENTRY_ACTIVE      = auto()
    FINISHED            = auto()


class TradeEvent(Enum):
    POSITION2_ADDED     = auto()
    POSITION2_CLOSE_BE  = auto()
    POSITION1_CLOSE_TP  = auto()
    FULL_FAIL_EXIT      = auto()
    REENTRY_TRIGGERED   = auto()
    REENTRY_CLOSE_TP    = auto()
    REENTRY_CLOSE_SL    = auto()


# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

_R_ADD_POS2      =  0.25
_R_CLOSE_POS2_BE =  0.00
_R_TP            =  0.50
_R_NEGATIVE      = -0.25
_R_FULL_FAIL     = -0.50
_R_SL_POS1_ALONE = -1.00


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------

class TradeStateMachine:
    """
    Drives one trade through R-based milestones, returning events each bar.

    Processing order
    ----------------
    INITIAL_MONITORING: adverse extreme checked first — a -0.5R wipeout on
        the same bar as a +0.25R run resolves as a fail, not a pos-2 add.
    All other states: favorable extreme first — a TP and a BE-close on the
        same bar resolves as a TP.
    """

    def __init__(
        self,
        entry_price: float,
        main_sl_price: float,
        direction: Direction,
        enable_pos2_loss_floor: bool = False,
    ) -> None:
        self._entry = entry_price
        self._sl = main_sl_price
        self._direction = direction
        self._risk = abs(entry_price - main_sl_price)
        self._enable_pos2_loss_floor = enable_pos2_loss_floor
        self.state = TradeState.INITIAL_MONITORING

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def update_bar(self, bar_low: float, bar_high: float) -> list[TradeEvent]:
        """Process one bar; return every event that fired (may be empty)."""
        dispatch = {
            TradeState.INITIAL_MONITORING:  self._handle_initial,
            TradeState.POS2_ACTIVE:         self._handle_pos2_active,
            TradeState.POS1_ONLY_POST_BE:   self._handle_pos1_only,
            TradeState.NEGATIVE_WATCH:      self._handle_negative_watch,
            TradeState.FAILED_WAIT_REENTRY: self._handle_failed_wait,
            TradeState.REENTRY_ACTIVE:      self._handle_reentry_active,
            TradeState.FINISHED:            lambda lo, hi: [],
        }
        return dispatch[self.state](bar_low, bar_high)

    def trigger_reentry(self) -> list[TradeEvent]:
        """
        Externally signal that a re-entry condition has been met.

        Call from the strategy layer when a new structural signal appears
        while the machine is in FAILED_WAIT_REENTRY. Returns
        [REENTRY_TRIGGERED] if the transition is valid, else [].
        """
        if self.state != TradeState.FAILED_WAIT_REENTRY:
            return []
        self.state = TradeState.REENTRY_ACTIVE
        return [TradeEvent.REENTRY_TRIGGERED]

    def r_value(self, price: float) -> float:
        """Convert an absolute price to its R-multiple relative to this trade."""
        if self._direction == Direction.BULL:
            return (price - self._entry) / self._risk
        return (self._entry - price) / self._risk

    @property
    def is_finished(self) -> bool:
        return self.state == TradeState.FINISHED

    # -----------------------------------------------------------------------
    # State handlers
    # -----------------------------------------------------------------------

    def _handle_initial(self, lo: float, hi: float) -> list[TradeEvent]:
        """
        Adverse first: a wipeout on the same bar as a +0.25R spike resolves
        as FULL_FAIL, not as a pos-2 add followed by a loss.
        """
        adv = self._adverse_r(lo, hi)
        fav = self._favorable_r(lo, hi)

        if adv <= _R_FULL_FAIL:
            self.state = TradeState.FAILED_WAIT_REENTRY
            return [TradeEvent.FULL_FAIL_EXIT]

        if fav >= _R_TP:
            self.state = TradeState.FINISHED
            return [TradeEvent.POSITION1_CLOSE_TP]

        if fav >= _R_ADD_POS2:
            self.state = TradeState.POS2_ACTIVE
            return [TradeEvent.POSITION2_ADDED]

        if adv <= _R_NEGATIVE:
            self.state = TradeState.NEGATIVE_WATCH

        return []

    def _handle_pos2_active(self, lo: float, hi: float) -> list[TradeEvent]:
        """
        Favorable first — TP takes priority over a same-bar BE pullback.
        """
        fav = self._favorable_r(lo, hi)
        adv = self._adverse_r(lo, hi)

        if fav >= _R_TP:
            self.state = TradeState.FINISHED
            return [TradeEvent.POSITION1_CLOSE_TP]

        if self._enable_pos2_loss_floor and adv <= _R_FULL_FAIL:
            self.state = TradeState.FAILED_WAIT_REENTRY
            return [TradeEvent.FULL_FAIL_EXIT]

        if adv <= _R_CLOSE_POS2_BE:
            self.state = TradeState.POS1_ONLY_POST_BE
            return [TradeEvent.POSITION2_CLOSE_BE]

        return []

    def _handle_pos1_only(self, lo: float, hi: float) -> list[TradeEvent]:
        """
        Pos-1 alone post-BE. TP at +0.5R; SL extended to -1R.
        Favorable first — TP takes priority over a same-bar SL touch.
        """
        fav = self._favorable_r(lo, hi)
        adv = self._adverse_r(lo, hi)

        if fav >= _R_TP:
            self.state = TradeState.FINISHED
            return [TradeEvent.POSITION1_CLOSE_TP]

        if adv <= _R_SL_POS1_ALONE:
            self.state = TradeState.FAILED_WAIT_REENTRY
            return [TradeEvent.FULL_FAIL_EXIT]

        return []

    def _handle_negative_watch(self, lo: float, hi: float) -> list[TradeEvent]:
        """
        In drawdown between -0.25R and -0.5R.
        Adverse first — a full wipeout bar resolves as FAIL.
        """
        adv = self._adverse_r(lo, hi)
        fav = self._favorable_r(lo, hi)

        if adv <= _R_FULL_FAIL:
            self.state = TradeState.FAILED_WAIT_REENTRY
            return [TradeEvent.FULL_FAIL_EXIT]

        if fav >= _R_TP:
            self.state = TradeState.FINISHED
            return [TradeEvent.POSITION1_CLOSE_TP]

        return []

    def _handle_failed_wait(self, lo: float, hi: float) -> list[TradeEvent]:
        """
        Waiting for a re-entry signal.

        Price-based re-entry fires when the market returns to the entry
        level (0R) in the trade direction — the original entry price is
        revisited, offering a second bite. Caller may also fire
        trigger_reentry() directly from a structural event.
        """
        fav = self._favorable_r(lo, hi)

        if fav >= _R_CLOSE_POS2_BE:
            self.state = TradeState.REENTRY_ACTIVE
            return [TradeEvent.REENTRY_TRIGGERED]

        return []

    def _handle_reentry_active(self, lo: float, hi: float) -> list[TradeEvent]:
        """
        Re-entry trade active.

        Uses the same TP and SL levels as the original trade (+0.5R / -1R).
        Favorable first.
        """
        fav = self._favorable_r(lo, hi)
        adv = self._adverse_r(lo, hi)

        if fav >= _R_TP:
            self.state = TradeState.FINISHED
            return [TradeEvent.REENTRY_CLOSE_TP]

        if adv <= _R_SL_POS1_ALONE:
            self.state = TradeState.FINISHED
            return [TradeEvent.REENTRY_CLOSE_SL]

        return []

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _favorable_r(self, lo: float, hi: float) -> float:
        """R of the bar extreme in the favorable (profit) direction."""
        if self._direction == Direction.BULL:
            return self.r_value(hi)
        return self.r_value(lo)

    def _adverse_r(self, lo: float, hi: float) -> float:
        """R of the bar extreme in the adverse (loss) direction."""
        if self._direction == Direction.BULL:
            return self.r_value(lo)
        return self.r_value(hi)
