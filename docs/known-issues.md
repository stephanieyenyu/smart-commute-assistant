# Known Issues

Each entry states whether it was checked against source code or against the database, and what is
still unconfirmed. Nothing is quietly deleted once resolved — the classification is the point of
the document.

**Verified against source** @ `e10e6d9`
**Verified against database** `PENDING` — B-class entries stay open until
`scripts/collect_metrics.sql` is run

| # | Issue | Class | Disposition |
|---|---|---|---|
| A-1 | Geocoding returned NULL coordinates and every failure was swallowed | Resolved defect | Fixed, guard added |
| A-2 | Compressed timing constants fire all three stages on one tick | Not a defect | Documented constraint |
| B-1 | `commute_logs` outcome fill rate unknown | Unverified | Open |
| B-2 | Duplicate `commute_overrides` and `commute_logs` rows | Unverified | Open |
| B-3 | `created_at` and `date` may disagree near midnight | Unverified | Open |
| C-1 | The dashboard router is never mounted | Defect | **Fixed** — router now included |
| C-2 | LIFF and dashboard routes accept an unverified identifier | Security | Fix recommended |
| C-3 | Database driver differs across three declarations | Configuration | **Fixed** — reconciled |
| C-4 | Degradation is toward optimism | Design limitation | Accepted, not fixed |
| C-5 | `bus_to_metro` is accepted but returns the default | Defect | Fix recommended |
| C-6 | Two source files differ only by letter case | Defect | **Fixed** — dead copy deleted |
| C-7 | `schema_guard` overlaps Alembic's role | Design limitation | Accepted, not fixed |
| C-8 | The scheduler holds no lock | Design limitation | Accepted, not fixed |
| D-1 | The tested timing logic is not the running timing logic | Documentation | Fix recommended |
| D-2 | Celery and Redis are declared but not deployed | Documentation | Fix recommended |
| D-3 | Two parallel grouping mechanisms exist | Documentation | Fix recommended |
| D-4 | Duplicate route spellings | Documentation | Fix recommended |
| D-5 | Two dashboard front ends are now both reachable | Documentation | Fix recommended |

---

## A. Investigated, resolved or not a defect

### A-1　Geocoding returned NULL coordinates and every failure was swallowed

**Symptom.** Commute suggestions degraded to a generic Google transit recommendation with no bus
or metro detail. No error appeared anywhere. The addresses were still stored and still displayed
correctly, so the settings looked fine.

**Cause.** The Google Cloud Geocoding API stopped serving requests after the free trial expired.
`geocode()` returned `None` for latitude and longitude. Those NULLs were written to the profile
and schedule, then read back by `_compute_today_plan()` and passed into the provider calls, which
failed in turn — and each of those failures was caught by `safe_call`, printed to stdout, and
converted to `None`. The pipeline continued to completion and produced a plausible answer.

**Why it took so long to find.** Three things had to line up. An address string being present
implied geocoding had worked. `safe_call` made every downstream failure look identical to "this
provider had nothing useful." And the fallback output was well-formed — it did not read as an
error, it read as a less specific suggestion.

**Fix.** An explicit precondition check at the point where coordinates become load-bearing, before
any provider call:

```python
if profile.home_lat is None or profile.home_lng is None \
   or profile.office_lat is None or profile.office_lng is None:
    return {"ok": False, "reason": "coords_missing", ...}
```

The message names which side failed and tells the user to select on the map rather than type an
address.

**Assessment.** The fix is not better error handling in the geocoder. `safe_call` returning `None`
is correct for weather and for transit snapshots — a suggestion without a rain buffer is still
useful. It is wrong for coordinates, because coordinates are a precondition rather than an
enrichment. The general lesson is that a uniform degradation policy is wrong when the inputs are
not uniformly optional.

**Residual.** Rows written during the outage still carry NULL coordinates. They surface as
`coords_missing` on next use rather than as silent degradation, which is the intended behaviour.

---

### A-2　Compressed timing constants fire all three stages on one tick

**Symptom.** Reducing `MORNING_MONITOR_OFFSETS` to shorten a demonstration caused all three
reminders to arrive simultaneously.

**Cause.** `EXACT_TRIGGER_WINDOW_SECONDS` was left at 75 while the offsets were compressed to 90
and 30 seconds. The windows `[T−90, T−15)`, `[T−30, T+45)` and `[T, T+120]` overlap, so a single
tick satisfies all three conditions at once.

