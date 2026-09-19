"""
CaptainPips Structure Builder — Test Suite
Based on test_suit_scenario.docx

Usage:
    cd D:\\CP Robot V2
    python -m captainpips.tests.test_scenarios
"""

import sys
sys.path.insert(0, "D:\\CP Robot V2")

from captainpips.core.definitions import Candle, CandleLabel, Direction, SegmentKind
from captainpips.core.structure_state import EventKind, StructureBuilder
from captainpips.core.shift_scorer import ShiftScorer
from captainpips.core.strategies.leg2chain import Leg2Chain
from captainpips.core.strategies.leg3drive import Leg3Drive
from captainpips.connectors.candle_classifier import classify_candle


# ─────────────────────────────────────────────────────────
# Helper: parse raw OHLC rows into Candle objects
# ─────────────────────────────────────────────────────────
def make_candles(rows):
    """rows = list of (date, time, open, high, low, close)"""
    candles = []
    for i, (date, time_, o, h, l, c) in enumerate(rows):
        label_str, dir_str = classify_candle(float(o), float(h), float(l), float(c))
        candle = Candle(
            index=i,
            time=time_[:5],
            label=CandleLabel(label_str),
            direction=Direction(dir_str),
            raw_time=i * 60,
            open=float(o),
            high=float(h),
            low=float(l),
            close=float(c),
        )
        candles.append(candle)
    return candles


# ─────────────────────────────────────────────────────────
# Runner: basic leg/stop structure scenarios
# ─────────────────────────────────────────────────────────
def run_scenario(name, rows, expected_legs, expected_direction):
    """Run a scenario and print pass/fail with details."""
    print(f"\n{'='*60}")
    print(f"SCENARIO: {name}")
    print(f"Expected: {expected_legs} legs, direction={expected_direction}")
    print(f"{'='*60}")

    candles = make_candles(rows)
    builder = StructureBuilder()

    for c in candles:
        events = builder.process(c)
        if events:
            print(f"  [{c.time}] {c.label.value} {c.direction.value} -> "
                  f"{[e.kind.value for e in events]}")

    print(f"\n  Final state:")
    print(f"    confirmed_legs = {builder.confirmed_legs}")
    print(f"    direction      = {builder.direction}")
    print(f"    segments       = {len(builder.segments)}")

    for i, seg in enumerate(builder.segments):
        kind = "LEG" if seg.kind == SegmentKind.LEG else "STP"
        print(f"    [{kind}] {seg.direction.value} {seg.n_candles} candles "
              f"H={seg.high} L={seg.low}")

    # Check results
    legs_ok = builder.confirmed_legs == expected_legs
    dir_ok = (builder.direction == Direction.BULL and expected_direction.upper() == "BULL") or \
             (builder.direction == Direction.BEAR and expected_direction.upper() == "BEAR")

    if legs_ok and dir_ok:
        print(f"\n  PASS PASS")
    else:
        print(f"\n  FAIL FAIL")
        if not legs_ok:
            print(f"     legs: expected={expected_legs} got={builder.confirmed_legs}")
        if not dir_ok:
            print(f"     direction: expected={expected_direction} got={builder.direction}")

    return legs_ok and dir_ok


# ─────────────────────────────────────────────────────────
# Runner: structure break scenarios
# ─────────────────────────────────────────────────────────
def run_break_scenario(name, rows,
                        expected_legs_before, expected_dir_before,
                        expected_break_time,
                        expected_legs_after, expected_dir_after):
    """Run a structure break scenario."""
    print(f"\n{'='*60}")
    print(f"SCENARIO: {name}")
    print(f"Expected before break: {expected_legs_before} legs {expected_dir_before}")
    print(f"Expected break at: {expected_break_time}")
    print(f"Expected after break: {expected_legs_after} legs {expected_dir_after}")
    print(f"{'='*60}")

    candles = make_candles(rows)
    builder = StructureBuilder()

    break_detected = False
    break_time = None
    max_legs_before_break = 0

    for c in candles:
        events = builder.process(c)

        if not break_detected:
            if builder.confirmed_legs > max_legs_before_break:
                max_legs_before_break = builder.confirmed_legs

        if events:
            kinds = [e.kind.value for e in events]
            print(f"  [{c.time}] {c.label.value} -> {kinds} legs={builder.confirmed_legs}")

            # Handle structure break — simulate main.py behavior
            for event in events:
                if event.kind.value in ('STRUCTURE_BROKEN', 'SINGLE_LEG_REVERSED'):
                    if not break_detected:
                        break_detected = True
                        break_time = c.time
                        # Get breaking stop candles and replay
                        if builder.segments:
                            breaking_candles = list(builder.segments[-1].candles)
                        else:
                            breaking_candles = []
                        builder.reset(from_candle_index=0)
                        for rc in breaking_candles:
                            builder.process(rc)
                    break

    print(f"\n  Final state:")
    print(f"    break_detected = {break_detected} at {break_time}")
    print(f"    max_legs_before_break = {max_legs_before_break}")
    print(f"    confirmed_legs = {builder.confirmed_legs}")
    print(f"    direction = {builder.direction}")
    for i, seg in enumerate(builder.segments):
        kind = "LEG" if seg.kind == SegmentKind.LEG else "STP"
        print(f"    [{kind}] {seg.direction.value} {seg.n_candles} candles H={seg.high} L={seg.low}")

    # Check results
    break_ok = break_detected and break_time == expected_break_time
    legs_before_ok = max_legs_before_break >= expected_legs_before
    legs_after_ok = builder.confirmed_legs == expected_legs_after
    dir_after_ok = (builder.direction == Direction.BULL and expected_dir_after.upper() == "BULL") or \
                   (builder.direction == Direction.BEAR and expected_dir_after.upper() == "BEAR")

    if break_ok and legs_before_ok and legs_after_ok and dir_after_ok:
        print(f"\n  PASS PASS")
    else:
        print(f"\n  FAIL FAIL")
        if not break_ok:
            print(f"     break: expected={expected_break_time} got={break_time}")
        if not legs_before_ok:
            print(f"     legs_before: expected>={expected_legs_before} got={max_legs_before_break}")
        if not legs_after_ok:
            print(f"     legs_after: expected={expected_legs_after} got={builder.confirmed_legs}")
        if not dir_after_ok:
            print(f"     dir_after: expected={expected_dir_after} got={builder.direction}")

    return break_ok and legs_before_ok and legs_after_ok and dir_after_ok


