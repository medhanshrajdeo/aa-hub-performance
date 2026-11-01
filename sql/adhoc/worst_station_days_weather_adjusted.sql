-- "Which station-days most underperformed their weather/volume-adjusted
-- expected D0?" -- reads the gold table signature analysis #4 writes
-- (fact_station_day_scorecard), not a live recompute of the OLS model.
-- See analysis/dfw_weather_scorecard.py for how expected_d0_rate is
-- modeled (weather severity + log(scheduled volume) + station/month FE).

DECLARE @top_n INT = 20;

SELECT TOP (@top_n)
    s.station,
    d.full_date,
    s.d0_rate,
    s.expected_d0_rate,
    s.d0_vs_expected_pp,
    s.max_severity_score,
    s.scheduled_departures
FROM dbo.fact_station_day_scorecard s
JOIN dbo.dim_date d ON d.date_key = s.date_key
ORDER BY s.d0_vs_expected_pp ASC;  -- most negative (worst underperformance) first
