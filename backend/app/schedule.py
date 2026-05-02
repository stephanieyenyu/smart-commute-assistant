from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import HTMLResponse

from app.commute_schedule import WEEKDAY_NAMES, normalize_active_weekdays, schedule_label
from app.crud import get_profile, set_active_weekdays, set_pending_field
from app.db import SessionLocal


router = APIRouter(prefix="/api/v1/schedule", tags=["schedule"])


def _coerce_weekdays(value) -> list[int]:
    weekdays = normalize_active_weekdays(value)
    return weekdays


def render_weekly_schedule_html(user_id: int, active_weekdays: list[int]) -> str:
    day_buttons = "\n".join(
        f'<button class="day" data-day="{index}" type="button">{name}</button>'
        for index, name in enumerate(WEEKDAY_NAMES)
    )
    active_js = "[" + ",".join(str(day) for day in active_weekdays) + "]"
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>通勤排程</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f7f7f2;
      --text: #20231f;
      --muted: #6f756c;
      --line: #d8dbd2;
      --accent: #146c5f;
      --accent-soft: #dff1eb;
      --surface: #ffffff;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100dvh;
      background: var(--bg);
      color: var(--text);
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      letter-spacing: 0;
    }}
    main {{
      width: min(720px, 100%);
      margin: 0 auto;
      padding: 28px 18px 36px;
    }}
    h1 {{
      margin: 0 0 8px;
      font-size: 28px;
      line-height: 1.2;
    }}
    p {{
      margin: 0;
      color: var(--muted);
      font-size: 16px;
      line-height: 1.6;
    }}
    .repeat-panel {{
      margin-top: 26px;
      padding: 20px 0;
      border-top: 1px solid var(--line);
      border-bottom: 1px solid var(--line);
    }}
    .days {{
      display: flex;
      gap: 12px;
      overflow-x: auto;
      padding: 8px 18px 16px;
      margin: 0 -18px;
      scroll-snap-type: x proximity;
      -webkit-overflow-scrolling: touch;
    }}
    .day {{
      flex: 0 0 92px;
      min-height: 92px;
      border-radius: 28px;
      border: 1px solid var(--line);
      background: var(--surface);
      color: var(--text);
      font-size: 20px;
      font-weight: 700;
      scroll-snap-align: center;
      box-shadow: 0 10px 22px rgba(20, 25, 20, 0.08);
    }}
    .day.active {{
      background: var(--accent);
      border-color: var(--accent);
      color: #fff;
    }}
    .presets {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 10px;
      margin-top: 14px;
    }}
    .preset, .save {{
      min-height: 48px;
      border-radius: 12px;
      border: 1px solid var(--line);
      background: var(--surface);
      color: var(--text);
      font-size: 16px;
      font-weight: 650;
    }}
    .save {{
      width: 100%;
      margin-top: 24px;
      background: var(--accent);
      border-color: var(--accent);
      color: #fff;
      font-size: 18px;
    }}
    .status {{
      min-height: 28px;
      margin-top: 14px;
      font-size: 16px;
      color: var(--accent);
      font-weight: 650;
    }}
    @media (max-width: 520px) {{
      main {{ padding-top: 22px; }}
      h1 {{ font-size: 25px; }}
      .day {{
        flex-basis: 82px;
        min-height: 82px;
        border-radius: 24px;
        font-size: 18px;
      }}
      .presets {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    }}
  </style>
</head>
<body>
  <main>
    <h1>重複提醒日</h1>
    <p>像鬧鐘 Repeat 一樣，左右滑動並點選固定要啟用通勤提醒的星期。選好後按下儲存即可。</p>
    <section class="repeat-panel" aria-label="選擇星期">
      <div class="days" id="days">
        {day_buttons}
      </div>
      <div class="presets">
        <button class="preset" type="button" data-preset="0,1,2,3,4">平日</button>
        <button class="preset" type="button" data-preset="5,6">週末</button>
        <button class="preset" type="button" data-preset="0,1,2,3,4,5,6">每天</button>
        <button class="preset" type="button" data-preset="">全休</button>
      </div>
    </section>
    <button class="save" id="save" type="button">儲存排程</button>
    <div class="status" id="status"></div>
  </main>
  <script>
    const userId = {int(user_id)};
    const selected = new Set({active_js});
    const status = document.getElementById("status");
    const buttons = Array.from(document.querySelectorAll(".day"));

    function renderDays() {{
      buttons.forEach((button) => {{
        const day = Number(button.dataset.day);
        button.classList.toggle("active", selected.has(day));
      }});
    }}

    buttons.forEach((button) => {{
      button.addEventListener("click", () => {{
        const day = Number(button.dataset.day);
        if (selected.has(day)) selected.delete(day);
        else selected.add(day);
        renderDays();
      }});
    }});

    document.querySelectorAll(".preset").forEach((button) => {{
      button.addEventListener("click", () => {{
        selected.clear();
        const preset = button.dataset.preset;
        if (preset) preset.split(",").forEach((day) => selected.add(Number(day)));
        renderDays();
      }});
    }});

    document.getElementById("save").addEventListener("click", async () => {{
      status.textContent = "儲存中...";
      try {{
        const active_weekdays = Array.from(selected).sort((a, b) => a - b);
        const response = await fetch(`/api/v1/schedule/weekly/${{userId}}`, {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify({{ active_weekdays }})
        }});
        const payload = await response.json();
        if (!response.ok || !payload.ok) throw new Error(payload.detail || "save failed");
        status.textContent = `已儲存：${{payload.label}}。可以回 LINE 查看排程設定。`;
      }} catch (error) {{
        status.textContent = "儲存失敗，請稍後再試。";
      }}
    }});

    renderDays();
  </script>
</body>
</html>"""


@router.get("/weekly/{user_id}/view", response_class=HTMLResponse)
async def weekly_schedule_view(user_id: int):
    db = SessionLocal()
    try:
        profile = get_profile(db, user_id)
        active_weekdays = normalize_active_weekdays(getattr(profile, "active_weekdays", None))
        return HTMLResponse(render_weekly_schedule_html(user_id, active_weekdays))
    finally:
        db.close()


@router.post("/weekly/{user_id}")
async def update_weekly_schedule(user_id: int, payload: dict = Body(default=None)):
    payload = payload or {}
    raw_weekdays = payload.get("active_weekdays")
    if raw_weekdays is None or not isinstance(raw_weekdays, list):
        raise HTTPException(status_code=400, detail="active_weekdays must be a list")

    weekdays = _coerce_weekdays(raw_weekdays)
    db = SessionLocal()
    try:
        profile = set_active_weekdays(db, user_id, weekdays)
        set_pending_field(db, user_id, None)
        return {
            "ok": True,
            "active_weekdays": normalize_active_weekdays(profile.active_weekdays),
            "label": schedule_label(profile.active_weekdays),
        }
    finally:
        db.close()
