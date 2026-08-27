-- ============================================================================
-- smart-commute-assistant — portfolio metrics collection
--
-- Run against the Render PostgreSQL instance.
--   psql "$DATABASE_URL" -f collect_metrics.sql > metrics_raw.txt
--
-- Record the run date. Every downstream document cites it as the snapshot date.
-- Each query is numbered to match PORTFOLIO_TODO.md Phase 0.1 and will become an
-- entry in docs/metrics.md showing the derivation of a README figure.
-- ============================================================================

\echo '=============================================================='
\echo 'SNAPSHOT'
\echo '=============================================================='
SELECT now() AS snapshot_taken_at, current_database() AS database;


-- ============================================================================
-- Q1  Table row counts — is there enough data to report anything?
-- ============================================================================
\echo ''
\echo '--- Q1  Row counts ---'
SELECT 'users'                AS table_name, COUNT(*) FROM users
UNION ALL SELECT 'households',              COUNT(*) FROM households
UNION ALL SELECT 'commute_profiles',        COUNT(*) FROM commute_profiles
UNION ALL SELECT 'commute_schedules',       COUNT(*) FROM commute_schedules
UNION ALL SELECT 'commute_overrides',       COUNT(*) FROM commute_overrides
UNION ALL SELECT 'commute_logs',            COUNT(*) FROM commute_logs
UNION ALL SELECT 'api_health_logs',         COUNT(*) FROM api_health_logs
UNION ALL SELECT 'commute_destinations',    COUNT(*) FROM commute_destinations
UNION ALL SELECT 'family_groups',           COUNT(*) FROM family_groups
UNION ALL SELECT 'family_members',          COUNT(*) FROM family_members
ORDER BY 1;


-- ============================================================================
-- Q2  commute_logs column fill rate  *** DECISION GATE ***
--
-- If actual_arrival_time and is_late are near-zero, the README gets an
-- "Evaluation Protocol — results pending" section instead of an Evaluation
-- section. Do not proceed to writing metrics.md before reading this result.
-- ============================================================================
\echo ''
\echo '--- Q2  commute_logs fill rate (DECISION GATE) ---'
SELECT
    COUNT(*)                              AS total_rows,
    COUNT(day_of_week)                    AS day_of_week,
    COUNT(is_holiday)                     AS is_holiday,
    COUNT(target_arrival_time)            AS target_arrival_time,
    COUNT(suggested_departure_time)       AS suggested_departure_time,
    COUNT(actual_departure_time)          AS actual_departure_time,
    COUNT(suggested_transport)            AS suggested_transport,
    COUNT(actual_transport)               AS actual_transport,
    COUNT(weather_condition)              AS weather_condition,
    COUNT(rain_prob)                      AS rain_prob,
    COUNT(temp)                           AS temp,
    COUNT(gmaps_traffic_duration)         AS gmaps_traffic_duration,
    COUNT(tdx_bus_eta)                    AS tdx_bus_eta,
    COUNT(actual_arrival_time)            AS actual_arrival_time,
    COUNT(is_late)                        AS is_late
FROM commute_logs;

-- Same thing as percentages, easier to read in the doc.
SELECT
    ROUND(100.0 * COUNT(suggested_departure_time) / NULLIF(COUNT(*), 0), 1) AS pct_suggested_departure,
    ROUND(100.0 * COUNT(actual_departure_time)    / NULLIF(COUNT(*), 0), 1) AS pct_actual_departure,
    ROUND(100.0 * COUNT(suggested_transport)      / NULLIF(COUNT(*), 0), 1) AS pct_suggested_transport,
    ROUND(100.0 * COUNT(actual_transport)         / NULLIF(COUNT(*), 0), 1) AS pct_actual_transport,
    ROUND(100.0 * COUNT(actual_arrival_time)      / NULLIF(COUNT(*), 0), 1) AS pct_actual_arrival,
    ROUND(100.0 * COUNT(is_late)                  / NULLIF(COUNT(*), 0), 1) AS pct_is_late
FROM commute_logs;


-- ============================================================================
-- Q3  Observation window
-- ============================================================================
\echo ''
\echo '--- Q3  Observation window ---'
SELECT
    MIN(date)                AS first_date,
    MAX(date)                AS last_date,
    COUNT(DISTINCT date)     AS days_with_activity,
    MAX(date) - MIN(date)    AS span_days
