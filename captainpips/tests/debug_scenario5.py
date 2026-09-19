import sys
sys.path.insert(0, "D:\\CP Robot V2")

from captainpips.core.definitions import Candle, Direction, CandleLabel, SegmentKind
from captainpips.core.structure_state import StructureBuilder
from captainpips.connectors.candle_classifier import classify_candle

rows = [
    ("17:10", 52047, 52062, 52038, 52057),
    ("17:11", 52059, 52105, 52054, 52104),
    ("17:12", 52107, 52129, 52106, 52115),
    ("17:13", 52116, 52123, 52102, 52108),
    ("17:14", 52108, 52118, 52099, 52112),
    ("17:15", 52112, 52131, 52099, 52128),
    ("17:16", 52127, 52169, 52117, 52169),
    ("17:17", 52168, 52168, 52131, 52132),
    ("17:18", 52132, 52158, 52124, 52154),
    ("17:19", 52153, 52176, 52147, 52171),
    ("17:20", 52171, 52186, 52166, 52178),
    ("17:21", 52178, 52198, 52170, 52197),
    ("17:22", 52197, 52210, 52176, 52208),
    ("17:23", 52207, 52207, 52177, 52180),
    ("17:24", 52181, 52313, 52173, 52254),
    ("17:25", 52258, 52300, 52255, 52267),
    ("17:26", 52267, 52284, 52239, 52249),
    ("17:27", 52249, 52249, 52220, 52229),
    ("17:28", 52228, 52228, 52202, 52212),
    ("17:29", 52212, 52214, 52187, 52201),
    ("17:30", 52203, 52214, 52183, 52186),
    ("17:31", 52185, 52215, 52178, 52208),
    ("17:32", 52207, 52208, 52189, 52203),
    ("17:33", 52202, 52207, 52195, 52204),
    ("17:34", 52203, 52224, 52201, 52205),
    ("17:35", 52204, 52236, 52204, 52220),
    ("17:36", 52221, 52232, 52210, 52228),
    ("17:37", 52226, 52229, 52213, 52226),
    ("17:38", 52226, 52226, 52214, 52217),
    ("17:39", 52216, 52219, 52195, 52198),
    ("17:40", 52198, 52198, 52183, 52184),
    ("17:41", 52185, 52210, 52185, 52197),
    ("17:42", 52197, 52207, 52180, 52181),
    ("17:43", 52180, 52180, 52150, 52157),
    ("17:44", 52157, 52165, 52154, 52160),
    ("17:45", 52161, 52162, 52140, 52158),
]

builder = StructureBuilder()
for i, (t, o, h, l, c) in enumerate(rows):
    label_str, dir_str = classify_candle(float(o), float(h), float(l), float(c))
    candle = Candle(index=i, time=t, label=CandleLabel(label_str),
                    direction=Direction(dir_str), raw_time=i*60,
                    open=float(o), high=float(h), low=float(l), close=float(c))
    cand = [x.label.value for x in builder.candidate]
    segs = len(builder.segments)
    legs = builder.confirmed_legs
    print(f"{t} {label_str:3} struct={candle.structure_direction.value:4} H={h} L={l} cand={cand} segs={segs} legs={legs}")
    events = builder.process(candle)
    if events:
        print(f"  --> {[e.kind.value for e in events]} legs={builder.confirmed_legs}")
        for seg in builder.segments:
            kind = "LEG" if seg.kind == SegmentKind.LEG else "STP"
            print(f"       [{kind}] {seg.direction.value} H={seg.high} L={seg.low}")
