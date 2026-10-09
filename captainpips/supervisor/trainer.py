"""
Training mode — the trader teaches the Supervisor.

Lessons are free-text notes the trader writes about how the strategy
should behave. They are injected into every analysis prompt so the AI
reasons from the trader's own rules rather than generic trading advice.
"""

from __future__ import annotations

import logging

from captainpips.supervisor.memory import SupervisorMemory

log = logging.getLogger(__name__)


class Trainer:
    """Records and formats trader-supplied lessons."""

    def __init__(self, memory: SupervisorMemory) -> None:
        self.memory = memory

    def add_lesson(
        self, title: str, content: str, category: str = "general",
    ) -> dict:
        """Store one lesson and return it with its assigned id."""
        lesson = self.memory.save_lesson({
            "title": title.strip(),
            "content": content.strip(),
            "category": category.strip() or "general",
        })
        log.info("lesson %d saved: %s", lesson["id"], lesson["title"])
        return lesson

    def get_lessons_summary(self) -> str:
        """Render every lesson as prompt-ready text, grouped by category."""
        lessons = self.memory.get_lessons()
        if not lessons:
            return "No lessons recorded."

        grouped: dict[str, list[dict]] = {}
        for lesson in lessons:
            grouped.setdefault(lesson.get("category", "general"), []).append(lesson)

        blocks = []
        for category in sorted(grouped):
            lines = [f"[{category.upper()}]"]
            for lesson in grouped[category]:
                lines.append(
                    f"  {lesson.get('title', 'untitled')}: "
                    f"{lesson.get('content', '')}"
                )
            blocks.append("\n".join(lines))

        return "\n\n".join(blocks)

    def list_lessons(self) -> list[dict]:
        return self.memory.get_lessons()
