# Metrics

Every figure quoted in the README is derived here, with the query or command that produced it, so it
can be checked rather than accepted.

**The database figures are all zero, and the zeroes are the finding.** Both tables this project
treats as evidence were structurally unwritable. Nothing below is estimated, and nothing absent is
presented as merely uncollected.

**Snapshot date** 2026-08-27
**Observation window** none yet — see below
**Raw export** not produced. `scripts/export_data_scrubbed.py` runs and writes empty arrays,
so nothing is committed under `data/`.

The deployment is a single user — me — on one origin–destination pair in Taipei. Nothing in this
document generalises beyond that, and the [Interpretation](#interpretation) section says so per
figure rather than once at the end.

---

## Sources

Measured 2026-08-27 against the production instance.

| Table | Rows | Note |
|---|---|---|
| `commute_logs` | **0** | No producer exists — [C-9](known-issues.md#c-9commute_logs-has-no-producer) |
| `api_health_logs` | **0** | Persistence was broken until 2026-08-27 — [A-6](known-issues.md#a-6api_health_logs-was-never-written-to) |
| `commute_overrides` | 1 | |
| `commute_profiles` | 1 | |
| `users` | 1 | |
| `commute_schedules` | 0 | |
| `households`, `family_groups`, `family_members`, `commute_destinations` | 0 | |

**Both zeroes are structural, not empty-database artifacts.** `commute_logs` is never constructed
anywhere in `backend/`. `api_health_logs` was written through a crud function that did not exist,
and the resulting `ImportError` was caught by a bare `except` and printed to stdout, which does not
survive a Render restart. The second is fixed; the first is not implemented.

Everything below is therefore a protocol rather than a result. The queries are written and correct;
they have been run and returned zero. **No figure in this document is estimated, and none is
presented as measured when it is not.**

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

Figures below count `backend/` only. The four scripts under `scripts/` and `docs/diagrams/` are
documentation tooling rather than part of the system; counting them would make the figure move
every time a script is added, which happened once already.

| Figure | Value | Derivation |
|---|---|---|
| Commits | 194 | `git rev-list --count HEAD` at `80ee635` |
| Development window | 2026-04-22 → 2026-07-30 | `git log --format=%ad --date=short` |
| Application source files | 53 | `git ls-files "backend/*.py" \| wc -l` |
| Application Python LOC | 12,315 | `git ls-files "backend/*.py" \| xargs wc -l` |
| Test LOC | 1,451 across 4 files | `wc -l tests/test_*.py` |
| Documentation tooling | 4 scripts | `scripts/*.py`, `docs/diagrams/gen_diagrams.py` — excluded above |
| HTTP routes declared | 37 distinct paths | Route inventory, `docs/api.md` |
| HTTP routes mounted | 34 + 3 WebSocket | Declared minus the one unmounted router, `docs/api.md` |
| Database tables | 10 | `grep -c "__tablename__" backend/app/models.py` |
| Alembic revisions | 15 | `ls backend/alembic/versions/*.py \| wc -l` |
| Scheduled jobs | 2 | `scheduler.add_job` registrations |
| Instrumented provider endpoints | 6 | Distinct first arguments to `log_api_health()` |
| `log_api_health` call sites | 17 | `grep -rc "log_api_health(" backend/app` |
| LINE command keys | 25 | `COMMAND_ALIASES` keys in `webhook.py` |
| LINE postback actions | 2 | `postback_data ==` comparisons in `webhook.py` |

**Declared and mounted differ by three routes.** `dashboard.py` was unmounted until `76859d9`
and accounted for nine of them; the remainder belong to `liff_routes.py:api_router`, which is
still not included and whose one route is declared again in `main.py`. Quoting 37 without that
distinction would overstate the reachable surface. See [`api.md`](api.md#unreachable-routes).

**Two scheduled jobs, not three.** The three reminder stages are branches inside one 30-second
tick. Counting them as three jobs would be wrong.

---

## Dataset fill rate

**Not applicable.** This section assumed a `commute_logs` row per planned commute with the outcome
columns populated on confirmation. Neither half exists: the table has no producer, and `is_late` has
no producer even if it did, since nothing asks the user whether they arrived on time.

The query is kept because it is the first thing to run once C-9 is implemented, and because the
reasoning below is what the figure would have to be read against.

```sql
SELECT
    COUNT(*)                          AS total_rows,
    COUNT(suggested_departure_time)   AS has_action,
    COUNT(actual_departure_time)      AS has_actual_departure,
    COUNT(actual_arrival_time)        AS has_actual_arrival,
    COUNT(is_late)                    AS has_label
FROM commute_logs;
```

All columns: 0 of 0 rows.

**What a low fill rate would mean, once there is one.** Not that the dataset is small — that it is
*biased*. A missing row would be a morning the user did not tap, and the mornings least likely to be
recorded are the rushed ones. The label would be missing-not-at-random with respect to the thing
being labelled: a model trained on the populated rows would be trained on the calm mornings.

---

## External provider reliability

The measurement this project is built to make, and the one it did not make. Every outbound call is
routed through `log_api_health()`, but until 2026-08-27 that function's persistence path raised
`ImportError` on every invocation and the exception was swallowed. **Rows accumulate from that deploy
onward; there is no earlier history to recover.**

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
| all six | 0 | — | — | — | — |

Re-run once the table has accumulated. The six endpoint labels are listed in
[`database.md`](database.md#api_health_logs).

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

No data. Reported per day with a denominator once there is any, because a failure count without a
call count is not interpretable — and because the geocoding outage in
[`known-issues.md` A-1](known-issues.md#a-1geocoding-returned-null-coordinates-and-every-failure-was-swallowed)
would have shown as a contiguous block rather than as background noise. It predates the fix, so it
will not appear.

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

Not computable: 0 rows.

**A high agreement rate would be close to meaningless here.** The user and the author are the same
person, and `actual_transport` would be self-reported after seeing the suggestion. Anchoring alone
would produce agreement. The query is kept for when C-9 is implemented, with that caveat attached in
advance.

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

Not computable: 0 rows.

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

### Duplicate rows

`(user_id, schedule_id, target_date)` on `commute_overrides` is not declared unique; uniqueness is
enforced by `get_or_create_override()` in application code only. Q13 returned zero duplicate groups
on 2026-08-27, against one override row — which is too few rows for the check to mean anything yet.
[`known-issues.md` B-2](known-issues.md#b-2duplicate-commute_overrides-and-commute_logs-rows)
stays open.

### The two empty tables are the headline result

A reader looking for numbers here will find none, and the reason is worth more than the numbers
would have been. Both of this project's evidence tables were unwritable, one because a crud function
was missing and the `ImportError` was swallowed by a bare `except`, the other because no producer was
ever written. Neither failed loudly. Both were found by querying the database rather than by reading
the code — which is exactly the class of defect
[`known-issues.md` A-1](known-issues.md#a-1geocoding-returned-null-coordinates-and-every-failure-was-swallowed)
describes, arrived at a second time by the same route.

---

## Filling this document

```bash
set DATABASE_URL=<External Database URL from Render>
python scripts/run_metrics.py > metrics_raw.txt
python scripts/export_data_scrubbed.py && python scripts/export_data_scrubbed.py --verify
python scripts/collect_repo_stats.py --markdown
```

`scripts/run_metrics.py` needs no `psql`; `scripts/collect_metrics.sql` holds the same queries for
reference. Re-run once `api_health_logs` has accumulated. If a query returns too few rows to support
a figure, **write that instead of the figure** — a stated absence is consistent with the rest of this
document in a way that a thin number is not.

---

**Source** `scripts/collect_metrics.sql`, `scripts/collect_repo_stats.py` · repository figures @ `80ee635`
