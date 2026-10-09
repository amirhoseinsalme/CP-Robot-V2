"""
Supervisor — reviews trade history and proposes improvements.

Everything the AI returns is stored as a *pending* suggestion. Nothing
here edits config.json or touches the running robot; applying a
suggestion is always a separate, explicit user action.
"""

from __future__ import annotations

import json
import logging
import re

from captainpips.supervisor.connector import AIConnector, AIConnectorError
from captainpips.supervisor.memory import SupervisorMemory

log = logging.getLogger(__name__)


CP_RULES = """\
You are reviewing trades from CaptainPips, a price-action robot that
trades market structure. Reference model:

STRUCTURE
- Price is parsed into alternating segments: LEG, STOP, LEG, STOP...
- A structure has one direction (BULL or BEAR).
- Point A = a leg's origin. Point B = a leg's furthest extreme.
- A leg is confirmed when the staircase extends (each leg endpoint goes
  beyond the previous leg's endpoint) AND the stop between stays nested
  (it does not breach the prior leg's start).
- The structure breaks when the current stop's retracement exceeds the
  previous stop's retracement, or when a single leg is fully reversed.

STRATEGIES
- Leg2Chain arms after Leg 1 confirms and Stop 1 opens, once the stop
  shows a correction (a breakout in the stop's own direction).
  Mode A: immediate entry on a leg-direction breakout inside a 2-candle
  window, valid only if no opposite-by-close candle appeared first.
  Mode B: engaged when an opposite-by-close candle appears; then requires
  a leg-direction breakout confirmed by follow-through within 2 bars.
- Leg3Drive arms at 2 confirmed legs when point D extends beyond point B.

RISK
- Stop loss is fixed pips or structural (a leg/stop extreme).
- Take profit is fixed pips or an R multiple of the stop distance.
- A second position may scale in at +0.25R, after which the first
  position's stop moves to breakeven.

Judge the data only. Do not invent rules that are not described above.\
"""

_ANALYSIS_INSTRUCTION = """\
Return your answer as a JSON object with exactly these keys:
  "summary":     one paragraph of plain prose
  "patterns":    array of {"title", "evidence", "confidence"} where
                 confidence is "low", "medium", or "high"
  "suggestions": array of {"title", "change", "rationale"} where
                 "change" names the setting or behaviour to adjust
Output the JSON object and nothing else. If the sample is too small to
support a conclusion, say so in "summary" and return empty arrays.\
"""


