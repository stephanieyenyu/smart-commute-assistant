# Decision Engine

**It contains no learned components.** Every rule below was written by hand, and every threshold
was chosen by judgement rather than fitted to data. This document states each rule explicitly so
that claim can be checked rather than taken on trust.

The engine is a behaviour policy: it maps the state of a morning to an action — a departure time and
a transport mode. A schema exists to record the action alongside the features and the outcome, and
**nothing writes to it**; the design is real and the write path is not implemented. See
[Why this is shaped like a dataset](#why-this-is-shaped-like-a-dataset).

**Source** `backend/app/service.py`, `backend/app/weather.py`
**Diagram** [`images/decision-flow.png`](images/decision-flow.png)

---

## Pipeline

`_compute_today_plan(db, user_id, target_date, force_mode_override, schedule_id)` runs nine steps.
The first three are guards and can terminate.

| # | Step | Terminates with |
|---|---|---|
| 1 | Resolve which schedule applies to the date | `no_schedule_for_date` |
| 2 | Copy schedule coordinates onto the profile snapshot | — |
| 3a | Check origin, destination and arrival time are present | `setup_incomplete` |
| 3b | Check all four coordinates are non-NULL | `coords_missing` |
| 4 | Resolve effective arrival time and transport mode | — |
| 5 | Outer fan-out — weather and options, concurrently | — |
| 6 | Inner fan-out — provider calls, concurrently | — |
| 7 | Mode selection | — |
| 8 | Weather buffer | — |
| 9 | Departure time | — |

### 1 · Schedule resolution

Schedules for the user are filtered to those with `is_active` true and `days` containing the
target weekday. If `schedule_id` was supplied, that schedule is used, but only if it is active for
the date; otherwise the first match is taken. **Ordering is unspecified** — with two schedules
active on the same weekday, which one is chosen is whatever the database returned.

### 2 · Coordinates come from the schedule, always

`origin_*` and `dest_*` overwrite `profile.home_*` and `profile.office_*` unconditionally. There is
no fallback to the stored profile. A schedule the user just edited must win over whatever the
profile last cached.

### 3 · Guards

`setup_incomplete` fires when an address or arrival time is missing.

`coords_missing` fires when any of the four coordinates is NULL and names which side failed:

> ⚠️ {住家／目的地}地址沒有成功轉換成地圖座標,無法計算通勤方式。
> 請重新設定,並在地圖上直接選點(而不是只打字輸入地址),確保有正確定位。

This guard exists because it once did not. See
[`known-issues.md`](known-issues.md#a-1geocoding-returned-null-coordinates-and-every-failure-was-swallowed).
It is the **only** branch in the pipeline where a missing external answer produces a visible error
rather than a plausible one.

### 4 · Intent resolution

```
arrival_time = override.target_arrival_time  ??  schedule.time
mode         = force_mode_override  ??  stored transport_mode_override  ??  "auto"
```

Override lookup is tried with `schedule_id` first, then again without it, so rows predating
multi-schedule support still resolve.

---

## 5–6 · What gets called, and when

The outer level is one `asyncio.gather` over two coroutines. The inner level creates tasks before
awaiting any of them, so all issued calls overlap.

| Call | Issued when mode is | Timeout |
|---|---|---|
| Weather (CWA) | always | 2.2 s |
| Options (the whole inner fan-out) | always | 4.8 s |
| Google Routes, `allowed_travel_modes` per mode | always | 4.2 s |
| Google Routes restricted to `[BUS]` | `shortest` only | 4.2 s |
| Google Routes restricted to `[SUBWAY, TRAIN, RAIL, LIGHT_RAIL]` | `shortest` only | 4.2 s |
| TDX bus snapshot | `auto`, `shortest`, `bus` | 2.5 s |
| TDX metro snapshot | `auto`, `shortest`, `metro` | 3.5 s |

`allowed_travel_modes` on the always-issued call is `None` for `auto` and `shortest`, `["BUS"]`
for `bus`, and the four rail values for `metro`.

The outer timeout of 4.8 s is only 0.6 s longer than the inner Routes timeout of 4.2 s. A Routes
call that returns at 4.1 s leaves 0.7 s for the remaining awaits and the option construction —
tight, and the mechanism by which a slow-but-successful provider still produces the `None` fallback.

### Why `shortest` needs three calls

An unrestricted Routes query returns the single itinerary Google considers best. It does not
report how long a bus-only journey would take, or a metro-only one — the alternatives are simply
absent from the response. Comparing modes therefore means asking the same question three times
with different constraints.

Returning Google's default and labelling it "shortest" would have been one call, and would have
been wrong. Because the three run concurrently, the honest version costs the same wall clock as
the dishonest one: about 4.2 s rather than 12.6 s.

---

## 7 · Mode selection

### `shortest`

Score each candidate by the `duration_minutes` of its corresponding Routes response. A candidate
is scored only if both its option object and its duration exist. Sort ascending, take the minimum.

| Candidate | Scored by |
|---|---|
| Google transit | unrestricted Routes `duration_minutes` |
| Bus | bus-restricted Routes `duration_minutes` |
| Metro | metro-restricted Routes `duration_minutes` |

If no candidate has a duration, the unrestricted Google option is returned unchanged. The user is
told the comparison was made either way.

**Ties resolve to the unrestricted Google option**, because it is appended to the candidate list
first and the sort is stable. That is list order rather than a decision; no rule prefers it. A
defensible tie-break would favour bus or metro, since those carry a live ETA and stop names the
generic answer does not.

### `auto`

Fixed priority, no scoring: **metro → bus → Google transit**. The first option that could be
constructed wins. Metro outranks bus because metro headways in Taipei are shorter and more
predictable; that is a judgement, not a measurement, and no data in this project supports it.

An option is "constructible" when either its TDX snapshot reports `available`, or the Google
response contains a matching transit step. So `auto` can select `metro` on the strength of a
Google step alone, with no live TDX data behind it.

### Heavy rail

`vehicle_type` values containing `HEAVY_RAIL`, or equal to `RAIL`, are TRA intercity and regional
services and are labelled 🚆 建議搭鐵路. They were previously matched by the metro test, because it
checked for `"RAIL"` as a substring — see
[`known-issues.md` C-10](known-issues.md#c-10tra-heavy-rail-was-announced-as-metro).

### `bus` / `metro`

Forced. If the snapshot is unavailable the option is still returned, as a stub carrying only the
penalty constants and the Google response. The user gets the mode they asked for, with a
lower-quality estimate and no indication that the estimate is degraded.

### `bus_to_metro`

Accepted as a command, returns the unrestricted Google option unchanged. Not implemented. See
[`known-issues.md`](known-issues.md#c-5bus_to_metro-is-accepted-but-returns-the-default).

### Penalty constants

| Mode | Wait | Reliability penalty | Transfer |
|---|---|---|---|
| Bus | from live ETA, or 0 | 3 min | — |
| Metro | 3 min | 1 min | 2 min |
| Google transit | — | — | — |

Six hand-chosen integers. Bus carries a larger reliability penalty than metro on the same
reasoning as the `auto` ordering, and with the same absence of evidence.

---

## 8 · Weather buffer

`_calculate_weather_buffer(pop, weather_text)` in `weather.py`, evaluated in order:

| Condition | Buffer |
|---|---|
| rain probability ≥ 80 | +10 min |
| rain probability ≥ 60 | +8 min |
| rain probability ≥ 40 | +5 min |
| description contains 雨 / 雷 / 陣雨 / 雷雨 / 豪雨 / 大雨 | +6 min |
| otherwise | 0 |

The keyword branch is reached only when rain probability is NULL or below 40, so a 30 % chance of
rain described as 陣雨 yields 6 minutes while a 45 % chance described as 多雲 yields 5. The two
signals are not combined; the first matching rule wins.

**These five numbers are the most obviously fittable thing in the system.** Four thresholds and
five outputs, against an outcome column that already records whether the user was late. They are
named here because that is the point — this is the component `commute_logs` was instrumented to
replace.

---

## 9 · Departure time

```
latest_on_time = arrival_time − (route_duration + weather_buffer)
```

Then, by mode:

**Bus with a live ETA**

```
leave_in  = max(0, eta_min − 3 − walk_minutes)
departure = min(now + leave_in, latest_on_time)
```

The `min()` is the substantive part. Chasing a specific bus must never push departure later than
arriving on time allows. Without it, a bus 40 minutes out would produce a departure time 40
minutes from now regardless of when the user needed to arrive.

**Bus without a live ETA**

```
departure = latest_on_time − (wait_minutes + reliability_penalty)
```

**Metro**

```
departure = latest_on_time
```

Google's duration already includes expected platform waiting, so the metro penalty constants are
defined but not applied here. Walk-to-station time appears in the explanation text only.

**Google transit**

```
departure = latest_on_time − mode_extra_minutes
```

---

## Degradation

`safe_call` catches `asyncio.TimeoutError` and every `Exception`, prints one line, and returns
`None`. Nothing propagates, so each caller decides what a missing answer means.

| Call returns `None` | Substituted | Effect on the answer |
|---|---|---|
| Weather | `{extra_buffer_minutes: 0, weather_text: "未知"}` | Buffer becomes 0. The suggestion is tighter than it should be on a wet morning |
| Option choice | `{best_option: {mode: google_transit}, selection_source: auto}` | Generic transit advice |
| Google Routes | `{}` | No steps and no duration; route time falls back to `DEFAULT_COMMUTE_MINUTES` |
| TDX bus / metro snapshot | option not constructed | `auto` falls silently to the next priority |
| Geocoding | caught at step 3b | **Visible error** |

**Four of these five produce a plausible answer built on missing data.** The service stays up and
the user cannot tell. Weather degrading to a zero buffer is the worst of them, because the failure
direction is toward optimism — the system is most likely to under-advise on exactly the mornings
when the buffer mattered. Recorded in
[`known-issues.md`](known-issues.md#c-4degradation-is-toward-optimism).

Every one of these calls writes a row to `api_health_logs`, so the failure is visible in the data
even when it is invisible in the interface. **Nothing currently reads those rows at request time**
to annotate the reply, which is the smallest available fix and is not implemented.

---

## Why this is shaped like a dataset

`commute_logs` is laid out to hold (features, action, outcome) per commute:

| Role | Columns |
|---|---|
| Features | `day_of_week`, `is_holiday`, `target_arrival_time`, `weather_condition`, `rain_prob`, `temp`, `gmaps_traffic_duration`, `tdx_bus_eta` |
| Action | `suggested_departure_time`, `suggested_transport` |
| Outcome | `actual_departure_time`, `actual_transport`, `actual_arrival_time`, `is_late` |

Everything the rules consult at decision time appears in the feature set — including `temp`, which no
rule uses, included because it was cheap and might matter later.

**No row has ever been written.** `CommuteLog` is never constructed anywhere in `backend/`
([`known-issues.md` C-9](known-issues.md#c-9commute_logs-has-no-producer)). Everything below
describes what the schema is for, not what it contains.

The obvious supervised formulation is: given the features, predict the buffer that would have
produced `is_late = false` with the least excess waiting. That is a regression on one number, with
the current rule table as the baseline to beat.

**Three reasons it is not straightforward.**

Outcomes are self-reported. `actual_departure_time` comes from a tap; `is_late` is the user's own
judgement against a target they chose. Neither is observed.

Missingness is not random. A user who forgets to tap produces a NULL, not a negative example, and
the mornings most likely to go unrecorded are the rushed ones — precisely the ones a lateness model
needs.

Actions were never randomised. Every row records what this policy chose, so the data supports
evaluating counterfactuals only under assumptions nothing here establishes.

A fourth reason sits before all of them: there is no data. The features are computed on every plan
and discarded, the action is rendered into a message and discarded, and the outcome signal reaches
`mark_departed_for_today()` and is used only to stop the scheduler. Joining those three write points
is the smallest change that would turn this section from a design into a dataset.

These are stated because they are the actual research questions, not caveats attached to a result.

---

**Source** `backend/app/service.py`, `backend/app/weather.py` @ `80ee635` — verified line by line