**Assessment.** Not a defect. The window is not an independent constant — it is bounded below by
the tick interval, for at-least-once delivery, and bounded above by the minimum offset spacing,
for stage separation. With offsets 3600 s apart the upper bound is far away and easy to forget.

```
SCHEDULER_TICK_SECONDS  <  EXACT_TRIGGER_WINDOW_SECONDS  <  min(offset spacing)
```

**Consequence.** Recording a demonstration must not compress these constants. The safe method is to
move `frozen_departure_time`, which shifts all windows together and leaves the invariant intact.
Documented in the demo storyboard.

---

## B. Unverified

### B-1　`commute_logs` outcome fill rate unknown

**Observation.** `actual_departure_time`, `actual_arrival_time` and `is_late` are written only when
the user taps 「已出門」 and completes the follow-up. What fraction of rows carry them has not been
measured.

**Why it matters.** It decides whether the README carries an Evaluation section or an Evaluation
Protocol section. A low fill rate does not mean a small dataset — it means a biased one, because
the mornings least likely to be recorded are the rushed ones, which is the class the label is
about.

**Check.** `scripts/collect_metrics.sql` Q2.

**Impact.** Gates every outcome-derived figure in [`metrics.md`](metrics.md).

---

### B-2　Duplicate `commute_overrides` and `commute_logs` rows

**Observation.** `(user_id, schedule_id, target_date)` is not declared unique on
`commute_overrides`; `get_or_create_override()` enforces it in application code only. The same
applies to `(user_id, date)` on `commute_logs`.

**Hypothesis.** The single-process deployment means concurrent creation is unlikely, so duplicates
probably do not exist. This has not been checked.

**Check.**
```sql
SELECT user_id, schedule_id, target_date, COUNT(*)
FROM commute_overrides GROUP BY 1,2,3 HAVING COUNT(*) > 1;
```

**Impact.** Would inflate any per-day aggregate. Would also mean two rows racing on the same
delivery guards, which would break at-most-once delivery.

---

### B-3　`created_at` and `date` may disagree near midnight

**Observation.** `commute_logs.date` is computed in application code from `Asia/Taipei`.
`commute_logs.created_at` uses `server_default=func.now()`, which is the database clock — UTC on
Render. Between 00:00 and 08:00 Taipei time these fall on different calendar days.

**Impact.** Grouping by `created_at::date` and grouping by `date` give different answers. No
current query does the former, and the derivations in [`metrics.md`](metrics.md) all use `date`.

**Check.**
```sql
SELECT COUNT(*) FROM commute_logs WHERE DATE(created_at) <> date;
```

**Assessment.** Probably harmless today, since commutes are logged in the morning. Would become a
real problem for anything running near midnight.

---

## C. Defects and accepted limitations

### C-1　The dashboard router is never mounted

**Location.** `backend/app/dashboard.py:35` declares
`router = APIRouter(prefix="/api/v1/dashboard", tags=["dashboard"])` with nine routes.
`backend/app/main.py:232–235` includes four routers — `webhook`, `liff`, `ws`, `family`. There is
no `include_router` call for this one anywhere in the codebase.

**Symptom.** Every path under `/api/v1/dashboard` returns 404 in the deployed service, including
the personal view, the household view, both WebSocket endpoints, and both undelete routes.

**How it went unnoticed.** `main.py` declares its own dashboard routes directly on `app` —
`/dashboard`, `/api/dashboard/status`, `/api/history/{userId}` — and those work. The dashboard is
functional through a different, overlapping route set, so the missing router produces no visible
failure. An earlier README documented `/api/v1/dashboard/view/{user_id}` as the dashboard URL,
which was drawn from the source and was never reachable.

**Also affected.** Four `commute_overrides` columns — `departure_check_sent_at`,
`departure_confirmed_at`, `departure_snoozed_until`, `departure_timeout_at`,
`departure_timeout_silent` — are written only by this module, so they are dead columns in practice.

**Fix applied.** `app.include_router(dashboard_router)` added to `main.py`. The nine routes are now
mounted; the household queue, the WebSocket voice channel and the sleep/wake tiers are reachable.
Route paths do not collide with the existing declarations in `main.py`, so nothing that previously
worked was changed.

**Residual — see D-5.** The overlapping declarations in `main.py` were left in place, so two
dashboard front ends are now both reachable over the same data. That is deliberate for this commit:
removing one changes the URLs that LINE messages already link to, and should be done as its own
change.

