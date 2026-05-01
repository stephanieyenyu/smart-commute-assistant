# Smart Commute Assistant

LINE 智慧通勤助理是一個以 FastAPI 為後端核心的個人化通勤決策系統。第一階段目標是完成單人 MVP：整合交通、天氣與 LINE Bot，透過 rule-based decision engine 產生出門時間、交通方式與遲到風險提示。

## Current MVP scope

- FastAPI backend skeleton
- PostgreSQL data model prepared for multi-user SaaS expansion
- Redis-ready integration layer for caching and fallback
- LINE webhook entry point
- Commute decision API for dashboard and testing
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

## Regression and stability tests

Phase 0/1 tests protect the current LINE commute advice, reminder behavior, scheduler ownership, and API fallback logging.

```bash
python -m unittest discover -s tests -v
```

## MVP next steps

1. Add Alembic migrations.
2. Implement TDX, Google Maps and weather clients with timeout, retry and Redis caching.
3. Replace mock commute data with real API data.
4. Add LINE Messaging API signature verification and reply/push message handling.
5. Add scheduled nightly brief and morning watchdog jobs.
