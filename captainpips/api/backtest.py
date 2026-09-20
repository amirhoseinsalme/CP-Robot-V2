from fastapi import APIRouter, UploadFile, File, Form
from fastapi.responses import HTMLResponse
import csv, io

router = APIRouter()

@router.post("/api/backtest/run")
async def run_backtest(
    file: UploadFile = File(...),
    symbol: str = Form("US30.U26"),
):
    # 1. Read CSV
    content = await file.read()
    text = content.decode("utf-8")
    reader = csv.DictReader(io.StringIO(text),
                            delimiter="\t",
                            fieldnames=["date","time","open","high","low","close","vol","x","y"])
    candles = []
    for row in reader:
        try:
            candles.append({
                "time": f"{row['date']} {row['time']}",
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
            })
        except Exception:
            continue

    # 2. Run engine
    from captainpips.backtest.engine import BacktestEngine
    from captainpips.config import Config

    cfg = Config.load()
    # Get session times from first enabled symbol config
    sc = next((s for s in cfg.symbols if s.enabled), None)
    from_time = sc.session_start if sc else "00:00"
    to_time = sc.session_end if sc else "23:59"

    engine = BacktestEngine(cfg)
    result = engine.run(candles, symbol=symbol, from_time=from_time, to_time=to_time)

    # 3. Return JSON result
    return {
        "total_trades": result.total_trades,
        "winning_trades": result.winning_trades,
        "win_rate": round(result.win_rate, 1),
        "total_pnl": round(result.total_pnl, 2),
        "max_drawdown": round(result.max_drawdown, 2),
        "profit_factor": round(result.profit_factor, 2),
        "final_balance": round(result.final_balance, 2),
        "equity_curve": result.equity_curve,
        "trades": [
            {
                "strategy": t.strategy,
                "direction": t.direction.value,
                "entry_price": t.entry_price,
                "entry_time": t.entry_time,
                "sl_price": t.sl_price,
                "tp_price": t.tp_price,
                "lot": t.lot,
                "exit_price": t.exit_price,
                "exit_time": t.exit_time,
                "exit_reason": t.exit_reason,
                "pnl": round(t.pnl, 2),
            }
            for t in result.trades
        ],
    }
