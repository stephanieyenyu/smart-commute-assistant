# Smart Commute Assistant

LINE 智慧通勤助理是一個以 FastAPI 為後端核心的個人化通勤決策系統。第一階段目標是完成單人 MVP：整合交通、天氣與 LINE Bot，透過 rule-based decision engine 產生出門時間、交通方式與遲到風險提示。

## Current MVP scope

- FastAPI backend skeleton
- PostgreSQL data model prepared for multi-user SaaS expansion
- Redis-ready integration layer for caching and fallback
- LINE webhook entry point
- Commute decision engine with shared route formatter
- API health logs and commute plan logs prepared for ML feedback
- Nightly brief, morning watchdog, and departure reminder scheduler
- Dashboard REST/WebSocket API for kiosk status displays
- Browser dashboard view for external monitors
- Docker Compose local development stack

## Architecture

```text
app/
  api/              FastAPI routes
  core/             settings and shared config
  models/           SQLAlchemy models
  schemas/          Pydantic request/response schemas
  services/         business logic and decision engine
  integrations/     external API clients
  worker/           future Celery tasks
```

## Local development

```bash
cp .env.example .env
docker compose up --build
```

FastAPI will be available at:

```text
http://localhost:8000/docs
```

Dashboard kiosk view:

```text
http://localhost:8000/api/v1/dashboard/view/{user_id}
```

## Regression and stability tests

Phase 0-6 tests protect the current LINE commute advice, reminder behavior, scheduler ownership, API fallback logging, schema alignment, formatter extraction, proactive notification jobs, dashboard status thresholds, and the external-monitor dashboard view.

```bash
python -m unittest discover -s tests -v
```

## MVP next steps

1. Add feedback collection for actual arrival time and ML shadow-mode prediction.
2. Harden multi-user settings, auth, and dynamic worker scheduling.
3. Optionally replace the server-rendered dashboard view with a dedicated Next.js frontend if a separate frontend service is needed.
