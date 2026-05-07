def render_dashboard_html() -> str:
    return """<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="Cache-Control" content="no-store, max-age=0">
  <title>通勤提醒看板</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #101214;
      --panel: #1b1f23;
      --panel-2: #242a30;
      --text: #f4f7f8;
      --muted: #aeb8bf;
      --accent: #38bdf8;
      --good: #34d399;
      --line: #36414a;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      background: var(--bg);
      color: var(--text);
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      letter-spacing: 0;
    }
    main {
      width: min(1120px, calc(100vw - 32px));
      margin: 0 auto;
      padding: 28px 0 36px;
    }
    header {
      display: flex;
      align-items: flex-end;
      justify-content: space-between;
      gap: 20px;
      padding-bottom: 18px;
      border-bottom: 1px solid var(--line);
    }
    h1 {
      margin: 0;
      font-size: 30px;
      line-height: 1.2;
    }
    .meta {
      color: var(--muted);
      font-size: 14px;
      text-align: right;
    }
    .grid {
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(320px, 0.8fr);
      gap: 18px;
      margin-top: 20px;
    }
    section {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 18px;
    }
    .summary {
      display: grid;
      gap: 14px;
    }
    .item {
      display: grid;
      grid-template-columns: 112px minmax(0, 1fr);
      gap: 12px;
      align-items: baseline;
      padding-bottom: 12px;
      border-bottom: 1px solid rgba(255,255,255,0.08);
    }
    .item:last-child { border-bottom: 0; padding-bottom: 0; }
    .label {
      color: var(--muted);
      font-size: 14px;
    }
    .value {
      font-size: 20px;
      line-height: 1.35;
      overflow-wrap: anywhere;
    }
    .week {
      display: grid;
      gap: 8px;
    }
    .day {
      display: grid;
      grid-template-columns: 56px minmax(0, 1fr);
      gap: 12px;
      align-items: center;
      min-height: 42px;
      padding: 9px 12px;
      background: var(--panel-2);
      border-radius: 6px;
      color: var(--muted);
    }
    .day.active {
      color: var(--text);
      border-left: 4px solid var(--good);
    }
    .day-name {
      font-weight: 700;
      color: inherit;
    }
    .status {
      margin-top: 18px;
      color: var(--muted);
      font-size: 14px;
    }
    .online { color: var(--good); }
    @media (max-width: 760px) {
      main { width: min(100vw - 20px, 680px); padding-top: 18px; }
      header { display: block; }
      .meta { text-align: left; margin-top: 8px; }
      .grid { grid-template-columns: 1fr; }
      .item { grid-template-columns: 92px minmax(0, 1fr); }
      .value { font-size: 18px; }
    }
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1 id="title">通勤提醒看板</h1>
        <div class="status"><span id="connection">連線中</span></div>
      </div>
      <div class="meta">
        <div id="viewLabel">個人看板</div>
        <div id="updatedAt">尚未更新</div>
      </div>
    </header>
    <div class="grid">
      <section class="summary" aria-label="通勤設定摘要">
        <div class="item"><div class="label">出發地</div><div class="value" id="origin">載入中</div></div>
        <div class="item"><div class="label">目的地</div><div class="value" id="destination">載入中</div></div>
        <div class="item"><div class="label">到達時間</div><div class="value" id="arrivalTime">載入中</div></div>
        <div class="item"><div class="label">提醒星期</div><div class="value" id="weekdayText">載入中</div></div>
        <div class="item"><div class="label">自動提醒</div><div class="value" id="reminderEnabled">載入中</div></div>
        <div class="item"><div class="label">今天交通</div><div class="value" id="todayTransportMode">載入中</div></div>
      </section>
      <section aria-label="一週排程">
        <div class="week" id="weeklySchedule"></div>
      </section>
    </div>
  </main>
  <script>
    const params = new URLSearchParams(location.search);
    const userId = params.get("userId") || "";
    const view = params.get("view") || "personal";
    const refreshMs = 30000;
    const text = (id, value) => { document.getElementById(id).textContent = value || "尚未設定"; };

    function renderWeek(rows) {
      const container = document.getElementById("weeklySchedule");
      container.innerHTML = "";
      const safeRows = rows && rows.length ? rows : [
        { label: "週一", active: false, text: "尚未設定" },
        { label: "週二", active: false, text: "尚未設定" },
        { label: "週三", active: false, text: "尚未設定" },
        { label: "週四", active: false, text: "尚未設定" },
        { label: "週五", active: false, text: "尚未設定" },
        { label: "週六", active: false, text: "尚未設定" },
        { label: "週日", active: false, text: "尚未設定" },
      ];
      for (const row of safeRows) {
        const item = document.createElement("div");
        item.className = row.active ? "day active" : "day";
        const name = document.createElement("div");
        name.className = "day-name";
        name.textContent = row.label;
        const value = document.createElement("div");
        value.textContent = row.text;
        item.append(name, value);
        container.append(item);
      }
    }

    async function refresh() {
      if (!userId) {
        text("connection", "缺少 userId");
        return;
      }
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 5000);
      try {
        const url = `/api/dashboard/status?userId=${encodeURIComponent(userId)}&view=${encodeURIComponent(view)}`;
        const response = await fetch(url, { cache: "no-store", signal: controller.signal });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const payload = await response.json();
        text("title", payload.title || "通勤提醒看板");
        text("viewLabel", payload.viewLabel || "個人看板");
        text("origin", payload.schedule.origin);
        text("destination", payload.schedule.destination);
        text("arrivalTime", payload.schedule.arrivalTime);
        text("weekdayText", payload.schedule.weekdayText);
        text("reminderEnabled", payload.schedule.reminderEnabled ? "開啟" : "關閉");
        text("todayTransportMode", payload.schedule.todayTransportMode);
        renderWeek(payload.schedule.weeklySchedule);
        text("updatedAt", `更新：${new Date(payload.generatedAt).toLocaleString("zh-TW", { hour12: false })}`);
        const connection = document.getElementById("connection");
        connection.textContent = "連線正常";
        connection.className = "online";
      } catch (error) {
        const connection = document.getElementById("connection");
        connection.textContent = "連線重試中";
        connection.className = "";
      } finally {
        clearTimeout(timeout);
      }
    }

    refresh();
    setInterval(refresh, refreshMs);
  </script>
</body>
</html>"""
