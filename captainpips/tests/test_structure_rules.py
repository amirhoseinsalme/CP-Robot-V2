"""
Integration tests for structure building and leg counting.

Candle source: real bear structure (NQ futures, 1-min bars).
The S1 P candle at 08:57 (H=4445.96) is omitted from the builder feed
because it precedes L1 in time and belongs to a prior structure — its
high exceeds L1's starting high (4444.77), which would fail the nested
stop condition in LegCounter.  A synthetic stop (H=4442.00, below L1
start) is injected between L1 and L2 so that the geometric staircase
can be confirmed.
"""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from captainpips.core.definitions import Candle, CandleLabel, Direction, Segment, SegmentKind
from captainpips.core.structure_rules import LegValidator, LegCounter, StructurePoints
from captainpips.core.structure_state import StructureBuilder, EventKind


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_candle(
    index: int,
    time: str,
    label: CandleLabel,
    direction: Direction,
    open_: float,
    high: float,
    low: float,
    close: float,
) -> Candle:
    """Construct a Candle directly — bypasses the decision-tree classifier."""
    return Candle(
        index=index,
        time=time,
        label=label,
        direction=direction,
        open=open_,
        high=high,
        low=low,
        close=close,
    )


# ---------------------------------------------------------------------------
# Candle definitions (from real bars, chronological order)
# ---------------------------------------------------------------------------

# L1 — two bearish C candles
L1_C1 = make_candle(0, "08:58", CandleLabel.C, Direction.BEAR,
                     open_=4444.30, high=4444.77, low=4442.29, close=4442.30)
L1_C2 = make_candle(1, "08:59", CandleLabel.C, Direction.BEAR,
                     open_=4442.29, high=4442.37, low=4440.03, close=4440.07)

# Synthetic S1 stop between L1 and L2.
# High (4442.00) is intentionally kept BELOW L1's starting high (4444.77)
# so that LegCounter's nested-stop check passes (S1.high <= L1_start).
# The real S1 (P at 08:57, H=4445.96) cannot be used here — it predates
# L1 and belongs to the previous structure.
S1_P_SYNTHETIC = make_candle(2, "09:00", CandleLabel.P, Direction.BULL,
                              open_=4440.10, high=4442.00, low=4440.00, close=4441.50)

# L2 — single bearish C candle
L2_C1 = make_candle(3, "09:01", CandleLabel.C, Direction.BEAR,
                     open_=4440.24, high=4440.24, low=4437.72, close=4437.73)

# S2 — large stop (ALWAYS_STOP), triggers L2 promotion
S2_L = make_candle(4, "09:02", CandleLabel.L, Direction.BULL,
                    open_=4437.75, high=4438.91, low=4437.25, close=4438.15)

# L3 — single bearish C candle
L3_C1 = make_candle(5, "09:03", CandleLabel.C, Direction.BEAR,
                     open_=4438.10, high=4438.10, low=4436.48, close=4436.58)

# S3 — four-candle stop sequence
S3_R = make_candle(6, "09:04", CandleLabel.R, Direction.BULL,
                    open_=4436.60, high=4436.76, low=4435.50, close=4436.41)
S3_B = make_candle(7, "09:05", CandleLabel.B, Direction.BULL,
                    open_=4436.46, high=4437.92, low=4436.38, close=4437.44)
S3_L = make_candle(8, "09:06", CandleLabel.L, Direction.BEAR,
                    open_=4437.49, high=4438.58, low=4435.74, close=4436.77)
S3_B2 = make_candle(9, "09:07", CandleLabel.B, Direction.BULL,
                     open_=4436.76, high=4437.96, low=4436.43, close=4437.65)

FULL_SEQUENCE = [
    L1_C1, L1_C2,
    S1_P_SYNTHETIC,
    L2_C1,
    S2_L,
    L3_C1,
    S3_R, S3_B, S3_L, S3_B2,
]


# ---------------------------------------------------------------------------
# Test 1 — LegValidator unit tests
# ---------------------------------------------------------------------------