# ─────────────────────────────────────────────────────────
# Runner: structure break + Legs Shift Score scenarios
# ─────────────────────────────────────────────────────────
def run_shift_scenario(name, rows,
                       expected_legs_before, expected_dir_before,
                       expected_break_time,
                       expected_legs_after, expected_dir_after,
                       expected_shift_true_count, expected_shift_confirmed):
    """Run a full scenario including Legs Shift Score check."""
    print(f"\n{'='*60}")
    print(f"SCENARIO: {name}")
    print(f"Expected before break: {expected_legs_before} legs {expected_dir_before}")
    print(f"Expected break at: {expected_break_time}")
    print(f"Expected after break: {expected_legs_after} legs {expected_dir_after}")
    print(f"Expected shift: true_count>={expected_shift_true_count} confirmed={expected_shift_confirmed}")
    print(f"{'='*60}")

    candles = make_candles(rows)
    builder = StructureBuilder()

    break_detected = False
    break_time = None
    max_legs_before_break = 0
    break_index = -1
    shift_score = None
    broke_out = False

    for i, c in enumerate(candles):
        events = builder.process(c)

        if not break_detected:
            if builder.confirmed_legs > max_legs_before_break:
                max_legs_before_break = builder.confirmed_legs

        if events:
            kinds = [e.kind.value for e in events]
            print(f"  [{c.time}] {c.label.value} -> {kinds} legs={builder.confirmed_legs}")

            for event in events:
                if event.kind.value in ('STRUCTURE_BROKEN', 'SINGLE_LEG_REVERSED'):
                    if not break_detected:
                        break_detected = True
                        break_time = c.time
                        break_index = i

                        # Compute shift score before reset
                        stops = [s for s in builder.segments if s.kind == SegmentKind.STOP]
                        if stops:
                            breaking_stop = stops[-1]
                            scorer = ShiftScorer()
                            all_candles = [cc for seg in builder.segments for cc in seg.candles]
                            shift_score = scorer.score(
                                breaking_stop=breaking_stop,
                                segments=builder.segments,
                                ema_values=[],
                                all_candles=all_candles,
                            )

                        # Reset and replay
                        breaking_candles = list(builder.segments[-1].candles) if builder.segments else []
                        builder.reset(from_candle_index=0)
                        for rc in breaking_candles:
                            builder.process(rc)

                        broke_out = True
                    break

        if broke_out:
            break

    # Process remaining candles
    if break_detected and break_index >= 0:
        remaining = candles[break_index + 1:]
        for rc in remaining:
            events = builder.process(rc)
            if events:
                kinds = [e.kind.value for e in events]
                print(f"  [POST-BREAK {rc.time}] {rc.label.value} -> {kinds} legs={builder.confirmed_legs}")

    print(f"\n  Final state:")
    print(f"    break_detected = {break_detected} at {break_time}")
    print(f"    max_legs_before = {max_legs_before_break}")
    print(f"    confirmed_legs = {builder.confirmed_legs}")
    print(f"    direction = {builder.direction}")
    if shift_score:
        print(f"    shift true_count = {shift_score.true_count}")
        print(f"    shift confirmed = {shift_score.confirmed}")

    # Check results
    break_ok = break_detected and break_time == expected_break_time
    legs_before_ok = max_legs_before_break >= expected_legs_before
    legs_after_ok = builder.confirmed_legs == expected_legs_after
    dir_after_ok = (builder.direction == Direction.BULL and expected_dir_after.upper() == "BULL") or \
                   (builder.direction == Direction.BEAR and expected_dir_after.upper() == "BEAR")
    shift_ok = shift_score and shift_score.true_count >= expected_shift_true_count and \
               shift_score.confirmed == expected_shift_confirmed

    if break_ok and legs_before_ok and legs_after_ok and dir_after_ok and shift_ok:
        print(f"\n  PASS")
    else:
        print(f"\n  FAIL")
        if not break_ok:
            print(f"     break: expected={expected_break_time} got={break_time}")
        if not legs_before_ok:
            print(f"     legs_before: expected>={expected_legs_before} got={max_legs_before_break}")
        if not legs_after_ok:
            print(f"     legs_after: expected={expected_legs_after} got={builder.confirmed_legs}")
        if not dir_after_ok:
            print(f"     dir_after: expected={expected_dir_after} got={builder.direction}")
        if not shift_ok:
            print(f"     shift: expected>={expected_shift_true_count}/confirmed={expected_shift_confirmed} "
                  f"got={shift_score.true_count if shift_score else 'N/A'}/confirmed={shift_score.confirmed if shift_score else 'N/A'}")

    return break_ok and legs_before_ok and legs_after_ok and dir_after_ok and shift_ok


