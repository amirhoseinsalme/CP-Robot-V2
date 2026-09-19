"""
Risk gating — evaluated before any trade is opened.

Imports only from config.
"""

from __future__ import annotations

from captainpips.config import RiskConfig


class RiskManager:
    """Stateless gate that checks risk rules in priority order."""

    def __init__(self, config: RiskConfig) -> None:
        self._cfg = config

    def can_trade(
        self,
        symbol: str,
        active_trade_count: int,
        daily_loss_pct: float,
        robot_running: bool,
    ) -> tuple[bool, str]:
        """
        Return (True, "") when a new trade is allowed, or (False, reason) when blocked.

        Checks are evaluated in strict priority order — the first failing
        check wins and subsequent checks are not evaluated.

        Parameters
        ----------
        symbol:             Symbol being considered (reserved for future per-symbol rules).
        active_trade_count: Number of currently open positions across all symbols.
        daily_loss_pct:     Today's realised loss as a percentage of starting balance.
        robot_running:      Live value of the robot_running flag (can be toggled at runtime).
        """
        if not robot_running:
            return False, "Robot is paused"

        if active_trade_count >= self._cfg.max_open_positions:
            return False, "Max positions reached"

        if daily_loss_pct >= self._cfg.max_daily_loss_pct:
            return False, "Daily loss limit hit"

        return True, ""

    def update_config(self, config: RiskConfig) -> None:
        """Hot-reload risk settings without restarting the bot."""
        self._cfg = config
