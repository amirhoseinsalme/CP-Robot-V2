# candle_classifier.py
"""
Exact Python port of CandleClassifier-V1.mq5
Reverse-engineered from RoboFriend_Master_v3.10 — decision tree v9
Decision tree: 986 samples, 100% accuracy.

DO NOT MODIFY — this is a faithful, line-by-line port of the MQL5 source.
The Classify() logic and all threshold values are copied verbatim from the
original MQL5 indicator file.
"""

from typing import Tuple


# ──────────────────────────────────────────────────────────────────────────────
# Internal decision tree — exact port of MQL5 Classify() function
# ──────────────────────────────────────────────────────────────────────────────
def _classify_tree(
    body: float,
    dom: float,
    counter: float,
    ratio: float,
    bull: bool,
    p_dir: bool,
    r_dir: bool,
    dom_gt_body: bool,
) -> str:
    """
    Exact port of the MQL5 Classify() function (lines 344-441 of source).
    All branch thresholds are identical to the MQL5 source.
    Returns one of: C  N  B  P  J  R  L  S  WR  WB
    """
    if body <= 69.03:
        if counter <= 26.01:
            if dom <= 39.21:
                if body <= 49.56:
                    return "S"
                # body > 49.56
                if dom <= 22.54:
                    return "B" if body <= 59.17 else "N"
                # dom > 22.54
                if dom <= 35.15:
                    if body <= 61.41:
                        if dom <= 32.52:
                            if body <= 59.73:
                                return "B"
                            return "N" if dom <= 27.78 else "B"
                        # dom > 32.52
                        if not p_dir:
                            return "S" if body <= 57.36 else "B"
                        return "B"
                    # body > 61.41
                    if dom <= 30.29:
                        if body <= 64.48:
                            if not r_dir:
                                return "N"
                            return "N" if ratio <= 2.15 else "B"
                        return "N"
                    return "B"
                # 35.15 < dom <= 39.21  (counter <= 26.01, body > 49.56, dom > 22.54)
                if not r_dir:
                    return "B"
                return "WB" if counter <= 10.20 else "S"

            # dom > 39.21,  counter <= 26.01
            if not p_dir:
                if dom <= 57.38:
                    if body <= 50.25:
                        if counter <= 4.74:
                            if dom <= 51.09:
                                return "WR" if bull else "S"
                            return "WR"
                        # counter > 4.74
                        if dom <= 52.51:
                            if body <= 26.29:
                                return "L" if dom <= 51.19 else "S"
                            if dom <= 48.58:
                                return "S"
                            return "WR"
                        # dom > 52.51
                        if body <= 19.87:
                            return "L"
                        if body <= 28.29:
                            return "S"
                        return "S" if counter <= 6.50 else "R"
                    # body > 50.25
                    return "WB"
                # dom > 57.38
                if body <= 1.61:
                    return "J"
                if ratio <= 2.97:
                    return "S" if counter <= 24.26 else "L"
                return "R" if body <= 39.70 else "WR"

            # p_dir is True,  dom > 39.21,  counter <= 26.01
            if ratio <= 2.36:
                return "L" if body <= 26.79 else "S"
            if body <= 1.61:
                return "J"
            return "P" if body <= 58.85 else "B"

        # counter > 26.01,  body <= 69.03
        if body <= 32.29:
            if body <= 2.07:
                return "J" if ratio <= 2.56 else "P"
            return "L"
        # body > 32.29,  counter > 26.01
        return "S"

    # body > 69.03
    if dom <= 25.87:
        if body <= 70.29:
            if not p_dir:
                return "C" if ratio <= 2.27 else "N"
            return "C"
        # body > 70.29,  dom <= 25.87
        return "C"

    # dom > 25.87,  body > 69.03
    if not p_dir:
        return "B" if body <= 69.80 else "N"
    return "C"


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────
def classify_candle(
    open_price: float,
    high: float,
    low: float,
    close: float,
) -> Tuple[str, str]:
    """
    Classify a single closed OHLC candle.

    Replicates the OnCalculate() pre-processing from the MQL5 source
    (lines 470-487) and then delegates to _classify_tree().

    Parameters
    ----------
    open_price : float
    high       : float
    low        : float
    close      : float

    Returns
    -------
    label : str
        One of: C  N  B  P  J  R  L  S  WR  WB
        Rule A  → D is treated identically to J (classifier never returns D,
                  but any downstream code equating D with J is already correct).
    direction : str
        'BULL'  — close >= open  and  label != J
        'BEAR'  — close <  open  and  label != J
        'NONE'  — label == J
        Rule B  → J candle is neither Bullish nor Bearish; Open==Close
                  is impossible as a standalone edge case because the
                  classifier itself returns J in that situation.
        Rule B  → Upper Shadow == Lower Shadow on P/R cannot occur;
                  classifier never produces P or R in that case.
    """
    rng = high - low

    # Zero-range candle: classifier cannot run → treat as J (no direction)
    if rng <= 0.0:
        return "J", "NONE"

    # ── Feature computation (mirrors MQL5 lines 473-483) ─────────────────────
    body        = abs(close - open_price) / rng * 100.0
    upper       = (high - max(open_price, close)) / rng * 100.0
    lower       = (min(open_price, close) - low)  / rng * 100.0

    bull        = close >= open_price
    dom         = max(upper, lower)
    counter     = min(upper, lower)
    ratio       = dom / (counter + 0.1)
    p_dir       = (bull and lower > upper) or (not bull and upper > lower)
    r_dir       = not p_dir
    dom_gt_body = dom > body

    # ── Classification (MQL5 lines 486-487) ──────────────────────────────────
    if body < 0.5:
        label = "J"
    else:
        label = _classify_tree(body, dom, counter, ratio,
                               bull, p_dir, r_dir, dom_gt_body)

    # ── Direction (Rules A / B) ───────────────────────────────────────────────
    direction = "NONE" if label == "J" else ("BULL" if bull else "BEAR")

    return label, direction
