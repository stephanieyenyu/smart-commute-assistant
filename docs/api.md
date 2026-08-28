# API Reference

**Source** `backend/app/main.py`, `webhook.py`, `liff_routes.py`, `dashboard_ws.py`, `family.py`
**Totals** 34 mounted HTTP routes · 3 WebSocket endpoints · 25 LINE command keys · 2 postback
actions · 2 scheduled jobs · 6 outbound provider endpoints
**Not mounted** 1 route in `liff_routes.py:api_router` — see
[Unreachable routes](#unreachable-routes)

Routes are grouped by who calls them rather than by URL. Three different callers reach paths under
`/api/`, with three different authentication situations; sorting by URL would hide that.

---

## Authentication overview

| Caller | Mechanism | Routes |
|---|---|---|
| LINE Platform | `X-Line-Signature` HMAC verification via `WebhookParser` | 1 |
| Resident (LIFF in LINE) | **none** — `userId` taken from the request body | 7 |
| Browser (dashboard) | **none** — `user_id` in the path | 8 |
| WebSocket clients | **none** — `user_id` in the path | 2 |
| Family group members | **none** — `group_id` / invite token in the path | 6 |
| Health check | none | 2 |

Only the LINE webhook verifies anything. `POST /webhooks/line` parses the body against
`LINE_CHANNEL_SECRET` and returns 400 on mismatch before any event is read; that check is correct
and complete.

Everything else identifies the caller by an identifier supplied in the request. A LINE User ID is
not a secret — it appears in every webhook payload and is passed to the LIFF page — so possession
of one is not evidence of being that user. The consequences are reviewed in
[`known-issues.md`](known-issues.md#c-2liff-and-dashboard-routes-accept-an-unverified-identifier).

This is stated plainly rather than omitted because it is the kind of thing a reader should be able
to find in the documentation rather than in the source.

---

## A. Public

| Method | Path | Purpose | Side effects |
|---|---|---|---|
| GET | `/` | Service banner | none |
| GET | `/health` | Health check; also the target of the keep-alive workflow | none |

---

## B. LINE Platform → backend

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/webhooks/line` | `X-Line-Signature` | Single entry point for every LINE event |

Dispatches on event type. `message` events are normalised and matched against `COMMAND_ALIASES`;
`postback` events are matched against two action strings. See [section G](#g-line-conversation-interface).

---

## C. Resident-facing (LIFF)

| Method | Path | Purpose | Effect |
|---|---|---|---|
| GET | `/liff/schedule` | Schedule form (HTML) | — |
| POST | `/liff/schedule/submit` | Submit a schedule from the LIFF form | creates or updates `commute_schedules`; pushes a confirmation |
| POST | `/api/schedule/submit` | Same handler, alternate path | as above |
| POST | `/api/schedule/submit/` | Trailing-slash duplicate | as above |
| POST | `/api/schedule/add` | Add a schedule | inserts `commute_schedules` |
| POST | `/api/schedule/add/` | Trailing-slash duplicate | as above |
| GET | `/api/schedule` | List a user's schedules | read-only |
| DELETE | `/api/schedule` | Delete by body | removes `commute_schedules` |
| DELETE | `/api/schedule/{schedule_id}` | Delete by path | removes `commute_schedules` |
| POST | `/api/schedule/delete` | Delete by POST | removes `commute_schedules` |

**Six of these ten are duplicate spellings of two operations.** Trailing-slash variants are
registered explicitly rather than relying on `redirect_slashes`, and deletion is reachable three
ways. This accumulated while the LIFF front end was changing; nothing distinguishes the variants.
Recorded in [`known-issues.md`](known-issues.md#d-4duplicate-route-spellings).

`POST /api/schedule/add` is declared **twice** — once on `liff_routes.api_router`, which is never
mounted, and once directly on `app` in `main.py`. The `main.py` one is live.

---

## D. Dashboard (browser)

| Method | Path | Purpose | Side effects |
|---|---|---|---|
| GET | `/dashboard` | Personal dashboard page | — |
| GET | `/dashboard/family` | Family dashboard page | — |
| GET | `/family-dashboard` | Alternate spelling of the above | — |
| GET | `/api/dashboard/status` | Live commute status for the dashboard | read-only |
| GET | `/api/commute-status` | Commute status, separate handler | read-only |
| GET | `/api/history/{userId}` | `commute_logs` history | read-only |
| GET | `/api/alert/status/{user_id}` | Whether a departure alert is pending | read-only |
| POST | `/api/alert/acknowledge/{user_id}` | Dismiss the departure alert | writes `alert_status = 'acknowledged'` |

`app.mount("/dashboard", StaticFiles(..., html=True))` serves the static bundle under
`/dashboard/*`. It is registered at the bottom of `main.py`, after every route decorator, because
Starlette matches in registration order: mounted earlier, it claimed every path below `/dashboard/`
and `GET /dashboard/family` returned 404 while the mount looked for a file named `family`.

Verified with `TestClient`: `/dashboard`, `/dashboard/family` and `/family-dashboard` all return 200
from their handlers, and the static bundle still serves under `/dashboard/`. Recorded as
[D-7](known-issues.md#d-7dashboardfamily-returns-404).

---

## E. WebSocket

| Path | Purpose |
|---|---|
| `/ws/dashboard/{user_id}` | Departure alerts pushed from the scheduler; drives the dashboard voice prompt |
| `/ws/{user_id}` | Per-user status channel |

`trigger_voice_alert()` in `dashboard_ws.py` is called by `_send_departure_question()` in the
scheduler. Its failure is caught and logged separately from the LINE push, so a disconnected
dashboard cannot suppress the LINE reminder.

---

## F. Family groups

Prefix `/api/family`, from `family.py`.

| Method | Path | Purpose | Effect |
|---|---|---|---|
| POST | `/api/family/create` | Create a group | inserts `family_groups` with an `invite_token` |
| GET | `/api/family/invite/{group_id}` | Fetch the invite link | read-only |
| POST | `/api/family/join` | Join by token | inserts `family_members` |
| GET | `/api/family/dashboard/{group_id}` | Group status view | read-only |
| GET | `/api/family/my-group` | The caller's group | read-only |
| PATCH | `/api/family/member/{member_id}/nickname` | Rename a member | updates `family_members` |

This is one of two grouping mechanisms in the schema. The other — `households` +
`users.household_id` — is reachable only through the unmounted dashboard router. See
[`database.md`](database.md#households-family_groups-family_members-commute_destinations).

---

## G. LINE conversation interface

Not HTTP routes. Everything arrives through `POST /webhooks/line`.

### Text commands

25 keys in `COMMAND_ALIASES`, each mapping to a set of accepted spellings — 60-odd surface strings
in total, since most commands accept traditional and simplified variants and an English form.

| Group | Keys |
|---|---|
| Plan queries | `today_commute`, `tomorrow_departure`, `view_settings` |
| Arrival time | `edit_today_arrival`, `edit_tomorrow_arrival` |
| Transport mode for today | `set_mode_auto`, `set_mode_shortest`, `set_mode_bus`, `set_mode_metro`, `set_mode_bus_to_metro` |
| Reminders | `enable_reminder`, `disable_reminder`, `view_reminder_setting` |
| Schedules | `add_schedule`, `weekly_schedule`, `edit_schedule`, `delete_schedule` |
| Dashboard links | `personal_dashboard_link`, `family_dashboard_link`, `board_management_help` |
| Household | `join_household` |
| Departure | `departed` |
| Meta | `system_settings`, `basic_settings`, `help`, `reset` |

**`departed` is the outcome-writing path.** The `✅ 已出門` Quick Reply on the departure question
sends the literal text `已出門`, which resolves through this table and writes
`commute_overrides.departed_at`. The entire outcome half of `commute_logs` depends on this one tap.

**`set_mode_bus_to_metro` is accepted but not implemented.** It writes
`transport_mode_override = 'bus_to_metro'`, and `choose_commute_option_with_override()` returns the
unrestricted Google option for that value with a comment saying so. The user is told the mode was
applied. Recorded in
[`known-issues.md`](known-issues.md#c-5bus_to_metro-is-accepted-but-returns-the-default).

### Postback actions

| Action | Trigger | Effect |
|---|---|---|
| `action=set_today_arrival_time` | Datetime picker, mode `time` | writes `commute_overrides.target_arrival_time` for today, then re-freezes the plan |
| `action=set_tomorrow_arrival_time` | Datetime picker, mode `time` | same, for tomorrow |

Only two postback actions exist. Almost all interaction is text-driven — a broad but shallow LINE
integration, worth stating as a shape rather than counting as a feature.

---

## H. Backend → providers (6 endpoints, 17 call sites)

All wrapped in `safe_call(coro, timeout_seconds)` and recorded by `log_api_health()`.

| Label | Provider call | Timeout | Called from |
|---|---|---|---|
| `google.routes.transit` | Routes / Directions, `allowed_travel_modes` varies | 4.2 s | `choose_commute_option_with_override` |
| `google.routes.walk` | Walking leg duration | 4.2 s | `maps_client` |
| `google.geocode` | Address → coordinates | — | schedule creation, profile setup |
| `tdx.bus.auth` + stop/ETA queries | Nearest stops, live arrival | 2.5 s | `get_bus_realtime_snapshot` |
| `tdx.metro.auth` + station queries | Nearest station, walk time | 3.5 s | `get_metro_snapshot` |
| `cwa.weather.city` | City forecast → rain probability | 2.2 s | `get_commute_weather` |

In `shortest` mode, `google.routes.transit` is called three times concurrently with different
`allowed_travel_modes` constraints. See
[`decision-engine.md`](decision-engine.md#why-shortest-needs-three-calls).

---

## I. Scheduled jobs

Two, both APScheduler, both in-process.

| Job | Trigger | Effect |
|---|---|---|
| `check_and_send_departure_reminders` | interval, 30 s | Freezes missing plans; evaluates three trigger windows per active override; pushes reminders |
| nightly brief | cron, 21:00 `Asia/Taipei` | Pushes tomorrow's plan; guarded by `nightly_brief_sent_at` |

The three reminder stages are branches inside the first job, not separate jobs. See
[`state-machines.md`](state-machines.md).

---

## Unreachable routes

**`app/dashboard.py` is now mounted.** It declares `APIRouter(prefix="/api/v1/dashboard")` with nine
routes and was, until recently, never passed to `include_router` — every path below returned 404 in
the deployed service:

```
GET       /api/v1/dashboard/view/{user_id}
GET       /api/v1/dashboard/status/{user_id}
GET       /api/v1/dashboard/household/{household_id}/view
GET       /api/v1/dashboard/household/{household_id}/status
POST      /api/v1/dashboard/departure-check/{user_id}
POST      /api/v1/dashboard/undelete-schedule/{user_id}/{template_id}
POST      /api/v1/dashboard/undelete-destination/{user_id}/{destination_id}
WEBSOCKET /api/v1/dashboard/ws/{user_id}
WEBSOCKET /api/v1/dashboard/household/{household_id}/ws
```

These carry the household queue — `primary`, `queue_position`, `queue_members` — the WebSocket voice
channel, and the sleep/wake poll tiers. `main.py`'s own dashboard routes have none of those. The
paths do not collide, so mounting the router changed nothing that previously worked; it made a
feature set reachable that had never been served.

**`app/liff_routes.py:api_router` is still unmounted.** Its single route,
`POST /api/schedule/add`, is declared again in `main.py`, so the operation works — but not through
this router. This is the only remaining unreachable route.

---

## Caveats

**No route enforces that the caller is the user they name.** Section C and section D both take an
identifier from the request. See
[`known-issues.md`](known-issues.md#c-2liff-and-dashboard-routes-accept-an-unverified-identifier).

**Ten routes cover four operations** in section C, including three deletion paths and two
trailing-slash duplicates.

**Nothing is versioned.** The only `/api/v1` prefix in the codebase belongs to the router that is
not mounted.

---

**Source** `backend/app/` @ `80ee635` — route inventory generated by
`collect_repo_stats.py --routes` and verified against `include_router` call sites
