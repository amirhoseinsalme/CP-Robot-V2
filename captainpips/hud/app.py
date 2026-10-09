"""
CaptainPips HUD — always-on-top desktop overlay.

Connects to ws://localhost:8000/ws/monitor and displays live
structure, entry watch, positions, and broken structures.

Run:  python captainpips/hud/app.py
"""

import asyncio
import json
import threading
import tkinter as tk
from datetime import datetime

import websockets

WS_URL = "ws://localhost:8000/ws/monitor"
API_BASE = "http://localhost:8000"
RECONNECT_S = 3
BG = "#1a1a1a"
HEADER_CLR = "#ef4444"
VALUE_CLR = "#e5e5e5"
MUTED_CLR = "#6b7280"
BULL_CLR = "#22c55e"
BEAR_CLR = "#ef4444"
ACTIVE_CLR = "#22c55e"   # green — active target
INACTIVE_CLR = "#374151" # dark gray — inactive target
FONT = ("Consolas", 10)
FONT_HEADER = ("Consolas", 9, "bold")
FONT_VALUE = ("Consolas", 11, "bold")
FONT_CHIP = ("Consolas", 9, "bold")
MAX_BREAKS = 3

# All targets in display order; keys accepted from server (full names + abbrs)
TARGET_ORDER = ["B", "D", "St", "M", "F", "C", "S", "G", "3"]
TARGET_TOOLTIPS = {
    "B": "Break out", "D": "Double", "St": "Structure",
    "M": "EMA", "F": "FT", "C": "Close gap",
    "S": "Size", "G": "Gap fill", "3": "3-candle gap",
}
# Map any server-side name → our abbreviation
_TARGET_ALIAS: dict[str, str] = {
    "B": "B", "breakout": "B", "break_out": "B", "break out": "B",
    "D": "D", "double": "D",
    "St": "St", "structure": "St",
    "M": "M", "ema": "M", "EMA": "M",
    "F": "F", "ft": "F", "FT": "F",
    "C": "C", "close_gap": "C", "close gap": "C",
    "S": "S", "size": "S", "Size": "S",
    "G": "G", "gap_fill": "G", "gap fill": "G",
    "3": "3", "3_candle_gap": "3", "3candle": "3", "3 candle gap": "3",
}


def _normalize_targets(raw) -> set[str]:
    """Accept a list/set of target names (full or abbreviated) and return a set of abbrs."""
    if not raw:
        return set()
    result = set()
    for item in raw:
        abbr = _TARGET_ALIAS.get(str(item))
        if abbr:
            result.add(abbr)
    return result