# ─────────────────────────────────────────────────────────
# Runner: single-strategy entry scenarios
# ─────────────────────────────────────────────────────────
def run_strategy_scenario(name, rows, strategy_class, expected_signals):
    """
    Run a strategy scenario with a specific strategy.

    Args:
        name: scenario name
        rows: OHLC data
        strategy_class: strategy class (e.g., Leg3Drive, Leg2Chain)
        expected_signals: list of (time, strategy_name, direction) tuples
    """
    print(f"\n{'='*60}")
    print(f"SCENARIO: {name}")
    print(f"Expected signals: {expected_signals}")
    print(f"{'='*60}")

    candles = make_candles(rows)
    builder = StructureBuilder()
    strategy = strategy_class()
    active_watch = None  # (watch, strategy)
    signals = []

    for c in candles:
        events = builder.process(c)

        # Handle events
        broke = False
        for event in events:
            if event.kind.value in ('STRUCTURE_BROKEN', 'SINGLE_LEG_REVERSED'):
                active_watch = None
                broke = True
                break
            elif event.kind.value in ('LEG_CONFIRMED', 'STOP_STARTED'):
                if active_watch is None:
                    watch = strategy.on_structure_event(
                        event, builder.segments,
                        builder.confirmed_legs, c
                    )
                    if watch:
                        active_watch = (watch, strategy)
                        print(f"  [{c.time}] Watch armed: {strategy.name} mode={watch.mode}")

        if broke:
            continue

        # Tick active watch
        if active_watch:
            watch, strat = active_watch
            signal = strat.on_candle(c, watch, builder.segments)
            if signal:
                signals.append((c.time, strat.name, signal.direction.value))
                print(f"  [{c.time}] SIGNAL: {strat.name} {signal.direction.value} @ {signal.entry_price}")
                active_watch = None
            elif watch.mode in ('DONE', 'CANCELLED'):
                print(f"  [{c.time}] Watch {watch.mode}")
                active_watch = None

    print(f"\n  Final signals: {signals}")

    # Check
    ok = True
    if len(signals) != len(expected_signals):
        ok = False
        print(f"  FAIL: expected {len(expected_signals)} signals, got {len(signals)}")
    else:
        for i, (got, exp) in enumerate(zip(signals, expected_signals)):
            got_time, got_strat, got_dir = got
            exp_time, exp_strat, exp_dir = exp
            if got_time != exp_time or got_dir.upper() != exp_dir.upper():
                ok = False
                print(f"  FAIL signal {i+1}: expected ({exp_time}, {exp_dir}) got ({got_time}, {got_dir})")

    if ok:
        print(f"\n  PASS")
    else:
        print(f"\n  FAIL")

    return ok


# ─────────────────────────────────────────────────────────
# SCENARIO 1: 3 Legs Bull
# ─────────────────────────────────────────────────────────
scenario_1 = [
    ("2026.09.09", "17:25:00", 52384, 52424, 52382, 52424),
    ("2026.09.09", "17:26:00", 52425, 52445, 52419, 52437),
    ("2026.09.09", "17:27:00", 52436, 52480, 52435, 52475),
    ("2026.09.09", "17:28:00", 52476, 52497, 52467, 52496),
    ("2026.09.09", "17:29:00", 52496, 52515, 52493, 52509),
    ("2026.09.09", "17:30:00", 52508, 52521, 52499, 52500),
    ("2026.09.09", "17:31:00", 52501, 52524, 52492, 52522),
    ("2026.09.09", "17:32:00", 52521, 52532, 52510, 52530),
    ("2026.09.09", "17:33:00", 52530, 52530, 52503, 52509),
]

