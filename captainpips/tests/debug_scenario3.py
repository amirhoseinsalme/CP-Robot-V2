import sys
sys.path.insert(0, "D:\\CP Robot V2")

from captainpips.core.definitions import Candle, Direction, SegmentKind, CandleLabel
from captainpips.core.structure_state import StructureBuilder
from captainpips.connectors.candle_classifier import classify_candle

rows = [
    ("18:24", 52345, 52354, 52339, 52350),
    ("18:25", 52350, 52369, 52341, 52366),
    ("18:26", 52366, 52389, 52364, 52384),
    ("18:27", 52385, 52391, 52370, 52384),
    ("18:28", 52383, 52393, 52377, 52388),
    ("18:29", 52389, 52409, 52388, 52408),
    ("18:30", 52407, 52417, 52399, 52410),
    ("18:31", 52410, 52411, 52392, 52393),
    ("18:32", 52391, 52397, 52380, 52394),
    ("18:33", 52394, 52394, 52380, 52388),
    ("18:34", 52388, 52407, 52385, 52407),
    ("18:35", 52406, 52413, 52399, 52406),
    ("18:36", 52405, 52425, 52405, 52418),
    ("18:37", 52418, 52421, 52412, 52413),
    ("18:38", 52412, 52413, 52396, 52405),
    ("18:39", 52405, 52454, 52390, 52442),
    ("18:40", 52467, 52479, 52426, 52434),
    ("18:41", 52434, 52441, 52417, 52418),
]

builder = StructureBuilder()
for i, (t, o, h, l, c) in enumerate(rows):
    label_str, dir_str = classify_candle(float(o), float(h), float(l), float(c))
    candle = Candle(index=i, time=t, label=CandleLabel(label_str),
                    direction=Direction(dir_str), raw_time=i*60,
                    open=float(o), high=float(h), low=float(l), close=float(c))
    cand = [x.label.value for x in builder.candidate]
    segs = len(builder.segments)
    print(f"{t} {label_str:3} struct={candle.structure_direction.value:4} candidate={cand} segs={segs}")
    events = builder.process(candle)
    if events:
        print(f"  --> {[e.kind.value for e in events]} legs={builder.confirmed_legs}")