**Severity.** Was high — the household sequential handoff, the dashboard voice prompts at T−60 and
T−5, and the sleep/wake behaviour that stops the system polling around the clock are all
implemented in this module and were all unreachable in production. The earlier assessment of
"roughly 400 lines of dead code" understated it: this is the feature set, not spare parts.

---

### C-2　LIFF and dashboard routes accept an unverified identifier

**Specific endpoints are deliberately not enumerated here.** This is a live deployment with a real
LINE channel. This is an audit finding, not a reproduction guide.

**Finding.** Exactly one route in the system verifies its caller: `POST /webhooks/line` checks
`X-Line-Signature` against the channel secret and rejects mismatches before parsing. That check is
correct.

Every other route identifies the caller by an identifier supplied in the request — a LINE User ID
in a request body, or a user or group identifier in a path. Several of those routes change state:
they create and delete schedules, and acknowledge alerts. A LINE User ID is not a secret; it
appears in every webhook payload and is passed to the LIFF page as a matter of course. Possession
of one is not evidence of being that user.

**Why the gap exists.** The LIFF pages hold no credentials of their own, so those routes were
exempted from verification as a block. The exemption keyed on *who calls this* rather than *what
does this change*. That is the wrong axis — a route that mutates state needs verification
regardless of who is calling.

**Fix direction.** LIFF provides an ID token intended for exactly this. Verify it against LINE and
match its `sub` claim to the `line_user_id` being acted on, on every state-changing
resident-reachable route. Dashboard views need a signed, expiring link rather than a bare user ID
in the path.

**Severity.** Medium. No credentials or payment data exist in this system. Someone holding an
identifier they were not meant to have could read another user's commute schedule and home-to-work
pattern, or delete their schedules. That is a real privacy exposure for a system that knows where
someone lives and when they leave.

---

### C-3　Database driver differs across three declarations

| File | Declares |
|---|---|
| `.env.example` | `postgresql+asyncpg://` |
| `docker-compose.yml` | `postgresql+psycopg2://` |
| Production (Render) | `pg8000`, per the deployment notes |

**Also inconsistent.** `.env.example` names `WEATHER_API_KEY`; `docker-compose.yml` passes
`CWA_API_KEY`. `weather.py` reads one of them, so the documented quickstart cannot start a working
service.

**Symptom.** `cp .env.example .env && docker compose up --build`, the documented setup path, does
not produce a running application.

**Background.** `pg8000` was adopted because on Python 3.14 without build tooling, `psycopg2`
fails to compile. That was the right call; it was applied to the deployment without updating the
two template files.

**Fix applied.** `db.py` calls `sqlalchemy.create_engine`, which is synchronous, so `asyncpg` could
never have worked; `backend/requirements.txt` installs `psycopg2-binary`. `.env.example` now
declares `postgresql+psycopg2://`, matching `docker-compose.yml`, with a comment covering the
`pg8000` substitution needed on Python 3.14 without build tooling.

The weather key is reconciled to `CWA_API_KEY`. `weather.py` accepts `WEATHER_API_KEY`,
`CWB_API_KEY` and `CWA_TOKEN` as fallbacks; the template documents the canonical name only. Every
variable in `.env.example` was checked against the `os.getenv()` call sites in `backend/app/`.

**Severity.** Was high for anyone trying to run this, which for a portfolio repository is the whole
audience.

---

### C-4　Degradation is toward optimism

> **Accepted, not fixed.** The correct fix requires surfacing degraded state in the user-facing
> message, which is a design change rather than a patch.

**Symptom.** When the CWA call fails or times out, `get_commute_weather()` substitutes
`{extra_buffer_minutes: 0, weather_text: "未知"}`. The rain buffer becomes zero and the suggested
departure time is up to ten minutes later than the rules would otherwise produce.

**Why the direction is wrong.** The failure makes the system advise leaving *later*, and it does so
without saying anything. A degraded weather call is most consequential precisely on the mornings
when weather mattered.

