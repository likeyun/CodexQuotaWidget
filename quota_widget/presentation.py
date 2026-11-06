"""Choose the one quota window shown by the compact desktop widget."""

from __future__ import annotations

import math
from typing import Any


def select_display_window(data: dict[str, Any] | None) -> tuple[str, dict[str, Any] | None]:
    """Prefer an explicitly identified five-hour window, then a weekly window.

    A zero remaining balance is valid. Missing or invalid balances and unknown
    window durations are never inferred to mean an available five-hour quota.
    Previously fetched (stale) windows remain eligible for display.
    """
    windows = data.get("windows") if isinstance(data, dict) else None
    if not isinstance(windows, dict):
        return "unknown", None

    candidates = []
    for key in ("primary", "secondary"):
        window = windows.get(key)
        if not isinstance(window, dict):
            continue
        remaining = window.get("remaining_percent")
        if (
            isinstance(remaining, (int, float))
            and not isinstance(remaining, bool)
            and 0 <= remaining <= 100
            and math.isfinite(remaining)
        ):
            candidates.append(window)

    for kind, seconds in (("five_hour", 18000), ("weekly", 604800)):
        for window in candidates:
            if window.get("limit_window_seconds") == seconds:
                return kind, window
    return "unknown", None
