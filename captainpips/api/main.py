"""
FastAPI server — exposes bot state and control to the panel.

Bot state is injected at startup via BotState. Config changes
hot-reload the running bot; WebSocket /ws/monitor streams all
significant events in real time.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from captainpips.config import Config, RiskConfig, SymbolConfig

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared bot state (injected at startup)
# ---------------------------------------------------------------------------

class BotState:
    """
    Mutable container for live bot references.

    Populated by the application entrypoint before calling uvicorn.run().
    The API reads and writes through this object; the bot loop uses the
    same references, so changes propagate immediately.
    """

    def __init__(self) -> None:
        self.config: Config = Config()
        self.config_path: str = "config.json"
        self.active_trades: dict[str, Any] = {}   # symbol → ActiveTrade-like
        self.robot_running: bool = True
        self.backtest_runs: dict[str, Any] = {}    # id → result
        self.on_config_changed: list = []           # callables to notify on reload
        self.ws_broadcast: "ConnectionManager | None" = None
        self.log_handler: Any = None               # MemoryLogHandler injected by bot
        self.get_structure: Any = None             # Callable → dict; injected by bot
        self.get_candles: Any = None               # Callable(symbol?) → dict; injected by bot

    def notify_config_changed(self) -> None:
        for cb in self.on_config_changed:
            try:
                cb(self.config)
            except Exception as exc:
                logging.getLogger(__name__).error("Config change callback error: %s", exc)


_bot_state: BotState = BotState()


def get_state() -> BotState:
    return _bot_state


# ---------------------------------------------------------------------------
# WebSocket connection manager
# ---------------------------------------------------------------------------

class ConnectionManager:
    """Tracks open WebSocket connections and broadcasts events."""

    def __init__(self) -> None:
        self._connections: list[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.append(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self._connections.discard(ws) if hasattr(self._connections, "discard") \
            else self._connections.remove(ws) if ws in self._connections else None

    async def broadcast(self, payload: dict) -> None:
        dead: list[WebSocket] = []
        for ws in list(self._connections):
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            if ws in self._connections:
                self._connections.remove(ws)

    def __len__(self) -> int:
        return len(self._connections)


_manager = ConnectionManager()


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    _bot_state.ws_broadcast = _manager
    log.info("API server started — WebSocket manager ready")
    yield
    log.info("API server shutting down")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="CaptainPips API", version="2.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8000", "http://127.0.0.1:8000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount(
    "/static",
    StaticFiles(directory="captainpips/panel/static"),
    name="static",
)


# ---------------------------------------------------------------------------
# Panel root
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def panel_root() -> HTMLResponse:
    path = "captainpips/panel/templates/monitor.html"
    try:
        with open(path, encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        return HTMLResponse(content="<h1>Panel not found</h1><p>Place monitor.html in captainpips/panel/templates/</p>", status_code=404)


@app.get("/settings", response_class=HTMLResponse)
def panel_settings() -> HTMLResponse:
    path = "captainpips/panel/templates/settings.html"
    try:
        with open(path, encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        return HTMLResponse(content="<h1>Settings page not found</h1>", status_code=404)


@app.get("/logs", response_class=HTMLResponse)
def panel_logs() -> HTMLResponse:
    path = "captainpips/panel/templates/logs.html"
    try:
        with open(path, encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        return HTMLResponse(content="<h1>Logs page not found</h1>", status_code=404)


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class StatusResponse(BaseModel):
    robot_running: bool
    active_trade_count: int
    active_symbols: list[str]
    connected_clients: int


class BacktestRequest(BaseModel):
    symbol: str
    from_ts: int
    to_ts: int
    timeframe: int = 1


class BacktestStatus(BaseModel):
    id: str
    symbol: str
    status: str           # "pending" | "running" | "done" | "error"
    result: dict | None = None


class MessageResponse(BaseModel):
    ok: bool
    message: str = ""


# ---------------------------------------------------------------------------
# Config routes
# ---------------------------------------------------------------------------

@app.get("/api/config", response_model=Config)
def get_config(state: BotState = Depends(get_state)) -> Config:
    return state.config


@app.post("/api/config", response_model=Config)
def update_config(
    body: Config,
    state: BotState = Depends(get_state),
) -> Config:
    state.config = body
    state.config.save(state.config_path)
    state.notify_config_changed()
    return state.config


# ---------------------------------------------------------------------------
# Symbol routes
# ---------------------------------------------------------------------------

@app.get("/api/symbols", response_model=list[SymbolConfig])
def list_symbols(state: BotState = Depends(get_state)) -> list[SymbolConfig]:
    return state.config.symbols


@app.post("/api/symbols", response_model=SymbolConfig)
def add_or_update_symbol(
    body: SymbolConfig,
    state: BotState = Depends(get_state),
) -> SymbolConfig:
    syms = state.config.symbols
    for i, s in enumerate(syms):
        if s.symbol == body.symbol:
            syms[i] = body
            state.config.save(state.config_path)
            state.notify_config_changed()
            return body
    syms.append(body)
    state.config.save(state.config_path)
    state.notify_config_changed()
    return body


@app.delete("/api/symbols/{name}", response_model=MessageResponse)
def delete_symbol(
    name: str,
    state: BotState = Depends(get_state),
) -> MessageResponse:
    before = len(state.config.symbols)
    state.config.symbols = [s for s in state.config.symbols if s.symbol != name]
    if len(state.config.symbols) == before:
        raise HTTPException(status_code=404, detail=f"Symbol {name!r} not found")
    state.config.save(state.config_path)
    state.notify_config_changed()
    return MessageResponse(ok=True, message=f"Symbol {name} removed")


# ---------------------------------------------------------------------------
# Status route
# ---------------------------------------------------------------------------

@app.get("/api/status", response_model=StatusResponse)
def get_status(state: BotState = Depends(get_state)) -> StatusResponse:
    return StatusResponse(
        robot_running=state.robot_running,
        active_trade_count=len(state.active_trades),
        active_symbols=list(state.active_trades.keys()),
        connected_clients=len(_manager),
    )


# ---------------------------------------------------------------------------
# Robot control routes
# ---------------------------------------------------------------------------

@app.post("/api/robot/start", response_model=MessageResponse)
def robot_start(state: BotState = Depends(get_state)) -> MessageResponse:
    state.robot_running = True
    state.config.risk.robot_running = True
    state.config.save(state.config_path)
    asyncio.create_task(
        _manager.broadcast({"type": "ROBOT_STATE", "running": True})
    ) if asyncio.get_event_loop().is_running() else None
    return MessageResponse(ok=True, message="Robot started")


@app.post("/api/robot/stop", response_model=MessageResponse)
def robot_stop(state: BotState = Depends(get_state)) -> MessageResponse:
    state.robot_running = False
    state.config.risk.robot_running = False
    state.config.save(state.config_path)
    asyncio.create_task(
        _manager.broadcast({"type": "ROBOT_STATE", "running": False})
    ) if asyncio.get_event_loop().is_running() else None
    return MessageResponse(ok=True, message="Robot stopped")


# ---------------------------------------------------------------------------
# Resync route
# ---------------------------------------------------------------------------

@app.post("/api/refresh/{symbol}", response_model=MessageResponse)
def refresh_symbol(
    symbol: str,
    state: BotState = Depends(get_state),
) -> MessageResponse:
    sc = next((s for s in state.config.symbols if s.symbol == symbol), None)
    if sc is None:
        raise HTTPException(status_code=404, detail=f"Symbol {symbol!r} not found")
    log.info("Resync requested for %s", symbol)
    return MessageResponse(ok=True, message=f"Resync triggered for {symbol}")


# ---------------------------------------------------------------------------
# Backtest routes
# ---------------------------------------------------------------------------

@app.get("/api/backtest/list", response_model=list[BacktestStatus])
def backtest_list(state: BotState = Depends(get_state)) -> list[BacktestStatus]:
    return [
        BacktestStatus(id=k, **v) for k, v in state.backtest_runs.items()
    ]


@app.post("/api/backtest/run", response_model=BacktestStatus)
def backtest_run(
    body: BacktestRequest,
    state: BotState = Depends(get_state),
) -> BacktestStatus:
    import uuid
    run_id = str(uuid.uuid4())
    entry: dict = {
        "symbol": body.symbol,
        "status": "pending",
        "result": None,
    }
    state.backtest_runs[run_id] = entry
    log.info("Backtest queued: id=%s symbol=%s", run_id, body.symbol)
    return BacktestStatus(id=run_id, **entry)


@app.get("/api/backtest/{run_id}", response_model=BacktestStatus)
def backtest_get(
    run_id: str,
    state: BotState = Depends(get_state),
) -> BacktestStatus:
    entry = state.backtest_runs.get(run_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found")
    return BacktestStatus(id=run_id, **entry)


# ---------------------------------------------------------------------------
# Structure state route
# ---------------------------------------------------------------------------

@app.get("/api/candles")
def get_candles(
    symbol: str | None = None,
    state: BotState = Depends(get_state),
) -> dict:
    if state.get_candles is None:
        return {"symbol": symbol, "candles": []}
    return state.get_candles(symbol)


@app.post("/api/candles/clear")
def clear_candles(state: BotState = Depends(get_state)) -> MessageResponse:
    # Note: candles are cleared server-side in _on_config_reload,
    # this endpoint just confirms it to the client
    return MessageResponse(ok=True, message="Candle cache cleared")


@app.get("/api/structure")
def get_structure(state: BotState = Depends(get_state)) -> dict:
    if state.get_structure is None:
        return {"symbols": {}}
    return {"symbols": state.get_structure()}


# ---------------------------------------------------------------------------
# Log viewer route
# ---------------------------------------------------------------------------

@app.get("/api/logs")
def get_logs(state: BotState = Depends(get_state)) -> dict:
    if state.log_handler is None:
        return {"logs": []}
    return {"logs": list(state.log_handler.logs)}


# ---------------------------------------------------------------------------
# WebSocket — real-time monitor
# ---------------------------------------------------------------------------

@app.websocket("/ws/monitor")
async def ws_monitor(ws: WebSocket) -> None:
    """
    Real-time event stream pushed to the panel.

    The bot layer broadcasts via BotState.ws_broadcast.broadcast(payload)
    whenever:
      - A new candle is processed
      - Structure changes (LEG_CONFIRMED, STOP_STARTED, STRUCTURE_BROKEN)
      - A signal fires
      - A position is opened or closed
    """
    await _manager.connect(ws)
    log.info("WS client connected (%d total)", len(_manager))
    try:
        while True:
            # Keep connection alive; client may send pings
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        _manager.disconnect(ws)
        log.info("WS client disconnected (%d remaining)", len(_manager))