**Scope.** Four of the five degradation paths in
[`decision-engine.md`](decision-engine.md#degradation) produce a plausible answer built on missing
data. Weather is the worst because the direction is unsafe; the others produce a less specific
answer rather than a differently-shaped one.

**What already exists.** Every failed call writes a row to `api_health_logs` at the moment it
fails. The data needed to annotate the reply is present and is not read.

**Fix, if adopted.** Two options. Fail conservatively — substitute a default buffer rather than
zero when weather is unavailable. Or read `api_health_logs` for the current request and append a
line to the message saying which input was unavailable. The second is more honest and is the one
worth doing.

**Severity.** Medium. No incorrect output, but confidently presented output that the system has no
basis for.

---

### C-5　`bus_to_metro` is accepted but returns the default

**Location.** `service.py`, in `choose_commute_option_with_override()`:

```python
if requested_mode == "bus_to_metro":
    # Simplified for now, can be expanded if we have specific bus_to_metro logic
    return {"best_option": google_option, "selection_source": "manual"}
```

**Symptom.** 「今天搭公車轉捷運」 is in `COMMAND_ALIASES`, is accepted, writes
`transport_mode_override = 'bus_to_metro'`, and is acknowledged to the user. The returned plan is
the unrestricted Google option — the same result as `auto` when neither snapshot is available.

**Assessment.** A user who selects this mode is told it was applied and receives a plan that does
not reflect it. That is worse than not offering the command.

**Fix.** Either implement it — the natural approach is a Routes query with both `BUS` and the rail
modes allowed, compared against the single-mode results — or remove the alias and reject the
value.

**Severity.** Low functionally, higher in honesty terms. It is the one place the system tells the
user something that is not true.

---

### C-6　Two source files differ only by letter case

`backend/app/maps_client.py` and `backend/app/integrations/Maps_client.py` are near-identical.
Both import `redis_cache`; both contain the Routes and Geocoding clients.

**Symptom.** On a case-insensitive filesystem — macOS by default, Windows — cloning or checking out
can collide or resolve unpredictably. Imports resolve to whichever the interpreter finds first.

**Which was live — the opposite of what an earlier draft of this document recorded.**
`backend/app/integrations/Maps_client.py` is imported by `google_maps.py`, which is imported by
`main.py`. Nothing imports `backend/app/maps_client.py` at all, and
`tests/test_phase1_stability.py` loads the `integrations/` copy by path. The earlier claim that
`service.py` imports the root copy was wrong; it reaches the client through `google_maps`.

**Fix applied.** `backend/app/maps_client.py` deleted. The `integrations/` copy stays.

**Severity.** Was low in effect but high in the risk it carried: deleting the wrong one would have
broken the application, and the first attempt at this cleanup did exactly that before the import
graph was checked.

---

### C-7　`schema_guard` overlaps Alembic's role

> **Accepted, not fixed.** It has prevented real boot failures. Recorded because the overlap is a
> genuine design cost, not because it should be removed without a replacement.

**Location.** `main.py:201` calls `ensure_runtime_schema()` at import time, before the FastAPI app
is constructed. It adds missing columns directly.

**Consequence.** There are two mechanisms that can change the schema, and they do not know about
each other. Against a database that has already booted the app once, `alembic upgrade head` fails
because the columns already exist; `alembic stamp head` is correct instead. That is a non-obvious
operational rule that exists only because of this overlap.

**Why it is there.** Render's free tier expires databases. `ensure_runtime_schema()` makes the app
boot against a fresh or partially-migrated database rather than crash. Given a free-tier
deployment and a single operator, this was the pragmatic choice.

**Fix, if adopted.** Run `alembic upgrade head` in the Render build command and reduce
`ensure_runtime_schema()` to a verification that raises on mismatch instead of repairing it.

**Severity.** Low operationally, medium as a correctness concern: the schema in `models.py` and the
schema in the migrations can diverge without anything noticing.

---

### C-8　The scheduler holds no lock

> **Accepted, not fixed.** Single instance, single user. The fix is known and has not been needed.

**Symptom.** None, currently.

**Mechanism.** APScheduler is started in the FastAPI `lifespan` handler, inside the web process. A
second Render instance would run a second 30-second tick. The `*_sent_at` guards would not prevent
double delivery, because two ticks can read a NULL guard before either writes it — the check and
the write are not atomic.

**Consequence.** Horizontal scaling is unavailable. This is a property of the design, not a
configuration setting.

**Fix, if adopted.** A PostgreSQL advisory lock around the tick body, or moving the scheduler to a
separate single-instance worker service. `celery_app.py` already sketches the second approach; see
D-2.

**Severity.** None today. Would be immediate and silent on the first scale-out.

---

## D. Documentation and structural debt

### D-1　The tested timing logic is not the running timing logic

**Location.** `backend/app/reminder_timing.py` implements `evaluate_departure_reminder()`,
returning a `ReminderTimingDecision` of `WAIT` / `SEND` / `SKIP_STALE` / `ALREADY_SENT`. It is
covered by `tests/test_phase5_dashboard.py`, which exercises the boundary at 07:59, 08:00, 08:02
and 08:03.

`reminder_scheduler.py` does not import it. It reimplements the same comparison inline as
`_is_departure_confirmation_window()`, and declares its own copy of
`STALE_REMINDER_GRACE_SECONDS = 120`.

**Impact.** The tests pass and describe correct behaviour, but they do not test the code path that
runs in production. Changing the constant in one file and not the other would leave a green test
suite and a changed system.

**Fix.** Import `evaluate_departure_reminder` in the scheduler and delete the inline version and
the duplicated constant. The signature already matches what the scheduler needs.

**Severity.** Medium. Tests that do not cover the running code are worse than no tests, because
they are read as coverage.

---

### D-2　Celery and Redis are declared but not deployed

`celery_app.py` defines a beat schedule duplicating both APScheduler jobs;
`app/tasks.py` defines the corresponding tasks. `render.yaml` declares no worker service, so
nothing runs them.

`integrations/redis_cache.py` is imported by `maps_client.py` and `tdx_client.py`, with cache TTLs
of 86400 s for geocoding, 300 s for routes and 3600 s for walk times. `REDIS_URL` defaults to
`redis://localhost:6379/0` and no Redis service is provisioned, so every operation falls through to
the in-process `_fallback_cache` — per-process, unbounded, and lost on restart.

**Impact.** A reader inspecting the codebase would reasonably conclude the system has a task queue
and a shared cache. It has neither. The caching still works, but with different characteristics
than the code suggests.

**Fix.** Either provision both in `render.yaml`, or remove `celery_app.py` and `tasks.py` and
rename `redis_cache.py` to reflect what it actually does. Documented in
[`architecture.md`](architecture.md#known-deviations) in the meantime.

---

### D-3　Two parallel grouping mechanisms exist

`households` + `users.household_id`, and `family_groups` + `family_members`. Both model "several
users who see each other's status", with separate invite tokens and separate route sets.

The household path is reached only through the unmounted dashboard router (C-1). The family path is
reached through `/api/family/*`, which is mounted. Only the second is live.

**Fix.** Delete the unused one. Which is unused depends on how C-1 is resolved, so this should be
decided together with it.

---

### D-4　Duplicate route spellings

Ten routes in the LIFF-facing group cover four operations:

- `POST /liff/schedule/submit`, `POST /api/schedule/submit`, `POST /api/schedule/submit/` — one operation
- `POST /api/schedule/add`, `POST /api/schedule/add/` — one operation, and `add` is additionally declared on the unmounted `api_router`
- `DELETE /api/schedule`, `DELETE /api/schedule/{schedule_id}`, `POST /api/schedule/delete` — one operation, three ways

Trailing-slash variants are registered explicitly rather than relying on FastAPI's
`redirect_slashes`.

**Impact.** Documentation surface is 2.5× the actual operation count, and nothing indicates which
spelling is canonical.

**Fix.** Keep one path per operation, let `redirect_slashes` handle trailing slashes, and remove the
rest once the LIFF front end is updated.

---

### D-5　Two dashboard front ends are now both reachable

Mounting the dashboard router (C-1) did not remove the overlapping declarations in `main.py`, so the
deployed service now serves two dashboards over the same data.

| | `dashboard_view.py` | `dashboard_page.py` |
|---|---|---|
| Lines | 351 | 1,044 |
| Served by | `main.py` — `/dashboard`, `/dashboard/family`, `/family-dashboard` | `dashboard.py` — `/api/v1/dashboard/view/{user_id}`, `/api/v1/dashboard/household/{id}/view` |
| WebSocket client | none | yes |
| Household queue | none | yes — `primary`, `queue_position`, `queue_members` |
| Sleep / wake tiers | none, fixed 30 s poll | yes — 300 s asleep, 30 s active |

The second is the complete one. The first predates it and was never removed.

**Why both were left in.** LINE messages already carry links to the `main.py` paths. Removing them
breaks links that have been sent, so it belongs in its own change with the link builder updated at
the same time.

**Fix.** Point `dashboard_links.py` at the `/api/v1/dashboard` paths, confirm the links in newly
sent messages resolve, then delete `dashboard_view.py` and the three route declarations in
`main.py`. D-3 should be decided in the same pass, since the household grouping mechanism becomes
the live one once this is done.

---

**Source** `backend/app/` @ `e10e6d9` — C and D entries verified against source; A-1 and A-2
verified against source and observed behaviour; B entries await
[`metrics.md`](metrics.md) collection