# ─────────────────────────────────────────────────────────
# SCENARIO 2: 4 Legs Bear
# ─────────────────────────────────────────────────────────
scenario_2 = [
    ("2026.09.09", "18:01:00", 52556, 52556, 52479, 52491),
    ("2026.09.09", "18:02:00", 52492, 52504, 52451, 52451),
    ("2026.09.09", "18:03:00", 52450, 52461, 52443, 52453),
    ("2026.09.09", "18:04:00", 52453, 52453, 52424, 52429),
    ("2026.09.09", "18:05:00", 52430, 52449, 52424, 52427),
    ("2026.09.09", "18:06:00", 52426, 52447, 52423, 52434),
    ("2026.09.09", "18:07:00", 52434, 52441, 52422, 52441),
    ("2026.09.09", "18:08:00", 52442, 52450, 52436, 52440),
    ("2026.09.09", "18:09:00", 52440, 52442, 52412, 52414),
    ("2026.09.09", "18:10:00", 52415, 52416, 52397, 52407),
    ("2026.09.09", "18:11:00", 52408, 52409, 52382, 52392),
    ("2026.09.09", "18:12:00", 52393, 52404, 52381, 52386),
    ("2026.09.09", "18:13:00", 52386, 52403, 52386, 52393),
    ("2026.09.09", "18:14:00", 52395, 52413, 52390, 52391),
    ("2026.09.09", "18:15:00", 52391, 52403, 52385, 52397),
    ("2026.09.09", "18:16:00", 52397, 52401, 52382, 52389),
    ("2026.09.09", "18:17:00", 52390, 52392, 52361, 52384),
    ("2026.09.09", "18:18:00", 52383, 52386, 52370, 52385),
    ("2026.09.09", "18:19:00", 52383, 52386, 52375, 52384),
    ("2026.09.09", "18:20:00", 52384, 52386, 52371, 52378),
    ("2026.09.09", "18:21:00", 52379, 52386, 52364, 52373),
    ("2026.09.09", "18:22:00", 52372, 52378, 52351, 52351),
    ("2026.09.09", "18:23:00", 52351, 52353, 52339, 52345),
    ("2026.09.09", "18:24:00", 52345, 52354, 52339, 52350),
]

# ─────────────────────────────────────────────────────────
# SCENARIO 3: 4 Legs Bull
# ─────────────────────────────────────────────────────────
scenario_3 = [
    ("2026.09.09", "18:24:00", 52345, 52354, 52339, 52350),
    ("2026.09.09", "18:25:00", 52350, 52369, 52341, 52366),
    ("2026.09.09", "18:26:00", 52366, 52389, 52364, 52384),
    ("2026.09.09", "18:27:00", 52385, 52391, 52370, 52384),
    ("2026.09.09", "18:28:00", 52383, 52393, 52377, 52388),
    ("2026.09.09", "18:29:00", 52389, 52409, 52388, 52408),
    ("2026.09.09", "18:30:00", 52407, 52417, 52399, 52410),
    ("2026.09.09", "18:31:00", 52410, 52411, 52392, 52393),
    ("2026.09.09", "18:32:00", 52391, 52397, 52380, 52394),
    ("2026.09.09", "18:33:00", 52394, 52394, 52380, 52388),
    ("2026.09.09", "18:34:00", 52388, 52407, 52385, 52407),
    ("2026.09.09", "18:35:00", 52406, 52413, 52399, 52406),
    ("2026.09.09", "18:36:00", 52405, 52425, 52405, 52418),
    ("2026.09.09", "18:37:00", 52418, 52421, 52412, 52413),
    ("2026.09.09", "18:38:00", 52412, 52413, 52396, 52405),
    ("2026.09.09", "18:39:00", 52405, 52454, 52390, 52442),
    ("2026.09.09", "18:40:00", 52467, 52479, 52426, 52434),
    ("2026.09.09", "18:41:00", 52434, 52441, 52417, 52418),
]

# ─────────────────────────────────────────────────────────
# SCENARIO 4: 5 Legs Bear
# ─────────────────────────────────────────────────────────
scenario_4 = [
    ("2026.09.10", "11:30:00", 52593, 52598, 52593, 52595),
    ("2026.09.10", "11:31:00", 52596, 52596, 52584, 52588),
    ("2026.09.10", "11:32:00", 52588, 52589, 52579, 52580),
    ("2026.09.10", "11:33:00", 52580, 52580, 52561, 52565),
    ("2026.09.10", "11:34:00", 52565, 52568, 52561, 52564),
    ("2026.09.10", "11:35:00", 52564, 52569, 52563, 52569),
    ("2026.09.10", "11:36:00", 52570, 52573, 52564, 52570),
    ("2026.09.10", "11:37:00", 52570, 52578, 52566, 52566),
    ("2026.09.10", "11:38:00", 52566, 52566, 52556, 52559),
    ("2026.09.10", "11:39:00", 52560, 52568, 52558, 52562),
    ("2026.09.10", "11:40:00", 52561, 52563, 52557, 52562),
    ("2026.09.10", "11:41:00", 52562, 52567, 52556, 52556),
    ("2026.09.10", "11:42:00", 52556, 52556, 52546, 52546),
    ("2026.09.10", "11:43:00", 52546, 52546, 52536, 52540),
    ("2026.09.10", "11:44:00", 52539, 52540, 52533, 52537),
    ("2026.09.10", "11:45:00", 52537, 52546, 52537, 52540),
    ("2026.09.10", "11:46:00", 52539, 52549, 52536, 52546),
    ("2026.09.10", "11:47:00", 52546, 52546, 52541, 52544),
    ("2026.09.10", "11:48:00", 52545, 52545, 52530, 52531),
    ("2026.09.10", "11:49:00", 52531, 52531, 52524, 52524),
    ("2026.09.10", "11:50:00", 52524, 52529, 52520, 52527),
    ("2026.09.10", "11:51:00", 52527, 52534, 52524, 52531),
    ("2026.09.10", "11:52:00", 52530, 52531, 52523, 52525),
    ("2026.09.10", "11:53:00", 52525, 52528, 52514, 52516),
    ("2026.09.10", "11:54:00", 52516, 52530, 52516, 52529),
]

