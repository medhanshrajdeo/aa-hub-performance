import pytest

from hubops.airports import resolve_airport_tz


def test_dfw_resolves_to_chicago_time():
    assert resolve_airport_tz(32.8998, -97.0403) == "America/Chicago"


def test_lax_resolves_to_los_angeles_time():
    assert resolve_airport_tz(33.9416, -118.4085) == "America/Los_Angeles"


def test_honolulu_resolves_to_pacific_honolulu():
    assert resolve_airport_tz(21.3187, -157.9224) == "Pacific/Honolulu"


def test_result_is_cached():
    first = resolve_airport_tz(32.8998, -97.0403)
    second = resolve_airport_tz(32.8998, -97.0403)
    assert first == second
    info = resolve_airport_tz.cache_info()
    assert info.hits >= 1


def test_invalid_coordinates_raise():
    with pytest.raises(ValueError):
        resolve_airport_tz(999.0, 999.0)
