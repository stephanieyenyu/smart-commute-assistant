# Data Dictionary

**Source** `backend/app/models.py`
**Diagram** [`images/er-diagram.png`](images/er-diagram.png)
**Scale** 10 tables · 15 Alembic revisions · one PostgreSQL instance on Render

## Conventions that apply throughout

**Foreign keys are declared.** Every relationship below is a real SQLAlchemy `ForeignKey`, unlike a
string-matched association. **No `ON DELETE` behaviour is specified anywhere**, so deleting a user
leaves orphaned rows in six tables unless application code clears them first. Nothing cascades.

**Times of day are strings, not `TIME`.** `commute_schedules.time`,
`commute_overrides.target_arrival_time`, `commute_overrides.frozen_departure_time`, and four
columns in `commute_logs` are all `VARCHAR` holding `'HH:MM'`. Any arithmetic requires a cast, and
malformed values are possible — the derivation queries in [`metrics.md`](metrics.md) guard with a
regex and report how many rows they excluded rather than dropping them silently.

**Weekdays are 0-indexed from Monday**, matching Python's `datetime.weekday()`. This applies to
`commute_schedules.days` and `commute_profiles.active_weekdays`, both JSON arrays.

**All timestamps are `Asia/Taipei`.** One service, one timezone, no cross-service comparison to get
wrong. `now_taipei()` in `reminder_scheduler.py` is the single source; `server_default=func.now()`
columns take the database's clock, which on Render is UTC — see
[`known-issues.md`](known-issues.md#b-3created_at-and-date-may-disagree-near-midnight).

**Some structure is created outside Alembic.** `schema_guard.ensure_runtime_schema()` runs at
import time in `main.py`, before the FastAPI app is constructed, and adds missing columns
directly. This overlaps Alembic's role and is why `alembic stamp head` rather than `upgrade head`
is correct against a database that has already booted the app once. Recorded in
[`known-issues.md`](known-issues.md#c-7schema_guard-overlaps-alembics-role).

---

## `commute_logs`

The decision record, and the reason the schema is shaped the way it is. Every field the engine
consulted at decision time is stored alongside what it chose and what actually happened, laid out
as (features, action, outcome). The rule-based engine was always intended to be replaceable by a
learned policy, and this table is the artifact that makes that possible.

### Identity

| Column | Type | Constraint | Notes |
|---|---|---|---|
| `id` | INTEGER | PK | |
| `user_id` | INTEGER | FK → `users.id`, NOT NULL | |
| `date` | DATE | NOT NULL, INDEX | The commute date, not the write time |

### Features — state at decision time

| Column | Type | Notes |
|---|---|---|
| `day_of_week` | INTEGER | 0 = Monday |
| `is_holiday` | BOOLEAN | |
| `target_arrival_time` | VARCHAR | `'HH:MM'`, after override resolution |
| `weather_condition` | VARCHAR | CWA description text, the same string the buffer rule keyword-matches |
| `rain_prob` | INTEGER | Percentage. **The single strongest input to the buffer rule** |
| `temp` | FLOAT | Not consumed by any rule; recorded for later use |
| `gmaps_traffic_duration` | INTEGER | Minutes, from the chosen Google Routes result |
| `tdx_bus_eta` | INTEGER | Minutes to the next bus at the selected stop |

### Action — what the policy chose

| Column | Type | Notes |
|---|---|---|
| `suggested_departure_time` | VARCHAR | `'HH:MM'` |
| `suggested_transport` | VARCHAR | `google_transit` / `bus` / `metro` |

### Outcome — what happened

| Column | Type | Notes |
|---|---|---|
| `actual_departure_time` | VARCHAR | Written when the user taps 「已出門」 |
| `actual_transport` | VARCHAR | Self-reported |
| `actual_arrival_time` | VARCHAR | Self-reported |
| `is_late` | BOOLEAN | **The label** |
| `created_at` | DATETIME | `server_default=func.now()` |

**What this table does not give you.** The outcome fields are self-reported, not observed. A user
who forgets to tap produces a NULL, not a negative example, so missingness is correlated with the
behaviour being measured — the days most likely to go unrecorded are exactly the rushed ones.
`is_late` is a user's own judgement against a target they set themselves. **Fill rate, not row
count, is the figure that matters**, and it is the first thing [`metrics.md`](metrics.md) reports.

---

## `api_health_logs`

Every outbound provider call, written by `log_api_health()` at 17 call sites. Append-only.

| Column | Type | Constraint | Notes |
|---|---|---|---|
| `id` | INTEGER | PK | |
| `endpoint` | VARCHAR | NOT NULL, INDEX | 6 labels, listed below |
| `timestamp` | DATETIME | NOT NULL, INDEX | |
| `latency_ms` | INTEGER | | Wall clock around the call |
| `status_code` | INTEGER | | NULL when the call never got a response |
| `error_message` | VARCHAR | | NULL on success |

| Label | Written by |
|---|---|
| `google.routes.transit` | `maps_client.py` |
| `google.routes.walk` | `maps_client.py` |
| `google.geocode` | `maps_client.py` |
| `tdx.bus.auth` | `tdx_bus.py` |
| `tdx.metro.auth` | `metro_basic.py` |
| `cwa.weather.city` | `weather.py` |

**No foreign key, deliberately.** The table carries no user reference, so it survives user
deletion and can be published without de-identification. Every external-reliability figure in
this project derives from it. Before it existed, provider failures went to `print()` and vanished
on the next Render restart.

**A failure row and a genuine outage are not the same thing.** A row with `error_message` set
records that the call as issued did not return usable data. It does not distinguish a provider
outage from a timeout the client imposed, or from a quota error. `google.geocode` rows carrying
authorisation errors are the trace of
[`known-issues.md` A-1](known-issues.md#a-1geocoding-returned-null-coordinates-and-every-failure-was-swallowed).

---

## `commute_overrides`

Per-user, per-schedule, per-day mutable state. Three distinct concerns share this table: the
user's intent for the day, the frozen plan, and the delivery guards.

| Column | Type | Constraint | Notes |
|---|---|---|---|
| `id` | INTEGER | PK | |
| `user_id` | INTEGER | FK → `users.id`, NOT NULL | |
| `schedule_id` | INTEGER | FK → `commute_schedules.id`, **nullable** | NULL rows predate multi-schedule support; lookup falls back to a schedule-agnostic row |
| `target_date` | DATE | NOT NULL, INDEX | |

### Intent overrides for the day

| Column | Type | Notes |
|---|---|---|
| `target_arrival_time` | VARCHAR | Wins over `commute_schedules.time` when present |
| `transport_mode_override` | VARCHAR | `auto` / `shortest` / `bus` / `metro` / `bus_to_metro` |
| `commute_disabled` | BOOLEAN | |
| `commute_enabled` | BOOLEAN | Separate column, not the negation of the above — both nullable, so "unset" is distinguishable from "explicitly off" |

### Frozen plan — computed once, read every tick

| Column | Type | Notes |
|---|---|---|
| `frozen_plan_key` | VARCHAR | Hash of the inputs; changes when the plan would differ |
| `frozen_departure_time` | VARCHAR | **`T` throughout [`state-machines.md`](state-machines.md)** |
| `frozen_reminder_text` | TEXT | The rendered message, stored so all three stages agree |
| `reminder_prepared_at` | DATETIME | |

All three must be non-NULL for the row to be selected by a tick.

### Delivery guards — the at-most-once mechanism

| Column | Type | Notes |
|---|---|---|
| `monitor_one_hour_sent_at` | DATETIME | |
| `monitor_five_min_sent_at` | DATETIME | |
| `departure_question_sent_at` | DATETIME | |
| `departed_at` | DATETIME | **Terminal.** Set from the 「已出門」 reply; skips the row entirely |
| `nightly_brief_sent_at` | DATETIME | Guards the 21:00 job |
| `nightly_brief_plan_key` | VARCHAR | |
| `last_sent_at` / `last_sent_plan_key` | DATETIME / VARCHAR | Legacy single-stage guards, superseded by the per-stage columns above |
| `departure_check_sent_at` | DATETIME | Written by the dashboard departure-check path |
| `departure_confirmed_at` | DATETIME | |
| `departure_snoozed_until` | DATETIME | |
| `departure_timeout_at` | DATETIME | |
| `departure_timeout_silent` | BOOLEAN | NOT NULL, defaults `False` |
| `alert_status` | VARCHAR | `pending` / `acknowledged` — dashboard only, gates nothing |

Four of these columns (`departure_snoozed_until`, `departure_timeout_at`,
`departure_timeout_silent`, `departure_check_sent_at`) are written but never read by the tick.
They belong to the dashboard path in `app/dashboard.py`, which is not mounted — see
[`known-issues.md`](known-issues.md#c-1the-dashboard-router-is-never-mounted).

---

## `commute_schedules`

A recurring commute. A user may have several; `_compute_today_plan()` selects by `schedule_id` if
given, otherwise the first one active for the date.

| Column | Type | Constraint | Notes |
|---|---|---|---|
| `id` | INTEGER | PK | |
| `user_id` | INTEGER | FK → `users.id`, NOT NULL | |
| `origin_name` / `origin_address` | VARCHAR | | |
| `origin_lat` / `origin_lng` | FLOAT | | NULL means geocoding failed — see below |
| `dest_name` / `dest_address` | VARCHAR | | |
| `dest_lat` / `dest_lng` | FLOAT | | |
| `time` | VARCHAR | | **Target arrival time**, not departure |
| `days` | JSON | | Weekday array, 0 = Monday |
| `is_active` | BOOLEAN | NOT NULL | defaults `True` |
| `reminder_enabled` | BOOLEAN | NOT NULL | defaults `True`; checked per tick |

**These coordinates override the profile, always.** `_compute_today_plan()` copies
`origin_*` and `dest_*` onto the in-memory profile snapshot unconditionally, and never falls back
to `commute_profiles.home_*`. A schedule the user just edited must win over whatever the profile
last cached, and the earlier fallback behaviour was the mechanism by which stale home data
survived an edit.

---

## `commute_profiles`

35 columns, 1:1 with `users`. Holds long-lived preferences and the cached results of geocoding and
nearest-stop lookups.

| Group | Columns |
|---|---|
| Home | `home_address`, `home_lat`, `home_lng`, `home_city`, `home_township`, `home_place_name` |
| Office | `office_address`, `office_lat`, `office_lng`, `office_city`, `office_township`, `office_place_name` |
| Bus stop | `selected_bus_stop_id`, `_name`, `_lat`, `_lng`, `walk_to_bus_stop_min`, `last_computed_walk_to_bus_stop_min` |
| Metro station | `selected_metro_station_id`, `_name`, `_lat`, `_lng`, `last_computed_walk_to_metro_min` |
| Preferences | `preferred_arrival_time`, `preferred_mode`, `transport_preference` (JSON), `max_walk_mins`, `active_weekdays` (JSON), `reminder_enabled`, `identity_type`, `destination_label` |
| Conversation state | `pending_field` |

`home_city` is what `get_commute_weather()` uses to select a CWA forecast area. When it is NULL,
weather returns the failure shape and the buffer is 0.

`pending_field` holds the name of the field a multi-turn text conversation is currently waiting
for. It is durable conversational state stored in the same row as durable preferences, which means
an abandoned conversation leaves the profile in a waiting state until the user says something
else. Low impact; noted for completeness.

---

## `users`

| Column | Type | Constraint | Notes |
|---|---|---|---|
| `id` | INTEGER | PK | |
| `line_user_id` | VARCHAR | UNIQUE, INDEX, NOT NULL | The LINE User ID; every push targets this |
| `display_name` | VARCHAR | | |
| `household_id` | INTEGER | FK → `households.id`, nullable | |
| `created_at` | DATETIME | NOT NULL | |

---

## `households`, `family_groups`, `family_members`, `commute_destinations`

| Table | Columns | Notes |
|---|---|---|
| `households` | `id` PK, `invite_code` UNIQUE, `name`, `created_at`, `updated_at` | Referenced by `users.household_id` |
| `family_groups` | `id` PK, `name` NOT NULL, `invite_token` UNIQUE INDEX, `created_at` | Reached through `/api/family/*` |
| `family_members` | `id` PK, `group_id` FK, `user_id` FK, `nickname`, `joined_at` | Join table |
| `commute_destinations` | `id` PK, `user_id` FK, `destination_name` | Saved destination labels |

**Two parallel grouping mechanisms exist.** `households` + `users.household_id` and
`family_groups` + `family_members` both model "several users who see each other's status", by
different routes and with different invite tokens. The household path is reached by the unmounted
dashboard router; the family path is reached by `/api/family/*`, which is mounted. Only the second
is live. This is duplication left by an unfinished migration, not two intentional features.

---

# Relationships

| Relationship | Cardinality | Via |
|---|---|---|
| `households` → `users` | 1..N | `household_id` (nullable) |
| `users` → `commute_profiles` | 1..1 | `user_id` UNIQUE |
| `users` → `commute_schedules` | 1..N | `user_id` |
| `users` → `commute_overrides` | 1..N | `user_id` |
| `commute_schedules` → `commute_overrides` | 1..N | `schedule_id` (nullable) |
| `users` → `commute_logs` | 1..N | `user_id` |
| `users` → `commute_destinations` | 1..N | `user_id` |
| `family_groups` → `family_members` | 1..N | `group_id` |
| `users` → `family_members` | 1..N | `user_id` |
| `api_health_logs` | — | no relationship, by design |

The load-bearing composite is `(user_id, schedule_id, target_date)` on `commute_overrides`. It is
**not declared unique**, so nothing at the database layer prevents two rows for the same morning.
`get_or_create_override()` enforces uniqueness in application code only. Whether duplicates exist
in production is checked in [`metrics.md`](metrics.md) and open in
[`known-issues.md`](known-issues.md#b-2duplicate-commute_overrides-and-commute_logs-rows).

---

# Deliberately out of scope

- The rules that produce `suggested_transport` and `suggested_departure_time` →
  [decision engine](decision-engine.md)
- How the `*_sent_at` guards combine into exactly-once delivery →
  [state machines](state-machines.md)
- Request and response shapes per endpoint → FastAPI's generated `/docs`
- Module call relationships → [architecture](architecture.md)

---

**Source** `backend/app/models.py`, `backend/app/schema_guard.py` @ `80ee635` — verified line by
line