def test_leg_validator_valid_two_c_candles():
    """Two C candles of the same direction form a valid leg."""
    candles = [
        make_candle(0, "09:00", CandleLabel.C, Direction.BEAR, 100, 101, 99, 99.5),
        make_candle(1, "09:01", CandleLabel.C, Direction.BEAR, 99.5, 100, 98, 98.5),
    ]
    assert LegValidator.is_valid_leg(candles), "Two C-BEAR candles should form a valid leg"


def test_leg_validator_single_s_invalid():
    """A lone S candle cannot form a leg."""
    candles = [
        make_candle(0, "09:00", CandleLabel.S, Direction.BEAR, 100, 101, 99, 99.5),
    ]
    assert not LegValidator.is_valid_leg(candles), "Single S candle must be invalid"


def test_leg_validator_single_b_invalid():
    """A single B candle fails the has_valid_base check (need 2+ B candles)."""
    candles = [
        make_candle(0, "09:00", CandleLabel.B, Direction.BEAR, 100, 101, 99, 99.5),
    ]
    assert not LegValidator.is_valid_leg(candles), "Single B candle must be invalid"


def test_leg_validator_two_b_candles_valid():
    """Two B candles of the same direction form a valid leg."""
    candles = [
        make_candle(0, "09:00", CandleLabel.B, Direction.BEAR, 100, 101, 99, 99.5),
        make_candle(1, "09:01", CandleLabel.B, Direction.BEAR, 99.5, 100, 98, 98.5),
    ]
    assert LegValidator.is_valid_leg(candles), "Two B-BEAR candles should be valid"


def test_leg_validator_mixed_direction_invalid():
    """Candles with mixed direction fail the all_same_direction check."""
    candles = [
        make_candle(0, "09:00", CandleLabel.C, Direction.BEAR, 100, 101, 99, 99.5),
        make_candle(1, "09:01", CandleLabel.C, Direction.BULL, 99.5, 101, 99, 100.5),
    ]
    assert not LegValidator.is_valid_leg(candles), "Mixed-direction candles must be invalid"


def test_leg_validator_s_inside_leg_invalid():
    """An S label anywhere inside the candle list invalidates the leg."""
    candles = [
        make_candle(0, "09:00", CandleLabel.C, Direction.BEAR, 100, 101, 99, 99.5),
        make_candle(1, "09:01", CandleLabel.S, Direction.BEAR, 99.5, 100, 97, 98),
        make_candle(2, "09:02", CandleLabel.C, Direction.BEAR, 98, 99, 96, 96.5),
    ]
    assert not LegValidator.is_valid_leg(candles), "S inside leg must make it invalid"


# ---------------------------------------------------------------------------
# Test 2 — Integration: feed real candles through StructureBuilder
# ---------------------------------------------------------------------------

def test_structure_builder_integration_no_crash():
    """Feed the full candle sequence without raising any exception."""
    builder = StructureBuilder()
    for candle in FULL_SEQUENCE:
        events = builder.process(candle)
        assert isinstance(events, list)


def test_structure_builder_integration_leg_confirmed_events():
    """At least one LEG_CONFIRMED event must be emitted during the sequence."""
    builder = StructureBuilder()
    all_events = []
    for candle in FULL_SEQUENCE:
        all_events.extend(builder.process(candle))

    leg_confirmed = [e for e in all_events if e.kind == EventKind.LEG_CONFIRMED]
    assert len(leg_confirmed) >= 1, (
        f"Expected at least 1 LEG_CONFIRMED event, got {len(leg_confirmed)}"
    )


def test_structure_builder_integration_confirmed_legs_min_2():
    """
    After the full sequence, at least 2 legs should be geometrically confirmed.

    Geometric checks (BEAR structure):
      staircase : each leg endpoint < previous leg endpoint
      nested    : each stop's high ≤ previous leg's starting high

    L1 start  (Point A) : L1_C1.high = 4444.77
    S1 high             : 4442.00  (synthetic, below 4444.77 ✓)
    L1 endpoint (B)     : min(L1.low=4440.03, S1.first.low=4440.00) = 4440.00
    L2 endpoint (D)     : min(L2.low=4437.72, S2.first.low=4437.25) = 4437.25
    D < B               : 4437.25 < 4440.00 ✓
    nested              : 4442.00 ≤ 4444.77 ✓  → L2 confirmed
    """
    builder = StructureBuilder()
    leg_counts: list[int] = []

    for candle in FULL_SEQUENCE:
        builder.process(candle)
        leg_counts.append(builder.confirmed_legs)

    final_count = builder.confirmed_legs
    assert final_count >= 2, (
        f"Expected confirmed_legs >= 2, got {final_count}\n"
        f"Leg count timeline: {leg_counts}"
    )