FROM commute_logs;

-- Rows per day, to see whether coverage is continuous or bursty.
SELECT date, COUNT(*) AS rows
FROM commute_logs
GROUP BY date
ORDER BY date;


-- ============================================================================
-- Q4  Label distribution — the supervised-dataset claim
-- ============================================================================
\echo ''
\echo '--- Q4  is_late distribution ---'
SELECT
    COALESCE(is_late::text, 'NULL') AS is_late,
    COUNT(*)                        AS rows,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS pct
FROM commute_logs
GROUP BY 1
ORDER BY 2 DESC;

-- Class balance among labelled rows only.
SELECT is_late, COUNT(*)
FROM commute_logs
WHERE is_late IS NOT NULL
GROUP BY 1;

-- By weekday, to check whether the feature carries any signal at all.
SELECT day_of_week,
       COUNT(*)                                   AS rows,
       COUNT(*) FILTER (WHERE is_late)            AS late,
       COUNT(*) FILTER (WHERE is_late IS FALSE)   AS on_time
FROM commute_logs
WHERE is_late IS NOT NULL
GROUP BY 1
ORDER BY 1;


-- ============================================================================
-- Q5  Policy adherence — did the user follow the suggested mode?
-- ============================================================================
\echo ''
\echo '--- Q5  suggested vs actual transport ---'
SELECT
    suggested_transport,
    actual_transport,
    COUNT(*) AS rows
FROM commute_logs
WHERE suggested_transport IS NOT NULL
  AND actual_transport    IS NOT NULL
GROUP BY 1, 2
ORDER BY 3 DESC;

-- Single headline number.
SELECT
    COUNT(*)                                                       AS comparable_rows,
    COUNT(*) FILTER (WHERE suggested_transport = actual_transport) AS agreed,
    ROUND(100.0 * COUNT(*) FILTER (WHERE suggested_transport = actual_transport)
          / NULLIF(COUNT(*), 0), 1)                                AS agreement_pct
FROM commute_logs
WHERE suggested_transport IS NOT NULL
  AND actual_transport    IS NOT NULL;


-- ============================================================================
-- Q6  Departure delta — how far off the suggestion was, in minutes
--
-- Times are stored as VARCHAR 'HH:MM'. The regex guard skips malformed rows;
-- report how many were skipped rather than silently dropping them.
-- ============================================================================
\echo ''
\echo '--- Q6  Departure delta ---'
WITH parsed AS (
    SELECT
        date,
        suggested_departure_time,
        actual_departure_time,
        EXTRACT(EPOCH FROM (
            actual_departure_time::time - suggested_departure_time::time
        )) / 60 AS delta_min
    FROM commute_logs
    WHERE suggested_departure_time ~ '^[0-9]{1,2}:[0-9]{2}$'
      AND actual_departure_time    ~ '^[0-9]{1,2}:[0-9]{2}$'
)
SELECT
    COUNT(*)                                                      AS n,
    ROUND(AVG(delta_min)::numeric, 1)                             AS mean_delta_min,
    ROUND(AVG(ABS(delta_min))::numeric, 1)                        AS mean_abs_delta_min,
    PERCENTILE_CONT(0.5)  WITHIN GROUP (ORDER BY delta_min)       AS median_delta_min,
    MIN(delta_min)                                                AS min_delta_min,
    MAX(delta_min)                                                AS max_delta_min,
    COUNT(*) FILTER (WHERE delta_min > 0)                         AS departed_late,
    COUNT(*) FILTER (WHERE delta_min <= 0)                        AS departed_on_time_or_early
FROM parsed;

-- Rows excluded by the regex guard — report this, do not hide it.
SELECT COUNT(*) AS malformed_or_null_time_rows
FROM commute_logs
WHERE NOT (suggested_departure_time ~ '^[0-9]{1,2}:[0-9]{2}$'
       AND actual_departure_time    ~ '^[0-9]{1,2}:[0-9]{2}$')
   OR suggested_departure_time IS NULL
   OR actual_departure_time    IS NULL;


