"""Work windows: N9 runs only while the owner is active or passively working. Code decides."""
import time
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Window:
    state: str                     # active | passive | closed
    started_at: Optional[float]    # when this window opened (None when closed)


def current(store, window_cfg: dict, now: Optional[float] = None) -> Window:
    """Compute the window and record when it opened, so N9 can count its runs per window."""
    now = now or time.time()
    last = store.last_activity()
    active_s = float(window_cfg["active_minutes"]) * 60
    passive_s = float(window_cfg["passive_hours"]) * 3600
    state = "closed"
    if last and now - last <= active_s:
        state = "active"
    elif last and now - last <= passive_s and store.one(
            "SELECT number FROM tickets WHERE state IN ('running','waiting') AND labeled_at >= ?", (now - passive_s,)):
        state = "passive"
    if state == "closed":
        store.set_meta("window_start", None)
        return Window("closed", None)
    if store.meta("window_start") is None:
        store.set_meta("window_start", str(now))
    return Window(state, float(store.meta("window_start")))