def test_structure_builder_integration_segments_alternate():
    """Confirmed segments must alternate LEG / STOP."""
    builder = StructureBuilder()
    for candle in FULL_SEQUENCE:
        builder.process(candle)

    for i in range(1, len(builder.segments)):
        prev_kind = builder.segments[i - 1].kind
        curr_kind = builder.segments[i].kind
        assert prev_kind != curr_kind, (
            f"Consecutive segments at index {i-1},{i} have the same kind: {curr_kind}"
        )


def test_structure_builder_integration_leg_count_monotonic():
    """confirmed_legs must never decrease as candles are processed."""
    builder = StructureBuilder()
    prev_count = 0

    for candle in FULL_SEQUENCE:
        builder.process(candle)
        assert builder.confirmed_legs >= prev_count, (
            f"confirmed_legs decreased from {prev_count} to {builder.confirmed_legs} "
            f"at candle index {candle.index}"
        )
        prev_count = builder.confirmed_legs


def test_structure_builder_integration_direction_preserved():
    """Structure direction must be BEAR throughout this sequence."""
    builder = StructureBuilder()
    for candle in FULL_SEQUENCE:
        builder.process(candle)

    assert builder.direction == Direction.BEAR, (
        f"Expected structure direction BEAR, got {builder.direction}"
    )


# ---------------------------------------------------------------------------
# Test 3 — StructurePoints geometry spot-checks
# ---------------------------------------------------------------------------

def test_structure_points_leg_start_bear():
    """For a BEAR leg, leg_start = first candle's high."""
    seg = Segment(kind=SegmentKind.LEG, direction=Direction.BEAR, candles=[L1_C1, L1_C2])
    assert StructurePoints.leg_start(seg) == L1_C1.high


def test_structure_points_leg_endpoint_bear_with_stop():
    """For a BEAR leg+stop, endpoint = min(leg.low, stop.first_candle.low)."""
    leg = Segment(kind=SegmentKind.LEG, direction=Direction.BEAR, candles=[L1_C1, L1_C2])
    stop = Segment(kind=SegmentKind.STOP, direction=Direction.BEAR, candles=[S1_P_SYNTHETIC])
    expected = min(L1_C2.low, S1_P_SYNTHETIC.low)  # min(4440.03, 4440.00) = 4440.00
    assert StructurePoints.leg_endpoint(leg, stop) == expected


def test_structure_points_stop_breach_bear():
    """For a BEAR stop, breach = first candle's low."""
    stop = Segment(kind=SegmentKind.STOP, direction=Direction.BEAR, candles=[S3_L, S3_B2])
    assert StructurePoints.stop_breach(stop) == S3_L.low


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [
        test_leg_validator_valid_two_c_candles,
        test_leg_validator_single_s_invalid,
        test_leg_validator_single_b_invalid,
        test_leg_validator_two_b_candles_valid,
        test_leg_validator_mixed_direction_invalid,
        test_leg_validator_s_inside_leg_invalid,
        test_structure_builder_integration_no_crash,
        test_structure_builder_integration_leg_confirmed_events,
        test_structure_builder_integration_confirmed_legs_min_2,
        test_structure_builder_integration_segments_alternate,
        test_structure_builder_integration_leg_count_monotonic,
        test_structure_builder_integration_direction_preserved,
        test_structure_points_leg_start_bear,
        test_structure_points_leg_endpoint_bear_with_stop,
        test_structure_points_stop_breach_bear,
    ]

    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except Exception as exc:
            print(f"  FAIL  {t.__name__}: {exc}")
            failed += 1

    print(f"\n{passed} passed, {failed} failed")