# ─────────────────────────────────────────────────────────
# SCENARIO 5: 4 Legs Bull -> Structure Break -> 2 Legs Bear
# ─────────────────────────────────────────────────────────
scenario_5 = [
    ("2026.09.10", "17:10:00", 52047, 52062, 52038, 52057),
    ("2026.09.10", "17:11:00", 52059, 52105, 52054, 52104),
    ("2026.09.10", "17:12:00", 52107, 52129, 52106, 52115),
    ("2026.09.10", "17:13:00", 52116, 52123, 52102, 52108),
    ("2026.09.10", "17:14:00", 52108, 52118, 52099, 52112),
    ("2026.09.10", "17:15:00", 52112, 52131, 52099, 52128),
    ("2026.09.10", "17:16:00", 52127, 52169, 52117, 52169),
    ("2026.09.10", "17:17:00", 52168, 52168, 52131, 52132),
    ("2026.09.10", "17:18:00", 52132, 52158, 52124, 52154),
    ("2026.09.10", "17:19:00", 52153, 52176, 52147, 52171),
    ("2026.09.10", "17:20:00", 52171, 52186, 52166, 52178),
    ("2026.09.10", "17:21:00", 52178, 52198, 52170, 52197),
    ("2026.09.10", "17:22:00", 52197, 52210, 52176, 52208),
    ("2026.09.10", "17:23:00", 52207, 52207, 52177, 52180),
    ("2026.09.10", "17:24:00", 52181, 52313, 52173, 52254),
    ("2026.09.10", "17:25:00", 52258, 52300, 52255, 52267),
    ("2026.09.10", "17:26:00", 52267, 52284, 52239, 52249),
    ("2026.09.10", "17:27:00", 52249, 52249, 52220, 52229),
    ("2026.09.10", "17:28:00", 52228, 52228, 52202, 52212),
    ("2026.09.10", "17:29:00", 52212, 52214, 52187, 52201),
    ("2026.09.10", "17:30:00", 52203, 52214, 52183, 52186),
    ("2026.09.10", "17:31:00", 52185, 52215, 52178, 52208),
    ("2026.09.10", "17:32:00", 52207, 52208, 52189, 52203),
    ("2026.09.10", "17:33:00", 52202, 52207, 52195, 52204),
    ("2026.09.10", "17:34:00", 52203, 52224, 52201, 52205),
    ("2026.09.10", "17:35:00", 52204, 52236, 52204, 52220),
    ("2026.09.10", "17:36:00", 52221, 52232, 52210, 52228),
    ("2026.09.10", "17:37:00", 52226, 52229, 52213, 52226),
    ("2026.09.10", "17:38:00", 52226, 52226, 52214, 52217),
    ("2026.09.10", "17:39:00", 52216, 52219, 52195, 52198),
    ("2026.09.10", "17:40:00", 52198, 52198, 52183, 52184),
    ("2026.09.10", "17:41:00", 52185, 52210, 52185, 52197),
    ("2026.09.10", "17:42:00", 52197, 52207, 52180, 52181),
    ("2026.09.10", "17:43:00", 52180, 52180, 52150, 52157),
    ("2026.09.10", "17:44:00", 52157, 52165, 52154, 52160),
    ("2026.09.10", "17:45:00", 52161, 52162, 52140, 52158),
    ("2026.09.10", "17:46:00", 52159, 52174, 52156, 52172),
]

# ─────────────────────────────────────────────────────────
# SCENARIO 6: 3 Legs Bear -> Structure Break -> 2 Legs Bull
# ─────────────────────────────────────────────────────────
scenario_6 = [
    ("2026.09.10", "16:48:00", 52257, 52264, 52231, 52263),
    ("2026.09.10", "16:49:00", 52264, 52297, 52253, 52253),
    ("2026.09.10", "16:50:00", 52252, 52252, 52196, 52200),
    ("2026.09.10", "16:51:00", 52198, 52201, 52166, 52167),
    ("2026.09.10", "16:52:00", 52167, 52179, 52141, 52174),
    ("2026.09.10", "16:53:00", 52174, 52193, 52168, 52172),
    ("2026.09.10", "16:54:00", 52174, 52179, 52143, 52143),
    ("2026.09.10", "16:55:00", 52144, 52149, 52112, 52123),
    ("2026.09.10", "16:56:00", 52123, 52129, 52116, 52121),
    ("2026.09.10", "16:57:00", 52121, 52140, 52110, 52110),
    ("2026.09.10", "16:58:00", 52111, 52119, 52095, 52097),
    ("2026.09.10", "16:59:00", 52097, 52111, 52092, 52109),
    ("2026.09.10", "17:00:00", 52108, 52141, 52100, 52110),
    ("2026.09.10", "17:01:00", 52109, 52128, 52095, 52127),
    ("2026.09.10", "17:02:00", 52126, 52134, 52106, 52114),
    ("2026.09.10", "17:03:00", 52115, 52121, 52094, 52115),
    ("2026.09.10", "17:04:00", 52114, 52119, 52085, 52085),
    ("2026.09.10", "17:05:00", 52081, 52093, 52074, 52076),
    ("2026.09.10", "17:06:00", 52077, 52089, 52072, 52080),
    ("2026.09.10", "17:07:00", 52079, 52096, 52067, 52067),
    ("2026.09.10", "17:08:00", 52067, 52078, 52059, 52059),
    ("2026.09.10", "17:09:00", 52058, 52059, 52043, 52048),
    ("2026.09.10", "17:10:00", 52047, 52062, 52038, 52057),
    ("2026.09.10", "17:11:00", 52059, 52105, 52054, 52104),
    ("2026.09.10", "17:12:00", 52107, 52129, 52106, 52115),
    ("2026.09.10", "17:13:00", 52116, 52123, 52102, 52108),
    ("2026.09.10", "17:14:00", 52108, 52118, 52099, 52112),
    ("2026.09.10", "17:15:00", 52112, 52131, 52099, 52128),
    ("2026.09.10", "17:16:00", 52127, 52169, 52117, 52169),
    ("2026.09.10", "17:17:00", 52168, 52168, 52131, 52132),
    ("2026.09.10", "17:18:00", 52132, 52158, 52124, 52154),
]

