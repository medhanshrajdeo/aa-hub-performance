from datetime import datetime

from hubops.turns import FlightLeg, pair_turns


def _leg(leg_id, tail, origin, dest, dep_sched, dep_act, dep_delay, arr_sched, arr_act, arr_delay):
    return FlightLeg(
        leg_id=leg_id,
        tail_number=tail,
        origin=origin,
        dest=dest,
        dep_scheduled=dep_sched,
        dep_actual=dep_act,
        dep_delay_min=dep_delay,
        arr_scheduled=arr_sched,
        arr_actual=arr_act,
        arr_delay_min=arr_delay,
    )


def test_clean_turn_pairs_and_computes_minutes_recovered():
    inbound = _leg(
        "L1", "N123AA", "LAX", "DFW",
        datetime(2026, 4, 15, 8, 0), datetime(2026, 4, 15, 8, 20), 20,
        datetime(2026, 4, 15, 12, 0), datetime(2026, 4, 15, 12, 10), 10,
    )
    outbound = _leg(
        "L2", "N123AA", "DFW", "ORD",
        datetime(2026, 4, 15, 13, 0), datetime(2026, 4, 15, 13, 5), 5,
        datetime(2026, 4, 15, 15, 0), datetime(2026, 4, 15, 15, 0), 0,
    )
    turns, quarantine = pair_turns([inbound, outbound])

    assert len(turns) == 1
    assert len(quarantine) == 1
    assert quarantine[0].leg_id == "L2"
    assert quarantine[0].reason == "no_next_leg_in_window"
    turn = turns[0]
    assert turn.station == "DFW"
    assert turn.inbound_leg_id == "L1"
    assert turn.outbound_leg_id == "L2"
    assert turn.actual_turn_min == 55  # 12:10 -> 13:05
    assert turn.scheduled_turn_min == 60  # 12:00 -> 13:00
    assert turn.minutes_recovered == 10 - 5  # inbound delay minus outbound delay


def test_positioning_leg_different_station_is_quarantined():
    inbound = _leg(
        "L1", "N999AA", "LAX", "DFW",
        None, datetime(2026, 4, 15, 8, 0), 0,
        None, datetime(2026, 4, 15, 12, 0), 0,
    )
    # Next leg departs from ORD, not DFW -- can't be this tail's next turn at DFW.
    outbound = _leg(
        "L2", "N999AA", "ORD", "JFK",
        None, datetime(2026, 4, 15, 13, 0), 0,
        None, datetime(2026, 4, 15, 15, 0), 0,
    )
    turns, quarantine = pair_turns([inbound, outbound])

    assert turns == []
    reasons = {q.leg_id: q.reason for q in quarantine}
    assert reasons["L1"] == "next_leg_different_station"
    assert reasons["L2"] == "no_next_leg_in_window"


def test_gap_over_six_hours_is_quarantined_not_a_turn():
    inbound = _leg(
        "L1", "N777AA", "LAX", "DFW",
        None, datetime(2026, 4, 15, 8, 0), 0,
        None, datetime(2026, 4, 15, 12, 0), 0,
    )
    outbound = _leg(
        "L2", "N777AA", "DFW", "ORD",
        None, datetime(2026, 4, 15, 19, 0), 0,  # 7h later: overnight parking, not a turn
        None, datetime(2026, 4, 15, 21, 0), 0,
    )
    turns, quarantine = pair_turns([inbound, outbound])

    assert turns == []
    assert quarantine[0].reason == "gap_too_long"


def test_negative_gap_is_quarantined():
    # Data error: outbound actual departure before inbound actual arrival.
    inbound = _leg(
        "L1", "N555AA", "LAX", "DFW",
        None, datetime(2026, 4, 15, 8, 0), 0,
        None, datetime(2026, 4, 15, 12, 0), 0,
    )
    outbound = _leg(
        "L2", "N555AA", "DFW", "ORD",
        None, datetime(2026, 4, 15, 11, 0), 0,
        None, datetime(2026, 4, 15, 13, 0), 0,
    )
    turns, quarantine = pair_turns([inbound, outbound])

    assert turns == []
    assert quarantine[0].reason == "negative_gap"


def test_missing_actual_time_is_quarantined():
    inbound = _leg(
        "L1", "N444AA", "LAX", "DFW",
        None, None, None,  # cancelled leg: no actual departure
        None, None, None,
    )
    outbound = _leg(
        "L2", "N444AA", "DFW", "ORD",
        None, datetime(2026, 4, 15, 13, 0), 0,
        None, datetime(2026, 4, 15, 15, 0), 0,
    )
    turns, quarantine = pair_turns([inbound, outbound])

    assert turns == []
    reasons = [q.reason for q in quarantine]
    assert "missing_actual_time" in reasons


def test_sequencing_uses_actual_departure_order_across_three_legs():
    # Three legs for one tail in one day; must sequence LAX->DFW->ORD->JFK
    # correctly and produce two turns (at DFW and at ORD).
    leg1 = _leg(
        "L1", "N1AA", "LAX", "DFW",
        None, datetime(2026, 4, 15, 6, 0), 0,
        None, datetime(2026, 4, 15, 10, 0), 0,
    )
    leg2 = _leg(
        "L2", "N1AA", "DFW", "ORD",
        None, datetime(2026, 4, 15, 11, 0), 0,
        None, datetime(2026, 4, 15, 13, 0), 0,
    )
    leg3 = _leg(
        "L3", "N1AA", "ORD", "JFK",
        None, datetime(2026, 4, 15, 14, 0), 0,
        None, datetime(2026, 4, 15, 16, 0), 0,
    )
    # Pass out of order to prove sorting works.
    turns, quarantine = pair_turns([leg3, leg1, leg2])

    stations = sorted(t.station for t in turns)
    assert stations == ["DFW", "ORD"]
    assert len(quarantine) == 1 and quarantine[0].leg_id == "L3"
