-- "At what scheduled turn length does <fleet>'s D0 fall off at <station>?"
-- Same pairing/conditioning logic as signature analysis #3
-- (analysis/dfw_turn_cliff.py): on-time-inbound turns only, so the
-- result isolates the station's own effect from inbound delay.

DECLARE @station VARCHAR(3) = 'DFW';
DECLARE @fleet_family VARCHAR(50) = '737-823';
DECLARE @bucket_min INT = 5;  -- scheduled-turn-length bucket width

SELECT
    (CAST(t.scheduled_turn_min / @bucket_min AS INT)) * @bucket_min AS turn_min_bucket,
    COUNT(*) AS turns,
    CAST(SUM(CASE WHEN f.is_d0 = 1 THEN 1 ELSE 0 END) AS FLOAT)
        / NULLIF(COUNT(*), 0) AS outbound_d0_pct
FROM dbo.fact_aircraft_turn t
JOIN dbo.fact_flight_leg f
    ON f.tail_number = t.tail_number
    AND f.origin_airport_key = t.station
    AND f.sched_dep_utc = t.outbound_dep_sched_utc
WHERE t.station = @station
  AND t.fleet_family = @fleet_family
  AND t.inbound_arr_delay_min <= 0          -- on-time (or early) inbound only
  AND t.scheduled_turn_min IS NOT NULL
GROUP BY (CAST(t.scheduled_turn_min / @bucket_min AS INT)) * @bucket_min
ORDER BY turn_min_bucket;
