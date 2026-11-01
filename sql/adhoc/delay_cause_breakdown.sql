-- "Break down cause-minutes by month/station for <carrier family>."
-- BTS only populates cause-minute buckets for arrivals delayed 15+ min
-- (ArrDel15=1) -- see docs/kpi_spec.md's "Controllable delay share" edge
-- case. This query implicitly excludes everything below that threshold
-- because the cause columns are NULL (not zero) for them, which SUM()
-- already handles correctly.

DECLARE @station VARCHAR(3) = 'DFW';
DECLARE @aa_family_only BIT = 1;  -- AA mainline + Envoy/PSA/Eagle regionals

SELECT
    d.year,
    d.month_name,
    f.origin_airport_key AS station,
    SUM(f.carrier_delay_min) AS carrier_delay_min,
    SUM(f.weather_delay_min) AS weather_delay_min,
    SUM(f.nas_delay_min) AS nas_delay_min,
    SUM(f.security_delay_min) AS security_delay_min,
    SUM(f.late_aircraft_delay_min) AS late_aircraft_delay_min,
    SUM(f.carrier_delay_min + f.weather_delay_min + f.nas_delay_min
        + f.security_delay_min + f.late_aircraft_delay_min) AS total_cause_delay_min,
    CAST(SUM(f.carrier_delay_min) AS FLOAT)
        / NULLIF(SUM(f.carrier_delay_min + f.weather_delay_min + f.nas_delay_min
            + f.security_delay_min + f.late_aircraft_delay_min), 0) AS controllable_delay_share
FROM dbo.fact_flight_leg f
JOIN dbo.dim_date d ON d.date_key = f.date_key
JOIN dbo.dim_carrier c ON c.carrier_key = f.marketing_carrier_key
WHERE f.origin_airport_key = @station
  AND (@aa_family_only = 0 OR c.is_aa_family = 1)
GROUP BY d.year, d.month_name, f.origin_airport_key
ORDER BY d.year, MIN(d.month);