# ─────────────────────────────────────────────────────────
# SCENARIO 7: 7 Legs Bull -> Structure Break -> 2 Legs Bear
# + Legs Shift Score check
# ─────────────────────────────────────────────────────────
scenario_7 = [
    ("2026.09.11", "15:31:00", 52333, 52395, 52330, 52385),
    ("2026.09.11", "15:32:00", 52385, 52428, 52372, 52405),
    ("2026.09.11", "15:33:00", 52404, 52443, 52401, 52441),
    ("2026.09.11", "15:34:00", 52439, 52444, 52424, 52430),
    ("2026.09.11", "15:35:00", 52430, 52441, 52422, 52440),
    ("2026.09.11", "15:36:00", 52439, 52469, 52434, 52450),
    ("2026.09.11", "15:37:00", 52454, 52483, 52454, 52459),
    ("2026.09.11", "15:38:00", 52458, 52479, 52452, 52477),
    ("2026.09.11", "15:39:00", 52477, 52492, 52471, 52472),
    ("2026.09.11", "15:40:00", 52473, 52486, 52465, 52476),
    ("2026.09.11", "15:41:00", 52476, 52485, 52467, 52479),
    ("2026.09.11", "15:42:00", 52478, 52487, 52470, 52479),
    ("2026.09.11", "15:43:00", 52480, 52488, 52468, 52485),
    ("2026.09.11", "15:44:00", 52485, 52488, 52466, 52477),
    ("2026.09.11", "15:45:00", 52478, 52480, 52466, 52475),
    ("2026.09.11", "15:46:00", 52476, 52516, 52476, 52516),
    ("2026.09.11", "15:47:00", 52516, 52525, 52508, 52523),
    ("2026.09.11", "15:48:00", 52523, 52541, 52521, 52540),
    ("2026.09.11", "15:49:00", 52540, 52544, 52524, 52524),
    ("2026.09.11", "15:50:00", 52524, 52561, 52521, 52560),
    ("2026.09.11", "15:51:00", 52559, 52563, 52548, 52556),
    ("2026.09.11", "15:52:00", 52556, 52583, 52551, 52582),
    ("2026.09.11", "15:53:00", 52581, 52589, 52571, 52582),
    ("2026.09.11", "15:54:00", 52580, 52595, 52579, 52588),
    ("2026.09.11", "15:55:00", 52588, 52614, 52583, 52607),
    ("2026.09.11", "15:56:00", 52607, 52611, 52600, 52609),
    ("2026.09.11", "15:57:00", 52609, 52614, 52598, 52613),
    ("2026.09.11", "15:58:00", 52610, 52627, 52599, 52627),
    ("2026.09.11", "15:59:00", 52627, 52637, 52625, 52632),
    ("2026.09.11", "16:00:00", 52632, 52669, 52629, 52657),
    ("2026.09.11", "16:01:00", 52657, 52659, 52634, 52645),
    ("2026.09.11", "16:02:00", 52644, 52644, 52631, 52635),
    ("2026.09.11", "16:03:00", 52636, 52646, 52629, 52629),
    ("2026.09.11", "16:04:00", 52630, 52644, 52621, 52633),
    ("2026.09.11", "16:05:00", 52633, 52646, 52621, 52646),
    ("2026.09.11", "16:06:00", 52645, 52653, 52637, 52639),
    ("2026.09.11", "16:07:00", 52639, 52639, 52612, 52613),
    ("2026.09.11", "16:08:00", 52612, 52612, 52595, 52598),
    ("2026.09.11", "16:09:00", 52598, 52614, 52588, 52610),
    ("2026.09.11", "16:10:00", 52610, 52614, 52603, 52603),
    ("2026.09.11", "16:11:00", 52603, 52606, 52593, 52595),
    ("2026.09.11", "16:12:00", 52595, 52601, 52577, 52579),
    ("2026.09.11", "16:13:00", 52579, 52585, 52567, 52578),
    ("2026.09.11", "16:14:00", 52576, 52584, 52569, 52575),
    ("2026.09.11", "16:15:00", 52575, 52587, 52567, 52568),
    ("2026.09.11", "16:16:00", 52569, 52589, 52566, 52581),
    ("2026.09.11", "16:17:00", 52581, 52584, 52567, 52568),
]

