"""
JSON-backed memory store for the AI Supervisor.

Five independent record files under supervisor_data/. Every record gets
an auto-incrementing ``id`` and an ISO-8601 ``created_at`` stamp so
records can be referenced and aged out later.

Completely independent of the robot code — imports stdlib only.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

# Files that hold high-volume time-series records and are safe to age out.
# Lessons, patterns, and suggestions are curated knowledge and never purged.
_ROLLING_FILES = ("observations", "trades")


class SupervisorMemory:
    """Append-only JSON record store, one file per record type."""

    def __init__(self, base_dir: str | None = None) -> None:
        if base_dir is None:
            base_dir = os.path.join(
                os.path.dirname(__file__), "..", "..", "supervisor_data",
            )
        self.base_dir = os.path.abspath(base_dir)
        os.makedirs(self.base_dir, exist_ok=True)

        self.observations_file = os.path.join(self.base_dir, "observations.json")
        self.lessons_file = os.path.join(self.base_dir, "lessons.json")
        self.patterns_file = os.path.join(self.base_dir, "patterns.json")
        self.trades_file = os.path.join(self.base_dir, "trades.json")
        self.suggestions_file = os.path.join(self.base_dir, "suggestions.json")

        self._files = {
            "observations": self.observations_file,
            "lessons": self.lessons_file,
            "patterns": self.patterns_file,
            "trades": self.trades_file,
            "suggestions": self.suggestions_file,
        }

        # Writes arrive from the live monitor thread and the analyzer loop.
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------

    def save_trade(self, trade: dict) -> dict:
        return self._append("trades", trade)

    def save_observation(self, obs: dict) -> dict:
        return self._append("observations", obs)

    def save_lesson(self, lesson: dict) -> dict:
        return self._append("lessons", lesson)

    def save_pattern(self, pattern: dict) -> dict:
        return self._append("patterns", pattern)

    def save_suggestion(self, suggestion: dict) -> dict:
        record = dict(suggestion)
        record.setdefault("status", "pending")
        return self._append("suggestions", record)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_recent_trades(self, n: int = 50) -> list[dict]:
        return self._load("trades")[-n:]

    def get_lessons(self) -> list[dict]:
        return self._load("lessons")

    def get_patterns(self) -> list[dict]:
        return self._load("patterns")

    def get_observations(self, n: int = 100) -> list[dict]:
        return self._load("observations")[-n:]

    def get_suggestions(self, status: str | None = "pending") -> list[dict]:
        records = self._load("suggestions")
        if status is None:
            return records
        return [r for r in records if r.get("status") == status]

    # ------------------------------------------------------------------
    # Mutate
    # ------------------------------------------------------------------

    def update_suggestion(self, id: int, status: str) -> dict | None:
        """Set a suggestion's status (e.g. 'applied', 'rejected')."""
        with self._lock:
            records = self._read_unlocked("suggestions")
            for record in records:
                if record.get("id") == id:
                    record["status"] = status
                    record["updated_at"] = _now()
                    self._write_unlocked("suggestions", records)
                    return record
        log.warning("update_suggestion: id=%s not found", id)
        return None

    def update_trade(
        self,
        symbol: str,
        exit_price: float,
        exit_time: str,
        pnl: float,
        status: str,
    ) -> dict | None:
        """Update an open trade with exit information. Finds by symbol and open status."""
        with self._lock:
            records = self._read_unlocked("trades")
            for record in records:
                if (record.get("symbol") == symbol and
                    record.get("status") == "open"):
                    record["exit_price"] = exit_price
                    record["exit_time"] = exit_time
                    record["pnl"] = pnl
                    record["status"] = status
                    record["updated_at"] = _now()
                    self._write_unlocked("trades", records)
                    return record
        log.warning("update_trade: no open trade found for %s", symbol)
        return None

    def delete_lesson(self, id: int | str) -> bool:
        """
        Remove one lesson. Returns False when the id is unknown.

        Ids are compared as strings so hand-authored lessons (``lesson_001``)
        and generated ones (``7``) can coexist in the same file.
        """
        target = str(id)
        with self._lock:
            records = self._read_unlocked("lessons")
            kept = [r for r in records if str(r.get("id")) != target]
            if len(kept) == len(records):
                log.warning("delete_lesson: id=%s not found", id)
                return False
            self._write_unlocked("lessons", kept)
        return True

    def update_lesson(
        self, id: int | str, title: str, category: str, content: str,
    ) -> dict | None:
        """Update an existing lesson. Returns the updated record or None if not found."""
        target = str(id)
        with self._lock:
            records = self._read_unlocked("lessons")
            for record in records:
                if str(record.get("id")) == target:
                    record["title"] = title.strip()
                    record["category"] = category.strip() or "general"
                    record["content"] = content.strip()
                    record["updated_at"] = _now()
                    self._write_unlocked("lessons", records)
                    return record
        log.warning("update_lesson: id=%s not found", id)
        return None

    def clear_old_data(self, days: int = 30) -> dict[str, int]:
        """
        Drop observation and trade records older than *days*.

        Lessons, patterns, and suggestions are curated knowledge and are
        left untouched regardless of age.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        removed: dict[str, int] = {}

        with self._lock:
            for key in _ROLLING_FILES:
                records = self._read_unlocked(key)
                kept = [r for r in records if _created_at(r) >= cutoff]
                removed[key] = len(records) - len(kept)
                if removed[key]:
                    self._write_unlocked(key, kept)

        log.info("clear_old_data: days=%d removed=%s", days, removed)
        return removed

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _append(self, key: str, record: dict) -> dict:
        with self._lock:
            records = self._read_unlocked(key)
            stored = dict(record)
            stored["id"] = _next_id(records)
            stored.setdefault("created_at", _now())
            records.append(stored)
            self._write_unlocked(key, records)
        return stored

    def _load(self, key: str) -> list[dict]:
        with self._lock:
            return self._read_unlocked(key)

    def _read_unlocked(self, key: str) -> list[dict]:
        path = self._files[key]
        if not os.path.exists(path):
            return []
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, OSError) as exc:
            log.error("memory read failed for %s: %s", path, exc)
            return []
        return data if isinstance(data, list) else []

    def _write_unlocked(self, key: str, records: list[dict]) -> None:
        path = self._files[key]
        tmp = f"{path}.tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(records, fh, indent=2, default=str)
            os.replace(tmp, path)
        except OSError as exc:
            log.error("memory write failed for %s: %s", path, exc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _next_id(records: list[dict]) -> int:
    ids = [r["id"] for r in records if isinstance(r.get("id"), int)]
    return max(ids) + 1 if ids else 1


def _created_at(record: dict) -> datetime:
    """Parse a record's timestamp; undated records are treated as current."""
    raw = record.get("created_at")
    if not isinstance(raw, str):
        return datetime.now(timezone.utc)
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed
