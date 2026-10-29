from hubops.metar import parse_metar, weather_severity_score


def test_parses_thunderstorm_low_ceiling_low_visibility():
    raw = "KDFW 151253Z 18015G25KT 3/4SM TSRA BKN008 OVC015 24/22 A2990"
    parsed = parse_metar(raw)

    assert parsed.station == "KDFW"
    assert parsed.has_thunderstorm is True
    assert parsed.ceiling_ft == 800  # lowest of BKN008 (800ft) and OVC015 (1500ft)
    assert parsed.visibility_mi == 0.75
    assert parsed.wind_gust_kt == 25


def test_severity_score_counts_three_of_four_flags():
    raw = "KDFW 151253Z 18015G25KT 3/4SM TSRA BKN008 OVC015 24/22 A2990"
    # thunderstorm + ceiling<1000 + visibility<3 trip; gust 25kt does not (>30 required)
    assert weather_severity_score(raw) == 3


def test_clear_calm_day_scores_zero():
    raw = "KDFW 151253Z 00000KT 10SM CLR 24/18 A3012"
    parsed = parse_metar(raw)

    assert parsed.has_thunderstorm is False
    assert parsed.ceiling_ft is None
    assert parsed.visibility_mi == 10.0
    assert parsed.wind_gust_kt is None
    assert weather_severity_score(raw) == 0


def test_high_gust_alone_scores_one():
    raw = "KDFW 151253Z 18020G35KT 10SM FEW250 24/18 A2990"
    parsed = parse_metar(raw)

    assert parsed.wind_gust_kt == 35
    assert parsed.ceiling_ft is None  # FEW is not a ceiling layer
    assert weather_severity_score(raw) == 1


def test_mixed_whole_and_fraction_visibility():
    raw = "KDFW 151253Z 09010KT 1 1/2SM BR OVC004 18/17 A2998"
    parsed = parse_metar(raw)

    assert parsed.visibility_mi == 1.5
    assert parsed.ceiling_ft == 400


def test_less_than_quarter_mile_visibility():
    raw = "KDFW 151253Z 09010KT M1/4SM FG VV002 18/17 A2998"
    parsed = parse_metar(raw)

    assert parsed.visibility_mi == 0.25
    assert parsed.ceiling_ft == 200  # vertical visibility (obscured sky) counts as a ceiling


def test_remarks_section_is_ignored():
    raw = (
        "KDFW 151253Z 18015G25KT 3/4SM TSRA BKN008 OVC015 24/22 A2990 "
        "RMK AO2 TSB25 SLP120 T02440222"
    )
    parsed = parse_metar(raw)
    # "TSB25" in the remarks section looks thunderstorm-ish but must not be
    # double-counted or cause a parse error -- it's simply not inspected.
    assert parsed.has_thunderstorm is True  # already true from the body's TSRA
    assert parsed.ceiling_ft == 800