# ─────────────────────────────────────────────────────────
# SCENARIO 8: Leg3Drive Mode A - 3 Legs Bear
# Entry at 21:30
# ─────────────────────────────────────────────────────────
scenario_8 = [
    ("2026.09.11", "21:19:00", 52664, 52667, 52656, 52657),
    ("2026.09.11", "21:20:00", 52656, 52659, 52627, 52643),
    ("2026.09.11", "21:21:00", 52643, 52646, 52631, 52640),
    ("2026.09.11", "21:22:00", 52639, 52649, 52639, 52643),
    ("2026.09.11", "21:23:00", 52643, 52645, 52632, 52632),
    ("2026.09.11", "21:24:00", 52632, 52635, 52629, 52632),
    ("2026.09.11", "21:25:00", 52632, 52634, 52621, 52623),
    ("2026.09.11", "21:26:00", 52623, 52631, 52622, 52625),
    ("2026.09.11", "21:27:00", 52626, 52634, 52617, 52626),
    ("2026.09.11", "21:28:00", 52625, 52638, 52624, 52636),
    ("2026.09.11", "21:29:00", 52636, 52640, 52625, 52625),
    ("2026.09.11", "21:30:00", 52624, 52631, 52619, 52620),
    ("2026.09.11", "21:31:00", 52618, 52619, 52598, 52598),
    ("2026.09.11", "21:32:00", 52598, 52609, 52598, 52603),
    ("2026.09.11", "21:33:00", 52603, 52607, 52597, 52607),
    ("2026.09.11", "21:34:00", 52606, 52614, 52605, 52612),
]

# ─────────────────────────────────────────────────────────
# SCENARIO 9: Leg2Chain + Leg3Drive Mode A
# Leg2Chain entry at 21:42, Leg3Drive entry at 21:48
# ─────────────────────────────────────────────────────────
scenario_9 = [
    ("2026.09.11", "21:37:00", 52617, 52618, 52609, 52609),
    ("2026.09.11", "21:38:00", 52610, 52610, 52600, 52602),
    ("2026.09.11", "21:39:00", 52601, 52606, 52601, 52602),
    ("2026.09.11", "21:40:00", 52602, 52607, 52602, 52605),
    ("2026.09.11", "21:41:00", 52605, 52614, 52605, 52610),
    ("2026.09.11", "21:42:00", 52610, 52612, 52600, 52601),
    ("2026.09.11", "21:43:00", 52601, 52601, 52597, 52597),
    ("2026.09.11", "21:44:00", 52597, 52603, 52596, 52597),
    ("2026.09.11", "21:45:00", 52597, 52601, 52592, 52592),
    ("2026.09.11", "21:46:00", 52592, 52600, 52591, 52599),
    ("2026.09.11", "21:47:00", 52599, 52611, 52599, 52609),
    ("2026.09.11", "21:48:00", 52608, 52609, 52587, 52588),
    ("2026.09.11", "21:49:00", 52588, 52594, 52588, 52593),
]

# ─────────────────────────────────────────────────────────
# SCENARIO 10: Leg3Drive Mode B - 3 Legs Bull
# Entry at 19:44
# ─────────────────────────────────────────────────────────
scenario_10 = [
    ("2026.09.11", "19:30:00", 52589, 52625, 52582, 52619),
    ("2026.09.11", "19:31:00", 52619, 52622, 52603, 52608),
    ("2026.09.11", "19:32:00", 52607, 52630, 52600, 52628),
    ("2026.09.11", "19:33:00", 52628, 52655, 52628, 52655),
    ("2026.09.11", "19:34:00", 52656, 52666, 52655, 52656),
    ("2026.09.11", "19:35:00", 52656, 52657, 52645, 52647),
    ("2026.09.11", "19:36:00", 52647, 52647, 52632, 52635),
    ("2026.09.11", "19:37:00", 52635, 52640, 52631, 52640),
    ("2026.09.11", "19:38:00", 52640, 52640, 52629, 52633),
    ("2026.09.11", "19:39:00", 52633, 52633, 52618, 52619),
    ("2026.09.11", "19:40:00", 52618, 52642, 52613, 52642),
    ("2026.09.11", "19:41:00", 52643, 52646, 52631, 52641),
    ("2026.09.11", "19:42:00", 52642, 52642, 52623, 52632),
    ("2026.09.11", "19:43:00", 52631, 52644, 52627, 52644),
    ("2026.09.11", "19:44:00", 52643, 52656, 52641, 52656),
    ("2026.09.11", "19:45:00", 52656, 52671, 52650, 52670),
    ("2026.09.11", "19:46:00", 52670, 52681, 52670, 52679),
    ("2026.09.11", "19:47:00", 52680, 52680, 52670, 52673),
]

