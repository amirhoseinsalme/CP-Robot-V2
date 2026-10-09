"""
Supervisor API routes — training, analysis, and monitor status.

Marking a suggestion "applied" records the trader's decision only; it
never edits config.json. Settings changes stay a deliberate action on
the Settings page.

The provider API key is write-only over HTTP: it is accepted on POST and
never returned, so it cannot be read back out of the panel.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from captainpips.supervisor.analyzer import Supervisor
from captainpips.supervisor.connector import AIConnector
from captainpips.supervisor.live_monitor import LiveMonitor
from captainpips.supervisor.memory import SupervisorMemory
from captainpips.supervisor.trainer import Trainer

log = logging.getLogger(__name__)

router = APIRouter()

PROVIDERS = ("anthropic", "openai", "gemini")
_ACTIONS = {"apply": "applied", "reject": "rejected"}

_memory = SupervisorMemory()
_trainer = Trainer(_memory)
_monitor = LiveMonitor(_memory)

_API_CONFIG_PATH = os.path.join(_memory.base_dir, "api_config.json")


def get_monitor() -> LiveMonitor:
    return _monitor


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class LessonRequest(BaseModel):
    title: str
    content: str
    category: str = "general"


class AnalyzeRequest(BaseModel):
    mode: str = "session"          # "session" | "optimize"


class ChatRequest(BaseModel):
    message: str


class ApiConfigRequest(BaseModel):
    provider: str = "anthropic"
    model: str = ""
    base_url: str = ""             # blank leaves the stored URL unchanged
    api_key: str = ""              # blank leaves the stored key unchanged


# ---------------------------------------------------------------------------
# Provider config
# ---------------------------------------------------------------------------

@router.get("/api/supervisor/config")
def get_api_config() -> dict:
    """Current provider settings. The key itself is never returned."""
    stored = _read_api_config()
    connector = AIConnector(stored)
    return {
        "provider": connector.provider,
        "model": connector.model,
        "base_url": connector.base_url,
        "key_set": bool(connector.api_key),
        "key_from_env": not stored.get("api_key") and bool(connector.api_key),
        "providers": list(PROVIDERS),
    }


@router.post("/api/supervisor/config")
def save_api_config(body: ApiConfigRequest) -> dict:
    if body.provider not in PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unknown provider {body.provider!r}")

    stored = _read_api_config()
    stored["provider"] = body.provider
    if body.model.strip():
        stored["model"] = body.model.strip()
    if body.base_url.strip():
        stored["base_url"] = body.base_url.strip()
    # An empty field means "keep the existing key" so the page can save
    # other fields without the key ever being sent to the browser.
    if body.api_key.strip():
        stored["api_key"] = body.api_key.strip()

    try:
        with open(_API_CONFIG_PATH, "w", encoding="utf-8") as fh:
            json.dump(stored, fh, indent=2)
    except OSError as exc:
        log.error("api_config write failed: %s", exc)
        raise HTTPException(status_code=500, detail="Could not save API config") from exc

    log.info("supervisor api config saved: provider=%s", body.provider)
    return get_api_config()


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------

@router.get("/api/supervisor/lessons")
def list_lessons() -> dict:
    return {"lessons": _trainer.list_lessons()}


@router.post("/api/supervisor/lesson")
def add_lesson(body: LessonRequest) -> dict:
    if not body.title.strip() or not body.content.strip():
        raise HTTPException(status_code=400, detail="Title and content are required")
    lesson = _trainer.add_lesson(body.title, body.content, body.category)
    return {"ok": True, "lesson": lesson}


@router.delete("/api/supervisor/lesson/{lesson_id}")
def delete_lesson(lesson_id: str) -> dict:
    # Typed as str so hand-authored ids like "lesson_001" are accepted
    # alongside the integer ids assigned to lessons added through the panel.
    if not _memory.delete_lesson(lesson_id):
        raise HTTPException(status_code=404, detail=f"Lesson {lesson_id} not found")
    return {"ok": True}


@router.post("/api/supervisor/lesson/{lesson_id}")
def update_lesson(lesson_id: str, body: LessonRequest) -> dict:
    if not body.title.strip() or not body.content.strip():
        raise HTTPException(status_code=400, detail="Title and content are required")
    record = _memory.update_lesson(lesson_id, body.title, body.content, body.category)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Lesson {lesson_id} not found")
    return {"ok": True, "lesson": record}


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

@router.post("/api/supervisor/analyze")
async def analyze(body: AnalyzeRequest) -> dict:
    if body.mode not in ("session", "optimize"):
        raise HTTPException(status_code=400, detail=f"Unknown mode {body.mode!r}")

    supervisor = Supervisor(_memory, AIConnector.from_file(_API_CONFIG_PATH))
    if body.mode == "optimize":
        return await supervisor.suggest_optimization()
    return await supervisor.analyze_session()


@router.post("/api/supervisor/suggestion/{suggestion_id}/{action}")
def act_on_suggestion(suggestion_id: int, action: str) -> dict:
    status = _ACTIONS.get(action.lower())
    if status is None:
        raise HTTPException(
            status_code=400,
            detail=f"Action must be one of {sorted(_ACTIONS)}",
        )

    record = _memory.update_suggestion(suggestion_id, status)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Suggestion {suggestion_id} not found")
    return {"ok": True, "suggestion": record}


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

@router.get("/api/supervisor/status")
def status() -> dict:
    observations = _memory.get_observations(200)
    trades = _memory.get_recent_trades(200)
    today = datetime.now(timezone.utc).date().isoformat()

    return {
        "monitor_running": _monitor.is_running,
        "connected": _monitor.is_connected,
        "ws_url": _monitor.ws_url,
        "events_today": sum(
            1 for r in (*observations, *trades)
            if str(r.get("created_at", "")).startswith(today)
        ),
        "events_session": _monitor.event_count,
        "observation_count": len(observations),
        "trade_count": len(trades),
        "lesson_count": len(_memory.get_lessons()),
        "recent_observations": list(reversed(observations[-5:])),
        "pending_suggestions": _memory.get_suggestions("pending"),
    }


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------

@router.post("/api/supervisor/chat")
async def chat(body: ChatRequest) -> dict:
    """Chat with the supervisor AI about trades, strategy, and optimization."""
    if not body.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    connector = AIConnector.from_file(_API_CONFIG_PATH)

    # Build context
    trades = _memory.get_recent_trades(20)
    lessons = _memory.get_lessons()
    today = datetime.now(timezone.utc).date().isoformat()
    today_trades = [t for t in trades if str(t.get("created_at", "")).startswith(today)]

    lessons_text = "\n".join([
        f"- [{l.get('category', 'general')}] {l.get('title', 'untitled')}: {l.get('content', '')}"
        for l in lessons
    ]) if lessons else "No lessons on record."

    trades_text = "\n".join([
        f"- {t.get('symbol')}: {t.get('direction')} @ {t.get('entry_price')}, "
        f"SL={t.get('sl_price')}, status={t.get('status')}"
        for t in today_trades
    ]) if today_trades else "No trades today."

    system_prompt = """\
You are a trading supervisor for CaptainPips Robot.
You know the trading rules, current performance, and configuration.
Answer questions about trades, strategy, performance, and optimization.
Be concise and focus on actionable insights."""

    context_message = f"""\
LESSONS:
{lessons_text}

TODAY'S TRADES:
{trades_text}

QUESTION:
{body.message}"""

    try:
        reply = await connector.think(system_prompt, context_message)
        return {"ok": True, "reply": reply}
    except Exception as exc:
        log.error(f"Chat error: {type(exc).__name__}: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_api_config() -> dict:
    if not os.path.exists(_API_CONFIG_PATH):
        return {}
    try:
        with open(_API_CONFIG_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        log.error("api_config read failed: %s", exc)
        return {}
    return data if isinstance(data, dict) else {}
