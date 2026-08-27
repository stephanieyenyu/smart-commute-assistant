# Architecture

One FastAPI service on a single Render dyno, one PostgreSQL database, four third-party
providers. There is no second service and no message broker. I kept it that way because the
system has one writer and one workload; the interesting failure surface here is not internal
coordination but the boundary with providers I do not control, and adding services would have
moved effort away from that boundary rather than toward it. The cost is structural and shapes
most of what follows: the scheduler lives inside the web process, so horizontal scaling is not
available without changing the design.

![System architecture](images/architecture.png)

*Editable source: [`diagrams/system-architecture.drawio`](diagrams/)*

## Request lifecycle

Two paths reach the decision engine and they converge immediately.

The **conversational path** starts at `POST /webhooks/line`. The body is verified against
`X-Line-Signature` by `WebhookParser`; a mismatch returns 400 before anything is parsed.
Verified text is normalised and looked up in `COMMAND_ALIASES` in `app/webhook.py` — 26 command
keys covering 60-odd surface spellings. Two postback actions carry a datetime picker payload.
Everything that needs a plan calls `_compute_today_plan()` in `app/service.py`.

The **scheduled path** starts at a 30-second APScheduler tick. It calls the same
`_compute_today_plan()`, but only when a schedule active today has no frozen plan yet. Once the
plan is frozen, later ticks read three columns and compare timestamps; no external call is made.
See [`state-machines.md`](state-machines.md).

## Provider calls are concurrent, bounded, and recorded

Four providers are queried per plan: Google Routes, Google Geocoding, TDX (bus and metro), and
the Central Weather Administration. They are independent, so they are issued as tasks before any
is awaited — under `asyncio.gather` at the outer level and `asyncio.create_task` at the inner
level. Wall-clock cost tracks the slowest call rather than their sum. In `shortest` mode this
matters directly: comparing modes honestly requires three separate Google Routes queries with
different `allowed_travel_modes` constraints, and concurrency is what makes the honest version
cost the same as returning Google's default would have.

Every call is wrapped in `safe_call(coro, timeout_seconds)`, which catches `asyncio.TimeoutError`
and every `Exception`, prints one line, and returns `None`. Nothing propagates. Each caller
therefore has to decide what a missing answer means, and those decisions are enumerated in
[`decision-engine.md`](decision-engine.md#degradation).

Every call also passes through `log_api_health()` in `app/integrations/api_health.py` — 6 endpoint
labels across 17 call sites — writing latency, status code and error message to `api_health_logs`.
This is the only place outbound calls are recorded, and it is why external reliability is
measurable at all. Every figure in [`metrics.md`](metrics.md) derives from that table rather than
from application logs, which do not survive a Render restart.

## Design principle: compute once, then only compare timestamps

The scheduler ticks every 30 seconds. If it recomputed the route each time, one user would
generate roughly 2,880 provider calls per day, and each recomputation could return a different
departure time — meaning the three reminders in a single morning could disagree with each other.

Instead the plan is computed once and written to `frozen_plan_key`, `frozen_departure_time` and
`frozen_reminder_text` on the `commute_overrides` row for that user, schedule and date. Every
later tick reads those columns. The tick becomes a pure comparison between the current time and a
stored value, which is what makes the timing invariant in
[`state-machines.md`](state-machines.md#the-invariant) checkable at all.

The accepted cost is staleness: a frozen plan does not react to traffic that develops after it was
computed. `ensure_today_reminders_prepared()` will refresh a plan that is missing, throttled by
`PREPARE_RETRY_SECONDS`, but it will not refresh one that is merely old.

## Design principle: fail loudly where the precondition is load-bearing

`safe_call` returning `None` everywhere means a provider outage produces a quieter answer rather
than an error. That is deliberate for weather and for transit snapshots — a commute suggestion
without a rain buffer is still useful.

It is not acceptable for coordinates. An address string being present does not mean geocoding
succeeded, and coordinates are the precondition for every downstream provider call. So
`_compute_today_plan()` checks all four coordinates explicitly and returns `coords_missing`
naming which side failed. This is the only branch in the pipeline where a missing external answer
produces a visible error instead of a plausible one. It exists because it once did not — see
[`known-issues.md`](known-issues.md#a-1geocoding-returned-null-coordinates-and-every-failure-was-swallowed).

## Known deviations

**Two dashboard front ends are both reachable.** `app/dashboard.py` declares
`APIRouter(prefix="/api/v1/dashboard")` with 9 routes and was, until recently, never passed to
`include_router` — so the household sequential handoff, the WebSocket voice channel and the
sleep/wake tiers were all unreachable in production. The router is now mounted. The overlapping
declarations in `main.py` were left in place, because LINE messages already link to those paths, so
the service currently serves two dashboards over the same data. See
[`known-issues.md`](known-issues.md#c-1the-dashboard-router-is-never-mounted) and
[D-5](known-issues.md#d-5two-dashboard-front-ends-are-now-both-reachable).

**`app/liff_routes.py:api_router` is also unmounted.** It declares `POST /api/schedule/add`, which
`main.py` declares again on `app`. The `main.py` version is the live one.

**The tested timing module is not the running one.** `app/reminder_timing.py` implements
`evaluate_departure_reminder()` with a four-value decision enum and is covered by tests.
`reminder_scheduler.py` does not import it; it reimplements the same comparison inline, with a
duplicated copy of `STALE_REMINDER_GRACE_SECONDS`. See
[`known-issues.md`](known-issues.md#d-1the-tested-timing-logic-is-not-the-running-timing-logic).

**Celery and Redis are declared but not deployed.** `app/celery_app.py` and `app/tasks.py` define
a beat schedule duplicating both APScheduler jobs; `render.yaml` declares no worker service.
`app/integrations/redis_cache.py` is imported by `maps_client.py` and `tdx_client.py`, but
`REDIS_URL` defaults to `redis://localhost:6379/0` and no Redis service is provisioned, so every
cache operation falls through to the in-process `_fallback_cache` — per-process and lost on restart.

## Consequence of a single process

APScheduler is started in the FastAPI `lifespan` handler, inside the web process. There is no lock
and no leader election. A second Render instance would run a second copy of the 30-second tick,
and the `*_sent_at` guards would not prevent double delivery because two ticks can read a NULL
guard before either writes it.

This is accepted at current scale, not solved. It is recorded as a limitation rather than
presented as a design, because the fix — a database advisory lock or an external scheduler — is
known and simply has not been needed.

## Related

- [State machines](state-machines.md) — the reminder progression and its timing invariant
- [Decision engine](decision-engine.md) — the rules, and what happens when a provider fails
- [Data dictionary](database.md) — all 10 tables
- [API reference](api.md) — what is actually reachable, grouped by caller
- [Known issues](known-issues.md) — verified defects and open questions
- [Metrics](metrics.md) — how each figure in the README is derived

---

**Source** `backend/app/` @ `e10e6d9` — verified line by line
