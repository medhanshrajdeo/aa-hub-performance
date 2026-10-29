"""Bank detection: turn a day's scheduled-flight-count-per-15-minute-slot
series into named departure/arrival "banks" (the wave structure a connecting
hub schedules around).

This is signature analysis #1 from the project plan: reconstruct the
station's bank structure directly from the public schedule (no internal
schedule data needed), so a before/after comparison around the April 2026
DFW rebank is possible. Peaks are detected with scipy.signal.find_peaks;
window boundaries come from scipy.signal.peak_widths at half-height, which
is a standard, defensible way to turn "a bump in a histogram" into "a start
and end time" without hand-tuned thresholds per peak.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.signal import find_peaks, peak_widths


@dataclass(frozen=True)
class BankWindow:
    bank_number: int
    peak_slot: int
    peak_count: float
    start_slot: int
    end_slot: int


def detect_bank_windows(
    slot_counts: list[float],
    min_separation_slots: int = 2,
    min_height: float | None = None,
    rel_height: float = 0.5,
) -> list[BankWindow]:
    """Detect bank peaks and their windows from a per-slot flight-count series.

    `min_height` defaults to one standard deviation above the mean slot
    count -- a bank should stand out above typical off-peak scheduling, not
    just be a local maximum. Pass an explicit value to compare two series
    (e.g. pre/post rebank) against the same threshold.
    """
    counts = np.asarray(slot_counts, dtype=float)
    if min_height is None:
        min_height = float(counts.mean() + counts.std())

    peak_slots, _ = find_peaks(counts, distance=min_separation_slots, height=min_height)
    if len(peak_slots) == 0:
        return []

    widths, _width_heights, left_ips, right_ips = peak_widths(
        counts, peak_slots, rel_height=rel_height
    )

    return [
        BankWindow(
            bank_number=i + 1,
            peak_slot=int(slot),
            peak_count=float(counts[slot]),
            start_slot=int(round(left_ips[i])),
            end_slot=int(round(right_ips[i])),
        )
        for i, slot in enumerate(peak_slots)
    ]


def slot_to_hhmm(slot: int, slot_minutes: int = 15) -> str:
    """Format a slot index (0 = 00:00) as "HH:MM", wrapping past midnight."""
    total_minutes = (slot * slot_minutes) % (24 * 60)
    hour, minute = divmod(total_minutes, 60)
    return f"{hour:02d}:{minute:02d}"
