#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
smart-commute-assistant — metrics collection without psql.

Runs every query in scripts/collect_metrics.sql against the production database
and prints an aggregate report. Output contains counts, rates and percentiles
only — no individual commute rows, no addresses, no coordinates — so it is safe
to paste back for filling in docs/metrics.md and the README.

Usage (Windows cmd, from the repo root):

    set DATABASE_URL=<External Database URL from Render>
    python scripts\\run_metrics.py > metrics_raw.txt

Then open metrics_raw.txt. If a driver is missing the script says which one to
install.
"""

from __future__ import annotations

import os
import sys
from datetime import date, datetime
from decimal import Decimal

try:
    from sqlalchemy import create_engine, text
except ImportError:
    print("Missing SQLAlchemy.  pip install sqlalchemy", file=sys.stderr)
    raise SystemExit(1)


def normalise(url: str) -> str:
    """Render hands out postgres://; SQLAlchemy needs postgresql:// plus a driver."""
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    if url.startswith("postgresql://"):
        for driver, module in (("psycopg2", "psycopg2"), ("pg8000", "pg8000")):
            try:
                __import__(module)
                return url.replace("postgresql://", f"postgresql+{driver}://", 1)
            except ImportError:
                continue
        print("No PostgreSQL driver found.\n"
              "  pip install psycopg2-binary\n"
              "  ...or, if that fails to compile:  pip install pg8000",
              file=sys.stderr)
        raise SystemExit(1)
    return url


def fmt(v):
    if v is None:
        return "NULL"
    if isinstance(v, (datetime, date)):
        return v.isoformat(sep=" ", timespec="seconds") if isinstance(v, datetime) else v.isoformat()
    if isinstance(v, Decimal):
        return f"{float(v):g}"
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


def run(conn, label, sql, note=""):
    print(f"\n--- {label} ---")
    if note:
        print(f"    ({note})")
    try:
        res = conn.execute(text(sql))
    except Exception as exc:                                   # noqa: BLE001
        print(f"    QUERY FAILED: {type(exc).__name__}: {str(exc)[:200]}")
        return
    cols = list(res.keys())
    rows = res.fetchall()
    if not rows:
        print("    (no rows)")
        return
    widths = [max(len(c), *(len(fmt(r[i])) for r in rows)) for i, c in enumerate(cols)]
    print("    " + " | ".join(c.ljust(widths[i]) for i, c in enumerate(cols)))
    print("    " + "-+-".join("-" * w for w in widths))
    for r in rows[:60]:
        print("    " + " | ".join(fmt(r[i]).ljust(widths[i]) for i in range(len(cols))))
    if len(rows) > 60:
        print(f"    ... {len(rows) - 60} more rows omitted")


