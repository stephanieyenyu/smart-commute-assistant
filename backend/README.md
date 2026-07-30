# Smart Commute Assistant

LINE 智慧通勤助理是一個以 FastAPI 為後端核心的個人化通勤決策系統。第一階段目標是完成單人 MVP：整合交通、天氣與 LINE Bot，透過 rule-based decision engine 產生出門時間、交通方式與遲到風險提示。

## Current MVP scope

- FastAPI backend skeleton
- PostgreSQL data model prepared for multi-user SaaS expansion
- Redis-ready integration layer for caching and fallback
- LINE webhook entry point
- Commute decision engine with shared route formatter
- API health logs and commute plan logs prepared for ML feedback
- Nightly brief (21:00 Asia/Taipei, tomorrow's commute preview) and departure reminder scheduler (1hr/5min pre-departure alerts with commute time + weather, "已出門" confirmation)
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

LINE command:

```text
取得 Dashboard 連結
```

家庭共用 Dashboard：

```text
家庭成員管理
建立家庭
取得家庭邀請碼
加入家庭 family-1
設定我的名稱 小明
取得家庭 Dashboard 連結
```

排程設定：

```text
查看排程設定
排程平日
排程每天
排程週末
自訂日曆排程
```

`自訂日曆排程` 支援輸入 `週一週三週五`、`週二週四`、`1,3,5` 這類固定啟用日。若只是某一天臨時休息或臨時啟用，傳送 `查看排程設定` 後使用 LINE 日期按鈕選擇休息日或啟用日。

## Physical computer dashboard mode

This mode is for a small computer, old laptop, Windows mini PC, or macOS device connected to an external monitor. The device only needs to open the dashboard URL in a browser; the backend and LINE Bot still run on Render.

1. In LINE, send `取得家庭 Dashboard 連結`.
2. Open the URL on the computer connected to the external monitor.
3. Keep the browser visible on that screen.

### Full-screen / Kiosk

Windows quick test:

```text
Press F11 in Chrome or Edge.
```

Windows Chrome kiosk shortcut:

```text
"C:\Program Files\Google\Chrome\Application\chrome.exe" --kiosk "https://your-render-url/api/v1/dashboard/household/family-1/view"
```

Windows Edge kiosk shortcut:

```text
"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" --kiosk "https://your-render-url/api/v1/dashboard/household/family-1/view" --edge-kiosk-type=fullscreen
```

macOS Chrome kiosk command:

```bash
open -na "Google Chrome" --args --kiosk "https://your-render-url/api/v1/dashboard/household/family-1/view"
```

### Open dashboard automatically after boot

Windows:

1. Create a Chrome or Edge shortcut using the kiosk target above.
2. Press `Win + R`.
3. Type `shell:startup`.
4. Move the shortcut into that Startup folder.
5. Restart the computer and confirm the dashboard opens automatically.

macOS:

1. Open Automator.
2. Create a new Application.
3. Add a `Run Shell Script` action.
4. Paste the macOS Chrome kiosk command above.
5. Save the app, for example `Commute Dashboard.app`.
6. Open `System Settings > General > Login Items`.
7. Add `Commute Dashboard.app`.
8. Restart the Mac and confirm the dashboard opens automatically.

## Regression and stability tests

Phase 0-6 tests protect the current LINE commute advice, reminder behavior, scheduler ownership, API fallback logging, schema alignment, formatter extraction, proactive notification jobs, dashboard status thresholds, and the external-monitor dashboard view.

```bash
python -m unittest discover -s tests -v
```

## MVP next steps

1. Add feedback collection for actual arrival time and ML shadow-mode prediction.
2. Harden multi-user settings, auth, and dynamic worker scheduling.
3. Optionally replace the server-rendered dashboard view with a dedicated Next.js frontend if a separate frontend service is needed.
