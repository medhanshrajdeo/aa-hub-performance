-- "What are the rebank's effect sizes, with CIs?"
-- fact_rebank_effect / fact_rebank_event_study are written by
-- analysis/dfw_rebank_diffindiff.py, not computed here -- this just
-- reads the modeled result. See README.md's Finding #2 for the
-- plain-English readout.

SELECT hub, metric, model, estimate, se, ci_low, ci_high, p_value, n, controls
FROM dbo.fact_rebank_effect
ORDER BY metric;

-- Event-study coefficients by week relative to the detected rebank week
-- (2026-W15) -- use this to eyeball the pre-trend check yourself rather
-- than trust the README's summary of it.
SELECT week_relative, coef, ci_low, ci_high
FROM dbo.fact_rebank_event_study
ORDER BY week_relative;