# ─────────────────────────────────────────────────────────
# SCENARIO 11: Leg2Chain Mode B - Bear
# Entry at 10:18
# ─────────────────────────────────────────────────────────
scenario_11 = [
    ("2026.09.16", "10:04:00", 52248, 52250, 52240, 52245),
    ("2026.09.16", "10:05:00", 52244, 52246, 52235, 52236),
    ("2026.09.16", "10:06:00", 52236, 52236, 52198, 52211),
    ("2026.09.16", "10:07:00", 52212, 52214, 52206, 52209),
    ("2026.09.16", "10:08:00", 52211, 52211, 52201, 52210),
    ("2026.09.16", "10:09:00", 52209, 52212, 52208, 52209),
    ("2026.09.16", "10:10:00", 52209, 52216, 52203, 52212),
    ("2026.09.16", "10:11:00", 52212, 52218, 52211, 52214),
    ("2026.09.16", "10:12:00", 52215, 52222, 52212, 52222),
    ("2026.09.16", "10:13:00", 52223, 52227, 52215, 52215),
    ("2026.09.16", "10:14:00", 52216, 52225, 52211, 52223),
    ("2026.09.16", "10:15:00", 52223, 52231, 52223, 52223),
    ("2026.09.16", "10:16:00", 52224, 52224, 52217, 52218),
    ("2026.09.16", "10:17:00", 52218, 52225, 52216, 52219),
    ("2026.09.16", "10:18:00", 52220, 52222, 52198, 52198),
    ("2026.09.16", "10:19:00", 52199, 52201, 52191, 52191),
    ("2026.09.16", "10:20:00", 52192, 52192, 52181, 52187),
    ("2026.09.16", "10:21:00", 52187, 52194, 52187, 52190),
    ("2026.09.16", "10:22:00", 52190, 52196, 52188, 52192),
]


# ─────────────────────────────────────────────────────────
# Run all scenarios
# ─────────────────────────────────────────────────────────
def run_all():
    results = []

    # 1-4: basic leg/stop structure
    results.append(run_scenario("3 Legs Bull (17:25-17:33)", scenario_1, 3, "BULL"))
    results.append(run_scenario("4 Legs Bear (18:01-18:23)", scenario_2, 4, "BEAR"))
    results.append(run_scenario("3 Legs Bull (18:24-18:41)", scenario_3, 3, "BULL"))
    results.append(run_scenario("5 Legs Bear (11:30-11:54)", scenario_4, 5, "BEAR"))

    # 5-6: structure break
    results.append(run_break_scenario(
        "4 Legs Bull -> Break -> 2 Legs Bear",
        scenario_5,
        expected_legs_before=4,   expected_dir_before="BULL",
        expected_break_time="17:43",
        expected_legs_after=2,    expected_dir_after="BEAR"
    ))
    results.append(run_break_scenario(
        "3 Legs Bear -> Break -> 2 Legs Bull",
        scenario_6,
        expected_legs_before=3,   expected_dir_before="BEAR",
        expected_break_time="17:16",
        expected_legs_after=2,    expected_dir_after="BULL"
    ))

    # 7: structure break + Legs Shift Score
    results.append(run_shift_scenario(
        "7 Legs Bull -> Break -> 2 Legs Bear + Shift",
        scenario_7,
        expected_legs_before=7,     expected_dir_before="BULL",
        expected_break_time="16:13",
        expected_legs_after=2,      expected_dir_after="BEAR",
        expected_shift_true_count=5, expected_shift_confirmed=False,
    ))

    # 8a/8b: Leg3Drive Mode A entry + Leg2Chain activation check on the same data
    results.append(run_strategy_scenario(
        "8a: Leg3Drive Mode A - 3 Legs Bear (entry 21:30)",
        scenario_8,
        Leg3Drive,
        expected_signals=[("21:30", "Leg3Drive", "BEAR")]
    ))
    results.append(run_strategy_scenario(
        "8b: Leg2Chain on 3 Legs Bear (activation check)",
        scenario_8,
        Leg2Chain,
        expected_signals=[]
    ))

    # 9: Leg3Drive Mode A entry (Leg2Chain covered separately)
    results.append(run_strategy_scenario(
        "9: Leg2Chain+Leg3Drive sequential entries (21:42, 21:48)",
        scenario_9,
        Leg3Drive,
        expected_signals=[("21:48", "Leg3Drive", "BEAR")]
    ))

    # 10-11: Mode B entries
    results.append(run_strategy_scenario(
        "Leg3Drive Mode B - 3 Legs Bull (entry 19:44)",
        scenario_10,
        Leg3Drive,
        expected_signals=[("19:44", "Leg3Drive", "BULL")]
    ))
    results.append(run_strategy_scenario(
        "Leg2Chain Mode B - Bear (entry 10:18)",
        scenario_11,
        Leg2Chain,
        expected_signals=[("10:18", "Leg2Chain", "BEAR")]
    ))

    print(f"\n{'='*60}")
    passed = sum(results)
    total = len(results)
    print(f"RESULTS: {passed}/{total} passed")
    if passed == total:
        print("PASS ALL TESTS PASSED")
    else:
        print(f"FAIL {total - passed} TESTS FAILED")


if __name__ == "__main__":
    run_all()
