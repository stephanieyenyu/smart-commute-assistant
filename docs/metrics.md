# Metrics

Every figure quoted in the README is derived here, with the query or command that produced it, so
it can be checked rather than accepted. Figures that have not been collected yet are marked
`PENDING` alongside the query that will fill them; none of them is estimated or carried over from
elsewhere.

**Snapshot date** `PENDING` — set when `scripts/collect_metrics.sql` is first run against production
**Observation window** `PENDING`
**Raw export** `PENDING` — [`data/commute_logs.json`](../data/commute_logs.json) and
[`data/api_health_logs.json`](../data/api_health_logs.json), produced by
`scripts/export_data_scrubbed.py` with home addresses, coordinates and LINE User IDs removed

The deployment is a single user — me — on one origin–destination pair in Taipei. Nothing in this
document generalises beyond that, and the [Interpretation](#interpretation) section says so per
figure rather than once at the end.

---

## Sources

| Table | Rows | What it holds |
|---|---|---|
| `commute_logs` | `PENDING` | One row per commute: features at decision time, the action chosen, the outcome |
| `api_health_logs` | `PENDING` | Append-only record of every outbound provider call |
| `commute_overrides` | `PENDING` | Per-day state, including the five delivery guard timestamps |
| `commute_schedules` | `PENDING` | Recurring commutes |

```sql
SELECT 'commute_logs' AS t, COUNT(*) FROM commute_logs
UNION ALL SELECT 'api_health_logs', COUNT(*) FROM api_health_logs
UNION ALL SELECT 'commute_overrides', COUNT(*) FROM commute_overrides
UNION ALL SELECT 'commute_schedules', COUNT(*) FROM commute_schedules;
```

---

## Scale

Collected from the repository, not the database. Regenerate with
`python scripts/collect_repo_stats.py --markdown`.

| Figure | Value | Derivation |
|---|---|---|
| Commits | 187 | `git rev-list --count HEAD` |
| Development window | 2026-04-22 → 2026-07-30 | `git log --format=%ad --date=short` |
| Source files | 59 | `git ls-files "*.py" "*.js" "*.html" \| wc -l` |
| Python LOC | 13,766 | `git ls-files "*.py" \| xargs wc -l` |
| Test LOC | 1,451 across 4 files | `wc -l tests/test_*.py` |
| HTTP routes declared | 37 distinct paths | Route inventory, `docs/api.md` |
| HTTP routes mounted | 34 + 3 WebSocket | Declared minus the 10 unmounted, `docs/api.md` |
| Database tables | 10 | `grep -c "__tablename__" backend/app/models.py` |
| Alembic revisions | 15 | `ls backend/alembic/versions/*.py \| wc -l` |
| Scheduled jobs | 2 | `scheduler.add_job` registrations |
| Instrumented provider endpoints | 6 | Distinct first arguments to `log_api_health()` |
| `log_api_health` call sites | 17 | `grep -rc "log_api_health(" backend/app` |
| LINE command keys | 25 | `COMMAND_ALIASES` keys in `webhook.py` |
| LINE postback actions | 3 | `action=` literals |

**Declared and mounted differ by ten routes.** Nine belong to `dashboard.py` and one to
`liff_routes.py:api_router`, neither of which is included in the app. Quoting 37 without that
distinction would overstate the reachable surface. See
[`api.md`](api.md#unreachable-routes).

**Two scheduled jobs, not three.** The three reminder stages are branches inside one 30-second
tick. Counting them as three jobs would be wrong.

---

## Dataset fill rate

**This is the first figure to read, and it gates everything below it.** Row count is not the
relevant number: a `commute_logs` row exists for every commute the engine planned, but the outcome
columns are only populated when the user tapped 「已出門」.

```sql
SELECT
    COUNT(*)                          AS total_rows,
    COUNT(suggested_departure_time)   AS has_action,
    COUNT(actual_departure_time)      AS has_actual_departure,
    COUNT(actual_arrival_time)        AS has_actual_arrival,
    COUNT(is_late)                    AS has_label
FROM commute_logs;
```

| Column | Populated | Share |
|---|---|---|
| `suggested_departure_time` | `PENDING` | |
| `suggested_transport` | `PENDING` | |
| `actual_departure_time` | `PENDING` | |
| `actual_transport` | `PENDING` | |
| `actual_arrival_time` | `PENDING` | |
| `is_late` | `PENDING` | |

**What a low fill rate would mean.** Not that the dataset is small — that it is *biased*. A missing
row is a morning the user did not tap, and the mornings most likely to go unrecorded are the
rushed ones. The label is missing-not-at-random with respect to the thing being labelled. A model
trained on the populated rows would be trained on the calm mornings.

---

## External provider reliability

The measurement that this project can actually make. Derived entirely from `api_health_logs`,
which exists because every outbound call is routed through one function.

```sql
SELECT endpoint,
       COUNT(*) AS calls,
       COUNT(*) FILTER (WHERE error_message IS NOT NULL OR status_code >= 400) AS failures,
       ROUND(100.0 * COUNT(*) FILTER (WHERE error_message IS NOT NULL
                                         OR status_code >= 400) / COUNT(*), 2) AS failure_pct,
       ROUND(AVG(latency_ms))                                   AS mean_ms,
       PERCENTILE_CONT(0.5)  WITHIN GROUP (ORDER BY latency_ms) AS p50_ms,
       PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95_ms
FROM api_health_logs GROUP BY endpoint ORDER BY calls DESC;
```

| Endpoint | Calls | Failures | Failure % | p50 ms | p95 ms |
|---|---|---|---|---|---|
| `google.routes.transit` | `PENDING` | | | | |
| `google.routes.walk` | `PENDING` | | | | |
| `google.geocode` | `PENDING` | | | | |
| `tdx.bus.auth` | `PENDING` | | | | |
| `tdx.metro.auth` | `PENDING` | | | | |
| `cwa.weather.city` | `PENDING` | | | | |

### The failure rate will be an upper bound

A row counts as a failure when `error_message` is set or `status_code >= 400`. That conflates three
different things:

- The provider genuinely failed
- The provider was slow and **this client's own timeout** fired — `safe_call` raises
  `TimeoutError` at 2.2 to 4.8 seconds depending on the call, which is a policy of this system, not
  a property of the provider
- A quota or authorisation error, which is a billing state rather than an outage

The logging is not granular enough to separate them. Any failure percentage quoted from this table
is therefore a ceiling, and the README must say so wherever it appears.

### Failure clustering

```sql
SELECT DATE(timestamp) AS day, COUNT(*) AS calls,
       COUNT(*) FILTER (WHERE error_message IS NOT NULL OR status_code >= 400) AS failures
FROM api_health_logs GROUP BY 1 ORDER BY 1;
```

`PENDING`. Reported per day with a denominator, because a failure count without a call count is
not interpretable — and because the geocoding outage in
[`known-issues.md` A-1](known-issues.md#a-1geocoding-returned-null-coordinates-and-every-failure-was-swallowed)
is expected to show as a contiguous block rather than as background noise.

---

## Policy adherence

How often the user did what the policy suggested. This is **not** an accuracy result — the policy
has no ground truth to be accurate against — it measures agreement between a suggestion and a
behaviour.

```sql
SELECT COUNT(*) AS comparable,
       COUNT(*) FILTER (WHERE suggested_transport = actual_transport) AS agreed
FROM commute_logs
WHERE suggested_transport IS NOT NULL AND actual_transport IS NOT NULL;
```

| Figure | Value |
|---|---|
| Comparable rows | `PENDING` |
| Agreement | `PENDING` |

**A high agreement rate would be close to meaningless here.** The user and the author are the same
person, and `actual_transport` is self-reported after seeing the suggestion. Anchoring alone would
produce agreement. It is reported because omitting it would be worse, not because it demonstrates
anything.

---

## Departure delta

```sql
WITH parsed AS (
    SELECT EXTRACT(EPOCH FROM (actual_departure_time::time
                             - suggested_departure_time::time)) / 60 AS delta_min
    FROM commute_logs
    WHERE suggested_departure_time ~ '^[0-9]{1,2}:[0-9]{2}$'
      AND actual_departure_time    ~ '^[0-9]{1,2}:[0-9]{2}$'
)
SELECT COUNT(*), ROUND(AVG(delta_min)::numeric, 1),
       PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY delta_min)
FROM parsed;
```

| Figure | Value |
|---|---|
| Rows parsed | `PENDING` |
| Mean delta | `PENDING` |
| Median delta | `PENDING` |
| Rows excluded by the regex guard | `PENDING` |

The regex guard is necessary because these columns are `VARCHAR`, not `TIME`
([`database.md`](database.md#conventions-that-apply-throughout)). **Excluded rows are reported
rather than dropped**, because a large exclusion count would itself be the finding.

---

## Interpretation

### Everything here is n = 1

One user, one city, one origin–destination pair, one set of provider accounts. The provider
reliability figures characterise those accounts over that window, from one client, with this
system's timeouts applied. They are not a statement about Google, TDX or the CWA.

### Provider failure rate is a ceiling, not a rate

Restated here because it is the figure most likely to be quoted out of context. See
[above](#the-failure-rate-will-be-an-upper-bound).

### The scheduler is not measured

`api_health_logs` records provider calls. There is no equivalent record of reminder deliveries —
no table logs "stage two fired at this time for this override." The `*_sent_at` columns show
*that* a stage fired, not whether it fired inside its window, so **the exactly-once property
argued in [`state-machines.md`](state-machines.md#the-invariant) is a code-level argument, not a
measured result.** Nothing in this project verifies it empirically. That is the largest gap
between what the system claims and what it demonstrates.

### No latency figure is quoted for the system itself

Only provider latency is recorded. End-to-end response time — webhook received to LINE reply sent
— is not instrumented anywhere, so no such figure appears in this project.

### Duplicate rows are not yet ruled out

`(user_id, schedule_id, target_date)` on `commute_overrides` is not declared unique; uniqueness is
enforced by `get_or_create_override()` in application code only. Whether duplicates exist is
checked by query Q13 and recorded as
[`known-issues.md` B-2](known-issues.md#b-2duplicate-commute_overrides-and-commute_logs-rows).
Any per-day aggregate below is provisional until that returns empty.

---

## Filling this document

```bash
psql "$DATABASE_URL" -f scripts/collect_metrics.sql > metrics_raw.txt
python scripts/export_data_scrubbed.py && python scripts/export_data_scrubbed.py --verify
python scripts/collect_repo_stats.py --markdown
```

Replace each `PENDING` with the returned value and set the snapshot date at the top. If a query
returns too few rows to support a figure, **write that instead of the figure** — a stated absence
is consistent with the rest of this document in a way that a thin number is not.

---

**Source** `scripts/collect_metrics.sql`, `scripts/collect_repo_stats.py` · repository figures @ `e10e6d9`
