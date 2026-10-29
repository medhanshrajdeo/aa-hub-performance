"""Aircraft turn pairing: fact_aircraft_turn's reason to exist.

A "turn" is one tail's inbound arrival at a station paired with its next
departure from that same station. BTS gives you flight legs, not aircraft
rotations, so the pairing has to be reconstructed: for a given tail, sort
its legs chronologically and walk consecutive pairs. A pair is a valid turn
only if the next leg actually departs from where the previous one landed
(same station), the gap is non-negative, and it's under 6 hours -- longer
gaps are overnight parking, not a turn a station leader can be scored on.

Legs that fail pairing (tail reassigned mid-sequence, missing actual time,
gap too long, end of the data window) are returned separately with a reason
code rather than silently dropped -- see docs/kpi_spec.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

_MAX_TURN_GAP = timedelta(hours=6)


@dataclass(frozen=True)
class FlightLeg:
    leg_id: str
    tail_number: str
    origin: str
    dest: str
    dep_scheduled: datetime | None
    dep_actual: datetime | None
    dep_delay_min: float | None
    arr_scheduled: datetime | None
    arr_actual: datetime | None
    arr_delay_min: float | None


@dataclass(frozen=True)
class AircraftTurn:
    tail_number: str
    station: str
    inbound_leg_id: str
    outbound_leg_id: str
    scheduled_turn_min: float | None
    actual_turn_min: float
    inbound_arr_delay_min: float
    outbound_dep_delay_min: float
    minutes_recovered: float


@dataclass(frozen=True)
class TurnQuarantine:
    tail_number: str
    leg_id: str
    reason: str


def pair_turns(legs: list[FlightLeg]) -> tuple[list[AircraftTurn], list[TurnQuarantine]]:
    """Pair consecutive legs per tail into turns. See module docstring for the rule."""
    turns: list[AircraftTurn] = []
    quarantine: list[TurnQuarantine] = []

    by_tail: dict[str, list[FlightLeg]] = {}
    for leg in legs:
        by_tail.setdefault(leg.tail_number, []).append(leg)

    for tail_number, tail_legs in by_tail.items():
        ordered = sorted(tail_legs, key=_sequence_key)

        for i, inbound in enumerate(ordered):
            if i == len(ordered) - 1:
                quarantine.append(
                    TurnQuarantine(tail_number, inbound.leg_id, "no_next_leg_in_window")
                )
                continue

            outbound = ordered[i + 1]

            if inbound.arr_actual is None or outbound.dep_actual is None:
                quarantine.append(
                    TurnQuarantine(tail_number, inbound.leg_id, "missing_actual_time")
                )
                continue

            if inbound.dest != outbound.origin:
                quarantine.append(
                    TurnQuarantine(tail_number, inbound.leg_id, "next_leg_different_station")
                )
                continue

            gap = outbound.dep_actual - inbound.arr_actual
            if gap.total_seconds() < 0:
                quarantine.append(TurnQuarantine(tail_number, inbound.leg_id, "negative_gap"))
                continue
            if gap > _MAX_TURN_GAP:
                quarantine.append(TurnQuarantine(tail_number, inbound.leg_id, "gap_too_long"))
                continue

            scheduled_turn_min = None
            if inbound.arr_scheduled is not None and outbound.dep_scheduled is not None:
                scheduled_turn_min = (
                    outbound.dep_scheduled - inbound.arr_scheduled
                ).total_seconds() / 60

            inbound_delay = inbound.arr_delay_min or 0.0
            outbound_delay = outbound.dep_delay_min or 0.0

            turns.append(
                AircraftTurn(
                    tail_number=tail_number,
                    station=inbound.dest,
                    inbound_leg_id=inbound.leg_id,
                    outbound_leg_id=outbound.leg_id,
                    scheduled_turn_min=scheduled_turn_min,
                    actual_turn_min=gap.total_seconds() / 60,
                    inbound_arr_delay_min=inbound_delay,
                    outbound_dep_delay_min=outbound_delay,
                    minutes_recovered=inbound_delay - outbound_delay,
                )
            )

    return turns, quarantine


def _sequence_key(leg: FlightLeg) -> datetime:
    # Prefer actual departure (the tail's real rotation order); fall back to
    # scheduled when actual is missing (e.g. a cancelled leg still occupies
    # a slot in the tail's day) so sequencing doesn't collapse those legs
    # onto whatever happens to sort first.
    return leg.dep_actual or leg.dep_scheduled or datetime.min
