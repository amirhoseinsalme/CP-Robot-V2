"""
Central configuration for CaptainPips.

All sections are Pydantic models. Load from JSON with Config.load(),
save back with Config.save(). Missing file produces defaults.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import BaseModel

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config sections
# ---------------------------------------------------------------------------

class ZmqConfig(BaseModel):
    push_port: int = 5555
    pull_port: int = 5556
    sub_port: int = 5557
    history_timeout_ms: int = 30_000
    send_timeout_ms: int = 200
    recv_timeout_ms: int = 2_000


class SymbolConfig(BaseModel):
    symbol: str
    enabled: bool = True
    session_start: str = "16:30"
    session_end: str = "18:30"
    sl_pips: float = 60.0
    tp_pips: float = 30.0
    pip_value: float = 1.0
    tick_size: float = 1.0
    tick_value: float = 1.0
    min_lot: float = 0.01
    lot_step: float = 0.01
    max_lot: float = 50.0


class SlConfig(BaseModel):
    type: str = "fixed"              # "fixed" or "structural"
    fixed_pips: float = 60.0
    structural_leg2chain: str = "leg1_extreme"   # "leg1_extreme" | "stop1_extreme"
    structural_leg3drive: str = "stop1_extreme"  # "leg1_extreme" | "stop1_extreme"


class TpConfig(BaseModel):
    type: str = "fixed"              # "fixed" or "r_based"
    fixed_pips: float = 30.0
    r_multiplier: float = 0.5        # used when type="r_based"


class Position2Config(BaseModel):
    enabled: bool = True
    risk_pct: float = 1.0
    sl_pips: float = 60.0
    entry_r: float = 0.25            # open pos2 when pos1 reaches +0.25R


class RiskConfig(BaseModel):
    risk_pct: float = 1.0
    max_open_positions: int = 5
    max_daily_loss_pct: float = 5.0
    robot_running: bool = True
    sl_pips: float = 60.0            # legacy flat field kept for migration compat
    tp_pips: float = 30.0            # legacy flat field kept for migration compat
    sl: SlConfig = SlConfig()
    tp: TpConfig = TpConfig()
    position2: Position2Config = Position2Config()


class PositionConfig(BaseModel):
    position1_multiplier: float = 0.5
    position2_multiplier: float = 3.0
    reentry_multiplier: float = 0.5
    enable_pos2_loss_floor: bool = False


class AppConfig(BaseModel):
    api_port: int = 8000
    api_host: str = "127.0.0.1"
    db_path: str = "captainpips.db"
    log_level: str = "INFO"


# ---------------------------------------------------------------------------
# Root config
# ---------------------------------------------------------------------------

class Config(BaseModel):
    zmq: ZmqConfig = ZmqConfig()
    symbols: list[SymbolConfig] = []
    risk: RiskConfig = RiskConfig()
    position: PositionConfig = PositionConfig()
    app: AppConfig = AppConfig()

    @classmethod
    def load(cls, path: str = "config.json") -> Config:
        """Load from JSON file. Returns default config if file doesn't exist."""
        p = Path(path)
        if not p.exists():
            log.info("Config file not found at %s — using defaults", path)
            return cls()
        with p.open(encoding="utf-8") as f:
            data = json.load(f)
        return cls.model_validate(data)

    def save(self, path: str = "config.json") -> None:
        """Serialize to JSON file, creating parent directories as needed."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("w", encoding="utf-8") as f:
            f.write(self.model_dump_json(indent=2))
        log.info("Config saved to %s", path)

    def enabled_symbols(self) -> list[SymbolConfig]:
        """Return only symbols with enabled=True."""
        return [s for s in self.symbols if s.enabled]