class HudApp:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("CP HUD")
        self.root.geometry("300x540+10+10")
        self.root.resizable(True, True)
        self.root.configure(bg=BG)
        self.root.wm_attributes("-topmost", True)

        self._direction = "None"
        self._confirmed_legs = 0
        self._looking_for = "Waiting for first candle..."
        self._entry_watch = "None"
        self._open_positions = 0
        self._breaks: list[str] = []
        self._session_start = "-"
        self._active_targets: set[str] = set()
        self._paradox_active = False
        self._ws_connected = False

        self._build_ui()
        self._start_ws_thread()

    def _build_ui(self) -> None:
        container = tk.Frame(self.root, bg=BG)
        container.pack(fill="both", expand=True, padx=0, pady=0)

        # -- STRUCTURE --
        self._add_header(container, "STRUCTURE")
        f = tk.Frame(container, bg=BG)
        f.pack(fill="x", padx=12, pady=(0, 2))

        tk.Label(f, text="Direction", font=FONT, fg=MUTED_CLR, bg=BG, anchor="w").grid(row=0, column=0, sticky="w")
        self.lbl_dir = tk.Label(f, text="None", font=FONT_VALUE, fg=MUTED_CLR, bg=BG, anchor="e")
        self.lbl_dir.grid(row=0, column=1, sticky="e")

        tk.Label(f, text="Confirmed Legs", font=FONT, fg=MUTED_CLR, bg=BG, anchor="w").grid(row=1, column=0, sticky="w")
        self.lbl_legs = tk.Label(f, text="0", font=FONT_VALUE, fg=VALUE_CLR, bg=BG, anchor="e")
        self.lbl_legs.grid(row=1, column=1, sticky="e")

        tk.Label(f, text="Paradox", font=FONT, fg=MUTED_CLR, bg=BG, anchor="w").grid(row=2, column=0, sticky="w")
        self.lbl_paradox = tk.Label(f, text="", font=FONT_VALUE, fg=MUTED_CLR, bg=BG, anchor="e")
        self.lbl_paradox.grid(row=2, column=1, sticky="e")

        f.columnconfigure(0, weight=1)
        f.columnconfigure(1, weight=1)

        # -- TARGETS (compact chip row) --
        tf = tk.Frame(container, bg=BG)
        tf.pack(fill="x", padx=12, pady=(2, 4))
        self._target_labels: dict[str, tk.Label] = {}
        for abbr in TARGET_ORDER:
            lbl = tk.Label(
                tf, text=abbr, font=FONT_CHIP,
                fg=INACTIVE_CLR, bg=BG, padx=3,
            )
            lbl.pack(side="left")
            self._target_labels[abbr] = lbl

        self._add_sep(container)

        # -- LOOKING FOR --
        self._add_header(container, "LOOKING FOR")
        self.lbl_looking = tk.Label(
            container, text="Waiting for first candle...",
            font=FONT, fg=VALUE_CLR, bg=BG, anchor="w", wraplength=276, justify="left",
        )
        self.lbl_looking.pack(fill="x", padx=12, pady=(0, 4))

        self._add_sep(container)

        # -- ENTRY WATCH --
        self._add_header(container, "ENTRY WATCH")
        self.lbl_entry = tk.Label(
            container, text="None", font=FONT_VALUE, fg=VALUE_CLR, bg=BG, anchor="w",
        )
        self.lbl_entry.pack(fill="x", padx=12, pady=(0, 4))

        self._add_sep(container)

        # -- OPEN POSITIONS --
        self._add_header(container, "OPEN POSITIONS")
        self.lbl_positions = tk.Label(
            container, text="None", font=FONT_VALUE, fg=VALUE_CLR, bg=BG, anchor="w",
        )
        self.lbl_positions.pack(fill="x", padx=12, pady=(0, 4))

        self._add_sep(container)

        # -- BROKEN STRUCTURES --
        self._add_header(container, "BROKEN STRUCTURES")
        self.lbl_breaks = tk.Label(
            container, text="None", font=FONT, fg=MUTED_CLR, bg=BG, anchor="w",
            wraplength=276, justify="left",
        )
        self.lbl_breaks.pack(fill="x", padx=12, pady=(0, 4))

        self._add_sep(container)

        # -- SESSION --
        self._add_header(container, "SESSION")
        f2 = tk.Frame(container, bg=BG)
        f2.pack(fill="x", padx=12, pady=(0, 4))

        tk.Label(f2, text="Start", font=FONT, fg=MUTED_CLR, bg=BG, anchor="w").grid(row=0, column=0, sticky="w")
        self.lbl_session = tk.Label(f2, text="-", font=FONT_VALUE, fg=VALUE_CLR, bg=BG, anchor="e")
        self.lbl_session.grid(row=0, column=1, sticky="e")

        tk.Label(f2, text="WS", font=FONT, fg=MUTED_CLR, bg=BG, anchor="w").grid(row=1, column=0, sticky="w")
        self.lbl_ws = tk.Label(f2, text="Disconnected", font=FONT, fg=BEAR_CLR, bg=BG, anchor="e")
        self.lbl_ws.grid(row=1, column=1, sticky="e")

        f2.columnconfigure(0, weight=1)
        f2.columnconfigure(1, weight=1)

    def _add_header(self, parent: tk.Frame, text: str) -> None:
        tk.Label(
            parent, text=text, font=FONT_HEADER, fg=HEADER_CLR, bg=BG, anchor="w",
        ).pack(fill="x", padx=12, pady=(8, 2))

    def _add_sep(self, parent: tk.Frame) -> None:
        tk.Frame(parent, bg="#2a2a2a", height=1).pack(fill="x", padx=0, pady=2)

    # ------------------------------------------------------------------
    # WebSocket
    # ------------------------------------------------------------------

    def _start_ws_thread(self) -> None:
        t = threading.Thread(target=self._ws_loop, daemon=True)
        t.start()

    def _ws_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self._ws_connect_forever())

    async def _ws_connect_forever(self) -> None:
        while True:
            try:
                async with websockets.connect(WS_URL) as ws:
                    self._ws_connected = True
                    self.root.after(0, self._render_ws)
                    await self._fetch_initial()
                    async for raw in ws:
                        try:
                            msg = json.loads(raw)
                            self.root.after(0, self._dispatch, msg)
                        except json.JSONDecodeError:
                            pass
            except Exception:
                self._ws_connected = False
                self.root.after(0, self._render_ws)
            await asyncio.sleep(RECONNECT_S)

    async def _fetch_initial(self) -> None:
        # Fetch structure state
        try:
            import urllib.request
            with urllib.request.urlopen(f"{API_BASE}/api/structure") as r:
                data = json.loads(r.read())
            if data.get("symbols"):
                for sym_state in data["symbols"].values():
                    self._direction = sym_state.get("direction") or "None"
                    self._confirmed_legs = sym_state.get("confirmed_legs", 0)
                    if sym_state.get("active_targets"):
                        # Initial fetch: replace (full snapshot from server)
                        self._active_targets = _normalize_targets(sym_state["active_targets"])
                    self._update_looking_for()
                self.root.after(0, self._render_all)
        except Exception:
            pass

        # Fetch config — session start time
        fetched_session = False
        try:
            import urllib.request
            with urllib.request.urlopen(f"{API_BASE}/api/config") as r:
                cfg = json.loads(r.read())
            val = cfg.get("session_start") or cfg.get("session_time") or cfg.get("start_time")
            if val:
                self._session_start = str(val)
                fetched_session = True
                self.root.after(0, self._render_all)
        except Exception:
            pass

        # Fallback: record connection time if still unknown
        if not fetched_session and self._session_start == "-":
            self._session_start = datetime.now().strftime("%H:%M")
            self.root.after(0, self._render_all)

    # ------------------------------------------------------------------
    # Message dispatch
    # ------------------------------------------------------------------

    def _dispatch(self, msg: dict) -> None:
        t = (msg.get("type") or "").lower()

        # Grab session_start from ANY message that carries it
        for key in ("session_start", "session_time", "start_time"):
            if msg.get(key) and self._session_start in ("-", ""):
                self._session_start = str(msg[key])
                break

        if t == "structure":
            self._on_structure(msg)
        elif t == "signal":
            self._on_signal(msg)
        elif t == "account":
            self._on_account(msg)
        elif t == "candle":
            self._on_candle(msg)
        elif t == "watch_update":
            self._on_watch_update(msg)
        elif t in ("config", "session"):
            self._on_config(msg)
        self._render_all()

    def _on_config(self, m: dict) -> None:
        for key in ("session_start", "session_time", "start_time"):
            if m.get(key):
                self._session_start = str(m[key])
                break

    def _on_structure(self, m: dict) -> None:
        if m.get("direction"):
            self._direction = m["direction"]
        if m.get("confirmed_legs") is not None:
            self._confirmed_legs = m["confirmed_legs"]
        if m.get("waiting_for"):
            self._looking_for = m["waiting_for"]
        else:
            self._update_looking_for()
        if "paradox_active" in m:
            self._paradox_active = bool(m["paradox_active"])
        if m.get("watch"):
            w = m["watch"]
            mode = f" | Mode {w['mode']}" if w.get("mode") else ""
            self._entry_watch = f"{w.get('strategy', 'Signal')}{mode}"
        # Active targets: ACCUMULATE — server sends only the newly confirmed
        # target; previously confirmed ones must stay lit until reset.
        if "active_targets" in m:
            self._active_targets.update(_normalize_targets(m["active_targets"]))
        if m.get("event") == "STRUCTURE_BROKEN" or m.get("broken"):
            d = m.get("direction", "?")
            legs = m.get("confirmed_legs", 0)
            self._breaks.append(f"{d} {legs} leg")
            if len(self._breaks) > MAX_BREAKS:
                self._breaks.pop(0)
        if m.get("event") == "RESET" or m.get("reset"):
            self._breaks.clear()
            self._active_targets.clear()

    def _on_signal(self, m: dict) -> None:
        strategy = m.get("strategy", "Signal")
        mode = f" | Mode {m['mode']}" if m.get("mode") else ""
        self._entry_watch = f"{strategy}{mode}"
        # Accumulate — don't wipe chips that were already confirmed
        if "active_targets" in m:
            self._active_targets.update(_normalize_targets(m["active_targets"]))

    def _on_account(self, m: dict) -> None:
        for key in ("open_positions", "positions", "active_trade_count"):
            if m.get(key) is not None:
                self._open_positions = m[key]
                break

    def _on_candle(self, m: dict) -> None:
        if m.get("watch"):
            w = m["watch"]
            mode = f" | Mode {w['mode']}" if w.get("mode") else ""
            self._entry_watch = f"{w.get('strategy', 'Signal')}{mode}"
        # Accumulate — don't wipe chips that were already confirmed
        if "active_targets" in m:
            self._active_targets.update(_normalize_targets(m["active_targets"]))

    def _on_watch_update(self, m: dict) -> None:
        strategy = m.get("strategy", "Signal")
        mode = m.get("mode", "")
        if mode:
            self._entry_watch = f"{strategy} | Mode {mode}"
        else:
            self._entry_watch = strategy if strategy else "None"
        # Accumulate — don't wipe chips that were already confirmed
        if "active_targets" in m:
            self._active_targets.update(_normalize_targets(m["active_targets"]))

    def _update_looking_for(self) -> None:
        if self._direction in (None, "None"):
            self._looking_for = "Waiting for first candle..."
        elif self._confirmed_legs == 0:
            self._looking_for = "Building first leg"
        elif self._confirmed_legs == 1:
            self._looking_for = "Correction in stop"
        else:
            self._looking_for = "BO in leg direction"

    # ------------------------------------------------------------------
    # Render
    # ------------------------------------------------------------------

    def _render_all(self) -> None:
        d = self._direction
        if d == "BULL":
            self.lbl_dir.config(text="Bull ▲", fg=BULL_CLR)
        elif d == "BEAR":
            self.lbl_dir.config(text="Bear ▼", fg=BEAR_CLR)
        else:
            self.lbl_dir.config(text="None", fg=MUTED_CLR)

        self.lbl_legs.config(text=str(self._confirmed_legs))
        if self._paradox_active:
            self.lbl_paradox.config(text="⚡ PARADOX", fg="#f59e0b")
        else:
            self.lbl_paradox.config(text="", fg=MUTED_CLR)
        self.lbl_looking.config(text=self._looking_for)
        self.lbl_entry.config(text=self._entry_watch)
        self.lbl_positions.config(
            text=str(self._open_positions) if self._open_positions > 0 else "None"
        )
        if self._breaks:
            self.lbl_breaks.config(text="\n".join(self._breaks), fg=VALUE_CLR)
        else:
            self.lbl_breaks.config(text="None", fg=MUTED_CLR)
        self.lbl_session.config(text=self._session_start)

        # Targets: green if active, dark gray if not
        for abbr, lbl in self._target_labels.items():
            if abbr in self._active_targets:
                lbl.config(fg=ACTIVE_CLR)
            else:
                lbl.config(fg=INACTIVE_CLR)

    def _render_ws(self) -> None:
        if self._ws_connected:
            self.lbl_ws.config(text="Live", fg="#3b82f6")
        else:
            self.lbl_ws.config(text="Disconnected", fg=BEAR_CLR)

    def run(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    HudApp().run()
