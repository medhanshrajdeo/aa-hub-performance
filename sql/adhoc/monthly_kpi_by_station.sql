-- "What's D0/A14/CF for <station> over the last N months?"
-- Matches docs/kpi_spec.md exactly: cancelled legs excluded from D0/A14
-- numerator and denominator; CF treats diverted as completed.

DECLARE @station VARCHAR(3) = 'DFW';
DECLARE @months_back INT = 12;

SELECT
    d.year,
    d.month,
    d.month_name,
    COUNT(*) AS scheduled_legs,
    SUM(CASE WHEN f.is_cancelled = 0 THEN 1 ELSE 0 END) AS operated_legs,
    CAST(SUM(CASE WHEN f.is_d0 = 1 THEN 1 ELSE 0 END) AS FLOAT)
        / NULLIF(SUM(CASE WHEN f.is_cancelled = 0 THEN 1 ELSE 0 END), 0) AS d0_pct,
    CAST(SUM(CASE WHEN f.is_a14 = 1 THEN 1 ELSE 0 END) AS FLOAT)
        / NULLIF(SUM(CASE WHEN f.is_cancelled = 0 THEN 1 ELSE 0 END), 0) AS a14_pct,
    1.0 - CAST(SUM(CASE WHEN f.is_cancelled = 1 THEN 1 ELSE 0 END) AS FLOAT)
        / NULLIF(COUNT(*), 0) AS cf_pct,
    AVG(CASE WHEN f.is_cancelled = 0 THEN f.taxi_out_min END) AS mean_taxi_out_min
FROM dbo.fact_flight_leg f
JOIN dbo.dim_date d ON d.date_key = f.date_key
WHERE f.origin_airport_key = @station
  AND d.full_date >= DATEADD(MONTH, -@months_back, CAST(GETUTCDATE() AS DATE))
GROUP BY d.year, d.month, d.month_name
ORDER BY d.year, d.month;