QUERIES = [
    ("Q1  Row counts", """
        SELECT 'commute_logs' AS table_name, COUNT(*) AS rows FROM commute_logs
        UNION ALL SELECT 'api_health_logs',   COUNT(*) FROM api_health_logs
        UNION ALL SELECT 'commute_overrides', COUNT(*) FROM commute_overrides
        UNION ALL SELECT 'commute_schedules', COUNT(*) FROM commute_schedules
        UNION ALL SELECT 'commute_profiles',  COUNT(*) FROM commute_profiles
        UNION ALL SELECT 'users',             COUNT(*) FROM users
        UNION ALL SELECT 'households',        COUNT(*) FROM households
        UNION ALL SELECT 'family_groups',     COUNT(*) FROM family_groups
        UNION ALL SELECT 'family_members',    COUNT(*) FROM family_members
        UNION ALL SELECT 'commute_destinations', COUNT(*) FROM commute_destinations
        ORDER BY 1
    """, "feeds metrics.md Sources table"),

    ("Q2  commute_logs fill rate  *** DECISION GATE ***", """
        SELECT
            COUNT(*)                        AS total_rows,
            COUNT(suggested_departure_time) AS suggested_departure,
            COUNT(suggested_transport)      AS suggested_transport,
            COUNT(actual_departure_time)    AS actual_departure,
            COUNT(actual_transport)         AS actual_transport,
            COUNT(actual_arrival_time)      AS actual_arrival,
            COUNT(is_late)                  AS is_late,
            COUNT(rain_prob)                AS rain_prob,
            COUNT(gmaps_traffic_duration)   AS gmaps_duration,
            COUNT(tdx_bus_eta)              AS tdx_bus_eta
        FROM commute_logs
    """, "decides whether the README carries an Evaluation section"),

    ("Q3  Observation window", """
        SELECT MIN(date) AS first_date, MAX(date) AS last_date,
               COUNT(DISTINCT date) AS active_days,
               (MAX(date) - MIN(date)) AS span_days
        FROM commute_logs
    """, ""),

    ("Q4  is_late distribution", """
        SELECT COALESCE(is_late::text,'NULL') AS is_late, COUNT(*) AS rows
        FROM commute_logs GROUP BY 1 ORDER BY 2 DESC
    """, ""),

    ("Q5  Mode agreement", """
        SELECT COUNT(*) AS comparable,
               COUNT(*) FILTER (WHERE suggested_transport = actual_transport) AS agreed
        FROM commute_logs
        WHERE suggested_transport IS NOT NULL AND actual_transport IS NOT NULL
    """, ""),

    ("Q5b Mode pairs", """
        SELECT suggested_transport, actual_transport, COUNT(*) AS rows
        FROM commute_logs
        WHERE suggested_transport IS NOT NULL AND actual_transport IS NOT NULL
        GROUP BY 1,2 ORDER BY 3 DESC
    """, ""),

    ("Q6  Departure delta (aggregate only)", """
        WITH parsed AS (
            SELECT EXTRACT(EPOCH FROM (actual_departure_time::time
                                     - suggested_departure_time::time))/60 AS delta_min
            FROM commute_logs
            WHERE suggested_departure_time ~ '^[0-9]{1,2}:[0-9]{2}$'
              AND actual_departure_time    ~ '^[0-9]{1,2}:[0-9]{2}$'
        )
        SELECT COUNT(*) AS n,
               ROUND(AVG(delta_min)::numeric,1)                        AS mean_min,
               ROUND(AVG(ABS(delta_min))::numeric,1)                   AS mean_abs_min,
               PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY delta_min)  AS median_min,
               MIN(delta_min) AS min_min, MAX(delta_min) AS max_min
        FROM parsed
    """, "per-row values deliberately not printed"),

    ("Q6b Rows the regex guard excluded", """
        SELECT COUNT(*) AS excluded FROM commute_logs
        WHERE suggested_departure_time IS NULL OR actual_departure_time IS NULL
           OR NOT (suggested_departure_time ~ '^[0-9]{1,2}:[0-9]{2}$'
               AND actual_departure_time    ~ '^[0-9]{1,2}:[0-9]{2}$')
    """, "reported rather than dropped silently"),

    ("Q7  Provider reliability by endpoint", """
        SELECT endpoint,
               COUNT(*) AS calls,
               COUNT(*) FILTER (WHERE error_message IS NOT NULL OR status_code >= 400) AS failures,
               ROUND(100.0 * COUNT(*) FILTER (WHERE error_message IS NOT NULL
                                                 OR status_code >= 400)
                     / NULLIF(COUNT(*),0), 2)                             AS failure_pct,
               ROUND(AVG(latency_ms))                                     AS mean_ms,
               PERCENTILE_CONT(0.5)  WITHIN GROUP (ORDER BY latency_ms)   AS p50_ms,
               PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY latency_ms)   AS p95_ms,
               MIN(timestamp) AS first_seen, MAX(timestamp) AS last_seen
        FROM api_health_logs GROUP BY endpoint ORDER BY calls DESC
    """, "the measurement backbone"),

    ("Q7b Overall failure rate", """
        SELECT COUNT(*) AS total_calls,
               COUNT(*) FILTER (WHERE error_message IS NOT NULL OR status_code >= 400) AS failures,
               ROUND(100.0 * COUNT(*) FILTER (WHERE error_message IS NOT NULL
                                                 OR status_code >= 400)
                     / NULLIF(COUNT(*),0), 2) AS failure_pct
        FROM api_health_logs
    """, "quote as an upper bound, not a rate"),

    ("Q8  Calls and failures by day", """
        SELECT DATE(timestamp) AS day, COUNT(*) AS calls,
               COUNT(*) FILTER (WHERE error_message IS NOT NULL OR status_code >= 400) AS failures
        FROM api_health_logs GROUP BY 1 ORDER BY 1
    """, ""),

    ("Q9  Error taxonomy", """
        SELECT endpoint, LEFT(error_message, 70) AS error, COUNT(*) AS n,
               MIN(timestamp) AS first_seen, MAX(timestamp) AS last_seen
        FROM api_health_logs WHERE error_message IS NOT NULL
        GROUP BY 1,2 ORDER BY 3 DESC
    """, ""),

    ("Q9b HTTP status distribution", """
        SELECT endpoint, status_code, COUNT(*) AS n
        FROM api_health_logs WHERE status_code IS NOT NULL
        GROUP BY 1,2 ORDER BY 1,3 DESC
    """, ""),

    ("Q10 Route call latency", """
        SELECT endpoint, COUNT(*) AS calls, ROUND(AVG(latency_ms)) AS mean_ms,
               PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95_ms,
               MAX(latency_ms) AS max_ms
        FROM api_health_logs WHERE endpoint LIKE 'google.routes%'
        GROUP BY 1 ORDER BY 1
    """, ""),

    ("Q11 Schedules and overrides", """
        SELECT (SELECT COUNT(*) FROM commute_schedules)                        AS schedules,
               (SELECT COUNT(*) FROM commute_schedules WHERE is_active)         AS active,
               (SELECT COUNT(*) FROM commute_overrides)                         AS overrides,
               (SELECT COUNT(*) FROM commute_overrides WHERE departed_at IS NOT NULL) AS confirmed_departures,
               (SELECT COUNT(*) FROM commute_overrides WHERE departure_question_sent_at IS NOT NULL) AS questions_sent,
               (SELECT COUNT(*) FROM commute_overrides WHERE monitor_one_hour_sent_at IS NOT NULL) AS one_hour_sent,
               (SELECT COUNT(*) FROM commute_overrides WHERE monitor_five_min_sent_at IS NOT NULL) AS five_min_sent
    """, "confirmation rate = confirmed_departures / questions_sent"),

    ("Q12 Geocoding failures over time", """
        SELECT DATE(timestamp) AS day, COUNT(*) AS calls,
               COUNT(*) FILTER (WHERE error_message IS NOT NULL OR status_code >= 400) AS failures
        FROM api_health_logs WHERE endpoint = 'google.geocode'
        GROUP BY 1 ORDER BY 1
    """, "the A-1 outage should show as a contiguous block"),

    ("Q13a Duplicate commute_overrides", """
        SELECT COUNT(*) AS duplicate_groups FROM (
            SELECT user_id, schedule_id, target_date
            FROM commute_overrides GROUP BY 1,2,3 HAVING COUNT(*) > 1
        ) d
    """, "known-issues B-2"),

    ("Q13b Duplicate commute_logs", """
        SELECT COUNT(*) AS duplicate_groups FROM (
            SELECT user_id, date FROM commute_logs GROUP BY 1,2 HAVING COUNT(*) > 1
        ) d
    """, "known-issues B-2"),

    ("Q13c created_at vs date mismatch", """
        SELECT COUNT(*) FILTER (WHERE DATE(created_at) <> date) AS mismatched,
               COUNT(*) AS total
        FROM commute_logs
    """, "known-issues B-3"),

    ("Q13d Arrival recorded without a label", """
        SELECT COUNT(*) AS arrival_without_label FROM commute_logs
        WHERE actual_arrival_time IS NOT NULL AND is_late IS NULL
    """, ""),
]


def main() -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("Set DATABASE_URL first.\n"
              "  Render dashboard > your PostgreSQL > Connections > External Database URL\n"
              "  (the Internal URL only works from inside Render)", file=sys.stderr)
        return 1

    engine = create_engine(normalise(url))
    snapshot = datetime.now().isoformat(sep=" ", timespec="seconds")

    print("=" * 74)
    print("smart-commute-assistant — metrics snapshot")
    print(f"snapshot taken at: {snapshot}")
    print("=" * 74)
    print("\nAggregate figures only. No individual commute rows, addresses or")
    print("coordinates appear below.")

    with engine.connect() as conn:
        for label, sql, note in QUERIES:
            run(conn, label, sql, note)

    print("\n" + "=" * 74)
    print("Done. Record the snapshot timestamp above — every document cites it.")
    print("Read Q2 first: it decides whether an Evaluation section is possible.")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
