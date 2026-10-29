import numpy as np

from hubops.banks import detect_bank_windows, slot_to_hhmm


def _gaussian_bump(center: int, width: float, height: float, n_slots: int) -> np.ndarray:
    x = np.arange(n_slots)
    return height * np.exp(-((x - center) ** 2) / (2 * width**2))


def test_detects_three_clean_banks():
    n_slots = 96  # a full day at 15-minute resolution
    baseline = np.full(n_slots, 2.0)
    signal = (
        baseline
        + _gaussian_bump(center=24, width=2, height=20, n_slots=n_slots)  # ~06:00
        + _gaussian_bump(center=48, width=2, height=18, n_slots=n_slots)  # ~12:00
        + _gaussian_bump(center=72, width=2, height=22, n_slots=n_slots)  # ~18:00
    )

    windows = detect_bank_windows(signal.tolist())

    assert len(windows) == 3
    peak_slots = sorted(w.peak_slot for w in windows)
    assert peak_slots == [24, 48, 72]
    assert all(w.start_slot < w.peak_slot < w.end_slot for w in windows)


def test_flat_signal_detects_no_banks():
    flat = [5.0] * 96
    assert detect_bank_windows(flat) == []


def test_min_height_threshold_filters_small_bumps():
    n_slots = 96
    baseline = np.full(n_slots, 2.0)
    signal = baseline + _gaussian_bump(24, 2, 20, n_slots) + _gaussian_bump(60, 2, 3, n_slots)

    # Default threshold (mean + std) should pick up the big bank but not the tiny bump.
    windows = detect_bank_windows(signal.tolist())
    assert len(windows) == 1
    assert windows[0].peak_slot == 24

    # Explicit low threshold picks up both.
    windows_low = detect_bank_windows(signal.tolist(), min_height=4.0)
    assert len(windows_low) == 2


def test_min_separation_merges_close_peaks():
    n_slots = 96
    baseline = np.full(n_slots, 2.0)
    # Two peaks only 1 slot apart -- should be treated as one bank at distance=2.
    signal = baseline + _gaussian_bump(40, 1, 20, n_slots) + _gaussian_bump(41, 1, 19, n_slots)

    windows = detect_bank_windows(signal.tolist(), min_separation_slots=4)
    assert len(windows) == 1


def test_slot_to_hhmm():
    assert slot_to_hhmm(0) == "00:00"
    assert slot_to_hhmm(24) == "06:00"
    assert slot_to_hhmm(72) == "18:00"
    assert slot_to_hhmm(95) == "23:45"
    assert slot_to_hhmm(96) == "00:00"  # wraps past midnight