-- ============================================================================
-- Q7  api_health_logs per endpoint — the measurement backbone
--
-- 6 instrumented endpoints, 20 call sites:
--   google.geocode  google.routes.transit  google.routes.walk
--   tdx.bus.auth    tdx.metro.auth         cwa.weather.city
-- ============================================================================
\echo ''
\echo '--- Q7  External API reliability by endpoint ---'
SELECT
    endpoint,
    COUNT(*)                                                          AS calls,
    COUNT(*) FILTER (WHERE error_message IS NOT NULL)                 AS transport_errors,
    COUNT(*) FILTER (WHERE status_code >= 400)                        AS http_errors,
    COUNT(*) FILTER (WHERE error_message IS NOT NULL
                        OR status_code >= 400)                        AS total_failures,
    ROUND(100.0 * COUNT(*) FILTER (WHERE error_message IS NOT NULL
                                      OR status_code >= 400)
          / NULLIF(COUNT(*), 0), 2)                                   AS failure_pct,
    ROUND(AVG(latency_ms))                                            AS mean_latency_ms,
    PERCENTILE_CONT(0.5)  WITHIN GROUP (ORDER BY latency_ms)          AS p50_ms,
    PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY latency_ms)          AS p95_ms,
    MAX(latency_ms)                                                   AS max_ms,
    MIN(timestamp)                                                    AS first_seen,
    MAX(timestamp)                                                    AS last_seen
FROM api_health_logs
GROUP BY endpoint
ORDER BY calls DESC;

-- Overall headline failure rate — the AMR_System "12.47%" analogue.
SELECT
    COUNT(*)                                                    AS total_calls,
    COUNT(*) FILTER (WHERE error_message IS NOT NULL
                        OR status_code >= 400)                  AS total_failures,
    ROUND(100.0 * COUNT(*) FILTER (WHERE error_message IS NOT NULL
                                      OR status_code >= 400)
          / NULLIF(COUNT(*), 0), 2)                             AS failure_pct
FROM api_health_logs;


-- ============================================================================
-- Q8  Failure clustering — bursty or uniform?
-- ============================================================================
\echo ''
\echo '--- Q8  Failures by day ---'
SELECT
    DATE(timestamp) AS day,
    COUNT(*)        AS failures,
    COUNT(DISTINCT endpoint) AS endpoints_affected
FROM api_health_logs
WHERE error_message IS NOT NULL OR status_code >= 400
GROUP BY 1
ORDER BY 2 DESC;

-- Same, split by endpoint, top 30.
SELECT
    DATE(timestamp) AS day,
    endpoint,
    COUNT(*) AS failures
FROM api_health_logs
WHERE error_message IS NOT NULL OR status_code >= 400
GROUP BY 1, 2
ORDER BY 3 DESC
LIMIT 30;

-- Daily call volume, so the failure counts have a denominator.
SELECT
    DATE(timestamp) AS day,
    COUNT(*)        AS calls,
    COUNT(*) FILTER (WHERE error_message IS NOT NULL
                        OR status_code >= 400) AS failures
FROM api_health_logs
GROUP BY 1
ORDER BY 1;


-- ============================================================================
-- Q9  Error taxonomy — named failure modes for known-issues.md
-- ============================================================================
\echo ''
\echo '--- Q9  Error messages ---'
SELECT
    endpoint,
    LEFT(error_message, 100) AS error,
    COUNT(*)                 AS occurrences,
    MIN(timestamp)           AS first_seen,
    MAX(timestamp)           AS last_seen
FROM api_health_logs
WHERE error_message IS NOT NULL
GROUP BY 1, 2
ORDER BY 3 DESC
LIMIT 40;

-- HTTP status code distribution.
SELECT
    endpoint,
    status_code,
    COUNT(*) AS occurrences
FROM api_health_logs
WHERE status_code IS NOT NULL
GROUP BY 1, 2
ORDER BY 1, 3 DESC;


-- ============================================================================
-- Q10  Parallel route call latency — substantiates the 3-way comparison claim
--
-- Three Google Directions calls with distinct allowed_travel_modes run
-- concurrently. If they are truly parallel, wall-clock cost should track the
-- slowest of the three rather than their sum.
-- ============================================================================
\echo ''
\echo '--- Q10  Route call latency ---'
SELECT
    endpoint,
    COUNT(*)                                                 AS calls,
    ROUND(AVG(latency_ms))                                   AS mean_ms,
    PERCENTILE_CONT(0.5)  WITHIN GROUP (ORDER BY latency_ms) AS p50_ms,
    PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95_ms,
    MAX(latency_ms)                                          AS max_ms
