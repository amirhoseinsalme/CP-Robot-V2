"""
Abstract base class for all CaptainPips entry strategies.

Imports only from definitions and structure_state.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from captainpips.core.definitions import Candle, Direction, Segment, Signal
from captainpips.core.structure_state import StructureEvent


# ---------------------------------------------------------------------------
# Entry watch
# ---------------------------------------------------------------------------

@dataclass
class EntryWatch:
    """
    An armed watch for a potential entry.

    Created by a strategy's on_structure_event and passed back into
    on_candle every bar until the entry triggers or the watch is cancelled.
    """

    strategy: str
    leg_number: int
    mode: str                      # "A" — immediate; "B" — pending breakout
    trigger_price: float
    trigger_direction: Direction
    pending_bo_index: int | None   # bar index when Mode B breakout armed; None for Mode A
    prev_candle: Candle | None = None  # last candle seen by on_candle; used for BO checks


# ---------------------------------------------------------------------------
# Abstract base strategy
# ---------------------------------------------------------------------------

class BaseStrategy(ABC):
    """
    Contract every entry strategy must satisfy.

    The runner calls on_structure_event whenever the structure changes.
    If a watch is returned it calls on_candle each subsequent bar with
    that watch until a Signal is returned or the watch is cancelled.
    """

    @abstractmethod
    def on_structure_event(
        self,
        event: StructureEvent,
        segments: list[Segment],
        confirmed_legs: int,
        candle: Candle,
    ) -> EntryWatch | None:
        """
        Evaluate a structural event and decide whether to arm for entry.

        Args:
            event:          The structural event just emitted.
            segments:       Full ordered segment list at this moment.
            confirmed_legs: Geometrically confirmed leg count.
            candle:         The candle that triggered the event.

        Returns:
            An EntryWatch to arm, or None to pass.
        """

    @abstractmethod
    def on_candle(
        self,
        candle: Candle,
        watch: EntryWatch,
        segments: list[Segment],
    ) -> Signal | None:
        """
        Check whether an armed watch has triggered on this candle.

        Args:
            candle:   The current bar.
            watch:    The active EntryWatch created by on_structure_event.
            segments: Full ordered segment list at this moment.

        Returns:
            A Signal if the entry condition is met, or None to keep watching.
        """

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable strategy identifier, e.g. 'Leg2Chain'."""