class Supervisor:
    """Pairs stored history with an AI connector to produce review reports."""

    def __init__(self, memory: SupervisorMemory, connector: AIConnector) -> None:
        self.memory = memory
        self.connector = connector

    # ------------------------------------------------------------------
    # Session review
    # ------------------------------------------------------------------

    async def analyze_session(self, trade_count: int = 50) -> dict:
        """Review recent trades, or analyze the rules and lessons if no trades exist."""
        trades = self.memory.get_recent_trades(trade_count)
        stats = summarize_trades(trades)

        if trades:
            message = "\n\n".join([
                self._knowledge_block(),
                f"TRADE STATS\n{json.dumps(stats, indent=2)}",
                f"TRADES (most recent {len(trades)})\n{json.dumps(trades, indent=2, default=str)}",
                "Identify recurring patterns in these trades and what to change.",
                _ANALYSIS_INSTRUCTION,
            ])
        else:
            # No trades yet — analyze the configuration against the rules and lessons
            log.info("analyze_session: no trades recorded, doing general analysis")
            message = "\n\n".join([
                self._knowledge_block(),
                "Based on the CP Robot trading rules and these lessons, analyze the strategy "
                "and suggest improvements for when trading begins.",
                _ANALYSIS_INSTRUCTION,
            ])

        report = await self._ask(CP_RULES, message, stats)
        self._store(report, source="analyze_session")
        return report

    # ------------------------------------------------------------------
    # Risk optimization
    # ------------------------------------------------------------------

    async def suggest_optimization(self, trade_count: int = 100) -> dict:
        """Propose stop-loss, take-profit, and risk adjustments."""
        trades = self.memory.get_recent_trades(trade_count)
        stats = summarize_trades(trades)

        if len(trades) < 10:
            log.info("suggest_optimization: only %d trades — too few", len(trades))
            return {
                "ok": True,
                "stats": stats,
                "summary": (
                    f"Only {len(trades)} trades recorded. At least 10 are "
                    "needed before risk settings should be changed."
                ),
                "patterns": [],
                "suggestions": [],
            }

        message = "\n\n".join([
            self._knowledge_block(),
            f"TRADE STATS\n{json.dumps(stats, indent=2)}",
            f"TRADES (most recent {len(trades)})\n{json.dumps(trades, indent=2, default=str)}",
            "Compare the winning and losing trades. Recommend concrete "
            "stop-loss, take-profit, and position-risk adjustments, and say "
            "what the trade data shows for each one.",
            _ANALYSIS_INSTRUCTION,
        ])

        report = await self._ask(CP_RULES, message, stats)
        self._store(report, source="suggest_optimization")
        return report

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _knowledge_block(self) -> str:
        """Lessons and patterns already in memory, for prompt context."""
        sections = []

        lessons = self.memory.get_lessons()
        if lessons:
            lines = [
                f"- [{item.get('category', 'general')}] "
                f"{item.get('title', 'untitled')}: {item.get('content', '')}"
                for item in lessons
            ]
            sections.append("LESSONS TAUGHT BY THE TRADER\n" + "\n".join(lines))

        patterns = self.memory.get_patterns()
        if patterns:
            lines = [
                f"- {item.get('title', 'untitled')}: {item.get('evidence', '')}"
                for item in patterns
            ]
            sections.append("PATTERNS ALREADY ON RECORD\n" + "\n".join(lines))

        return "\n\n".join(sections) if sections else "No prior lessons on record."

    async def _ask(self, system_prompt: str, message: str, stats: dict) -> dict:
        try:
            raw = await self.connector.think(system_prompt, message)
        except AIConnectorError as exc:
            log.error("supervisor request failed: %s", exc)
            return {
                "ok": False,
                "error": str(exc),
                "stats": stats,
                "summary": "",
                "patterns": [],
                "suggestions": [],
            }

        parsed = _parse_report(raw)
        parsed["ok"] = True
        parsed["stats"] = stats
        return parsed

    def _store(self, report: dict, source: str) -> None:
        """Persist patterns and suggestions from a successful report."""
        if not report.get("ok"):
            return

        for pattern in report.get("patterns", []):
            if isinstance(pattern, dict):
                self.memory.save_pattern({**pattern, "source": source})

        stored = []
        for suggestion in report.get("suggestions", []):
            if isinstance(suggestion, dict):
                stored.append(
                    self.memory.save_suggestion({**suggestion, "source": source})
                )
        report["suggestions"] = stored

        log.info(
            "%s: stored %d patterns, %d suggestions",
            source, len(report.get("patterns", [])), len(stored),
        )


# ---------------------------------------------------------------------------
# Trade statistics
# ---------------------------------------------------------------------------

def summarize_trades(trades: list[dict]) -> dict:
    """
    Reduce raw trade records to win/loss counts and totals.

    Tolerant of field naming: profit is read from pnl / profit / result_r,
    and a trade counts as a win when that value is positive.
    """
    wins = losses = flat = 0
    total = 0.0
    by_strategy: dict[str, dict[str, int]] = {}

    for trade in trades:
        value = _profit_of(trade)
        if value is None:
            flat += 1
        elif value > 0:
            wins += 1
            total += value
        elif value < 0:
            losses += 1
            total += value
        else:
            flat += 1

        name = str(trade.get("strategy") or "unknown")
        bucket = by_strategy.setdefault(name, {"wins": 0, "losses": 0})
        if value is not None and value > 0:
            bucket["wins"] += 1
        elif value is not None and value < 0:
            bucket["losses"] += 1

    decided = wins + losses
    return {
        "count": len(trades),
        "wins": wins,
        "losses": losses,
        "undetermined": flat,
        "win_rate": round(wins / decided, 3) if decided else None,
        "net": round(total, 2),
        "by_strategy": by_strategy,
    }


def _profit_of(trade: dict) -> float | None:
    for key in ("pnl", "profit", "result_r", "r_multiple"):
        value = trade.get(key)
        if isinstance(value, (int, float)):
            return float(value)

    outcome = trade.get("outcome") or trade.get("result")
    if isinstance(outcome, str):
        lowered = outcome.strip().lower()
        if lowered in ("win", "tp", "target"):
            return 1.0
        if lowered in ("loss", "lose", "sl", "fail", "stopped"):
            return -1.0
    return None


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

def _parse_report(raw: str) -> dict:
    """
    Pull the JSON report out of a model reply.

    Falls back to keeping the reply as a prose summary when it is not
    valid JSON, so a malformed response never loses the analysis.
    """
    text = raw.strip()

    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    else:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            text = text[start:end + 1]

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        log.warning("report was not valid JSON — keeping it as prose")
        return {"summary": raw.strip(), "patterns": [], "suggestions": []}

    if not isinstance(data, dict):
        return {"summary": raw.strip(), "patterns": [], "suggestions": []}

    return {
        "summary": data.get("summary", ""),
        "patterns": data.get("patterns") or [],
        "suggestions": data.get("suggestions") or [],
    }