FROM api_health_logs
WHERE endpoint LIKE 'google.routes%'
GROUP BY endpoint
ORDER BY endpoint;

-- Calls grouped into 5-second buckets, to see which ones fired together.
-- If the three modes appear in the same bucket, the fan-out is concurrent.
SELECT
    to_timestamp(FLOOR(EXTRACT(EPOCH FROM timestamp) / 5) * 5) AS bucket,
    STRING_AGG(DISTINCT endpoint, ', ' ORDER BY endpoint)      AS endpoints,
    COUNT(*)                                                   AS calls
FROM api_health_logs
WHERE endpoint LIKE 'google.routes%'
GROUP BY 1
HAVING COUNT(*) > 1
ORDER BY 1 DESC
LIMIT 25;


-- ============================================================================
-- Q11  Feature exercise — was the scheduling machinery actually used?
-- ============================================================================
\echo ''
\echo '--- Q11  Schedules and overrides ---'
SELECT
    COUNT(*)                                    AS total_schedules,
    COUNT(*) FILTER (WHERE is_active)           AS active,
    COUNT(*) FILTER (WHERE reminder_enabled)    AS reminder_enabled,
    COUNT(DISTINCT user_id)                     AS distinct_users
FROM commute_schedules;

SELECT
    COUNT(*)                                        AS total_overrides,
    COUNT(*) FILTER (WHERE commute_disabled)        AS disabled_days,
    COUNT(*) FILTER (WHERE commute_enabled)         AS enabled_days,
    COUNT(*) FILTER (WHERE transport_mode_override IS NOT NULL) AS mode_overrides,
    COUNT(*) FILTER (WHERE alert_status = 'acknowledged')        AS acknowledged,
    COUNT(*) FILTER (WHERE alert_status = 'pending')             AS pending,
    MIN(target_date)                                AS first_date,
    MAX(target_date)                                AS last_date
FROM commute_overrides;

-- Weekday coverage across active schedules.
SELECT days, COUNT(*) AS schedules
FROM commute_schedules
WHERE is_active
GROUP BY days
ORDER BY 2 DESC;


-- ============================================================================
-- Q12  Geocoding failure history — the silent-failure incident
--
-- Establishes the date range during which Google Geocoding was returning
-- errors after the free trial expired. Feeds known-issues.md entry A-1.
-- ============================================================================
\echo ''
\echo '--- Q12  Geocoding failures over time ---'
SELECT
    DATE(timestamp)          AS day,
    COUNT(*)                 AS calls,
    COUNT(*) FILTER (WHERE error_message IS NOT NULL
                        OR status_code >= 400) AS failures,
    STRING_AGG(DISTINCT LEFT(error_message, 60), ' | ') AS sample_errors
FROM api_health_logs
WHERE endpoint = 'google.geocode'
GROUP BY 1
ORDER BY 1;


-- ============================================================================
-- Q13  Sanity checks — flag anything that would make a claim unsafe
-- ============================================================================
\echo ''
\echo '--- Q13  Sanity checks ---'

-- Duplicate log rows for the same user and date.
SELECT user_id, date, COUNT(*) AS rows
FROM commute_logs
GROUP BY 1, 2
HAVING COUNT(*) > 1
ORDER BY 3 DESC
LIMIT 20;

-- Orphaned commute_logs rows (no matching user).
SELECT COUNT(*) AS orphaned_commute_logs
FROM commute_logs cl
LEFT JOIN users u ON u.id = cl.user_id
WHERE u.id IS NULL;

-- Orphaned schedules.
SELECT COUNT(*) AS orphaned_schedules
FROM commute_schedules cs
LEFT JOIN users u ON u.id = cs.user_id
WHERE u.id IS NULL;

-- Rows where actual_arrival_time is set but is_late is not — an inconsistency
-- that would undermine the labelled-dataset claim.
SELECT COUNT(*) AS arrival_without_label
FROM commute_logs
WHERE actual_arrival_time IS NOT NULL AND is_late IS NULL;

-- Timezone basis check: does created_at agree with date?
SELECT
    COUNT(*) FILTER (WHERE DATE(created_at) <> date) AS created_at_date_mismatch,
    COUNT(*)                                         AS total
FROM commute_logs;

\echo ''
\echo '=============================================================='
\echo 'DONE — record the snapshot timestamp printed at the top.'
\echo '=============================================================='
