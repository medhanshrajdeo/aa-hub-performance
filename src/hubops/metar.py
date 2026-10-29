"""METAR parsing and weather severity scoring.

IEM ASOS hands back raw METAR text, not a clean visibility/ceiling/wind
table -- e.g. "KDFW 151253Z 18015G25KT 3/4SM TSRA BKN008 OVC015 24/22 A2990".
This pulls the four signals the project plan scores stations on: a
thunderstorm group, ceiling under 1,000 ft, visibility under 3 statute
miles, and wind gusts over 30 kt.

Severity score is an unweighted count of those four flags (0-4). The plan
doesn't specify relative weights, and there's no real distribution to tune
against yet -- phase 4 can revisit weighting once station-hours are loaded
and the scorecard (analysis #4) is checked against known bad-weather days.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_WIND_RE = re.compile(r"^(?:\d{3}|VRB)(\d{2,3})(?:G(\d{2,3}))?KT$")
_VISIBILITY_WHOLE_RE = re.compile(r"^(\d{1,2})SM$")
_VISIBILITY_FRACTION_RE = re.compile(r"^(M)?(\d+)/(\d+)SM$")
_VISIBILITY_MIXED_RE = re.compile(r"^(\d{1,2}) (\d+)/(\d+)SM$")
_CLOUD_RE = re.compile(r"^(FEW|SCT|BKN|OVC|VV)(\d{3})$")
_WEATHER_PHENOMENON_RE = re.compile(
    r"^[+-]?(VC)?(MI|PR|BC|DR|BL|SH|TS|FZ)?"
    r"(DZ|RA|SN|SG|IC|PL|GR|GS|UP|FG|BR|SA|DU|HZ|FU|VA|PY|PO|SQ|FC|SS|DS)*$"
)
_CEILING_COVER_CODES = {"BKN", "OVC", "VV"}


@dataclass(frozen=True)
class ParsedMetar:
    station: str | None
    wind_gust_kt: int | None
    visibility_mi: float | None
    ceiling_ft: int | None
    has_thunderstorm: bool


def parse_metar(raw: str) -> ParsedMetar:
    """Parse one raw METAR observation line into the fields we score on."""
    body = raw.split("RMK")[0].strip()
    tokens = body.split()

    station = tokens[0] if tokens and re.match(r"^[A-Z0-9]{4}$", tokens[0]) else None

    wind_gust_kt: int | None = None
    visibility_mi: float | None = None
    ceiling_ft: int | None = None
    has_thunderstorm = False

    i = 0
    while i < len(tokens):
        token = tokens[i]

        wind_match = _WIND_RE.match(token)
        if wind_match and wind_gust_kt is None:
            gust = wind_match.group(2)
            if gust is not None:
                wind_gust_kt = int(gust)
            i += 1
            continue

        vis = _parse_visibility_token(tokens, i)
        if vis is not None:
            value, consumed = vis
            visibility_mi = value
            i += consumed
            continue

        cloud_match = _CLOUD_RE.match(token)
        if cloud_match:
            cover, height_hundreds_ft = cloud_match.groups()
            if cover in _CEILING_COVER_CODES:
                height_ft = int(height_hundreds_ft) * 100
                if ceiling_ft is None or height_ft < ceiling_ft:
                    ceiling_ft = height_ft
            i += 1
            continue

        if "TS" in token and _WEATHER_PHENOMENON_RE.match(token):
            has_thunderstorm = True
            i += 1
            continue

        i += 1

    return ParsedMetar(
        station=station,
        wind_gust_kt=wind_gust_kt,
        visibility_mi=visibility_mi,
        ceiling_ft=ceiling_ft,
        has_thunderstorm=has_thunderstorm,
    )


def _parse_visibility_token(tokens: list[str], i: int) -> tuple[float, int] | None:
    """Try to parse a visibility value starting at tokens[i].

    Handles whole-mile ("10SM"), simple fraction ("3/4SM", "M1/4SM" meaning
    "less than 1/4"), and mixed whole-plus-fraction split across two tokens
    by METAR's tokenizer ("1" "1/2SM" -> 1.5SM).
    """
    token = tokens[i]

    whole_match = _VISIBILITY_WHOLE_RE.match(token)
    if whole_match:
        return float(whole_match.group(1)), 1

    fraction_match = _VISIBILITY_FRACTION_RE.match(token)
    if fraction_match:
        less_than, numerator, denominator = fraction_match.groups()
        value = int(numerator) / int(denominator)
        return value, 1

    if i + 1 < len(tokens):
        combined = f"{token} {tokens[i + 1]}"
        mixed_match = _VISIBILITY_MIXED_RE.match(combined)
        if mixed_match:
            whole, numerator, denominator = mixed_match.groups()
            value = int(whole) + int(numerator) / int(denominator)
            return value, 2

    return None


def weather_severity_score(raw: str) -> int:
    """Count of triggered severity flags (0-4): thunderstorm, ceiling <1000ft,
    visibility <3mi, gusts >30kt."""
    parsed = parse_metar(raw)
    score = 0
    if parsed.has_thunderstorm:
        score += 1
    if parsed.ceiling_ft is not None and parsed.ceiling_ft < 1000:
        score += 1
    if parsed.visibility_mi is not None and parsed.visibility_mi < 3:
        score += 1
    if parsed.wind_gust_kt is not None and parsed.wind_gust_kt > 30:
        score += 1
    return score
