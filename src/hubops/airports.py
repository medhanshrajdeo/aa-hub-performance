"""Airport coordinate -> IANA time zone lookup.

Shared by bts_times.py (flight local times) and metar.py (weather observation
times): both need to turn an airport's lat/long into a tz name before they
can do anything date-arithmetic-shaped.
"""

from __future__ import annotations

from functools import lru_cache

from timezonefinder import TimezoneFinder

_finder = TimezoneFinder()


@lru_cache(maxsize=512)
def resolve_airport_tz(lat: float, lon: float) -> str:
    """Return the IANA tz name (e.g. "America/Chicago") for an airport's coordinates.

    Raises ValueError if the point doesn't resolve to a known tz (bad/missing
    coordinates in the FAA/airport reference data) -- caller should route the
    row to quarantine rather than silently defaulting to UTC.
    """
    tz_name = _finder.timezone_at(lat=lat, lng=lon)
    if tz_name is None:
        raise ValueError(f"no timezone found for coordinates ({lat}, {lon})")
    return tz_name
