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
      --line: #36414a;
      --green: #22c55e;
      --blue: #3b82f6;
      --orange: #f97316;
      --red: #ef4444;
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
      width: min(1180px, calc(100vw - 32px));
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
      grid-template-columns: minmax(0, 1fr) minmax(340px, 0.85fr);
      gap: 18px;
      margin-top: 20px;
    }
    section {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 18px;
    }
    h2 {
      margin: 0 0 14px;
      font-size: 18px;
      line-height: 1.3;
    }
    .summary {
      display: grid;
      gap: 12px;
    }
    .item {
      display: grid;
      grid-template-columns: 112px minmax(0, 1fr);
      gap: 12px;
      align-items: baseline;
      padding-bottom: 11px;
      border-bottom: 1px solid rgba(255,255,255,0.08);
    }
    .item:last-child { border-bottom: 0; padding-bottom: 0; }
    .label {
      color: var(--muted);
      font-size: 14px;
    }
    .value {
      font-size: 19px;
      line-height: 1.35;
      overflow-wrap: anywhere;
    }
    .members {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 12px;
      margin-top: 20px;
    }
    .member {
      min-height: 132px;
      border: 1px solid var(--line);
      border-left: 8px solid var(--green);
      border-radius: 8px;
      background: var(--panel-2);
      padding: 14px;
    }
    .member.blue { border-left-color: var(--blue); }
    .member.orange { border-left-color: var(--orange); }
    .member.red { border-left-color: var(--red); }
    .member-name {
      font-size: 18px;
      font-weight: 800;
      margin-bottom: 8px;
    }
    .member-status {
      font-size: 26px;
      font-weight: 850;
      line-height: 1.2;
    }
    .member-detail {
      margin-top: 8px;
      color: var(--muted);
      font-size: 14px;
      line-height: 1.45;
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
      border-left: 4px solid var(--green);
    }
    .day-name {
      font-weight: 700;
      color: inherit;
    }
    .schedule-list {
      display: grid;
      gap: 8px;
      margin-top: 14px;
    }
    .schedule-row {
      padding: 10px 12px;
      border: 1px solid rgba(255,255,255,0.08);
      border-radius: 6px;
      color: var(--muted);
      background: rgba(255,255,255,0.03);
    }
    .schedule-row strong {
      color: var(--text);
    }
    .status {
      margin-top: 18px;
      color: var(--muted);
      font-size: 14px;
    }
    .online { color: var(--green); }
    @media (max-width: 760px) {
      main { width: min(100vw - 20px, 680px); padding-top: 18px; }
      header { display: block; }
      .meta { text-align: left; margin-top: 8px; }
      .grid { grid-template-columns: 1fr; }
      .item { grid-template-columns: 92px minmax(0, 1fr); }
      .value { font-size: 18px; }
      .member-status { font-size: 23px; }
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

    <div class="members" id="members"></div>

    <div class="grid">
      <section class="summary" aria-label="通勤設定摘要">
        <h2>目前設定</h2>
        <div class="item"><div class="label">出發地</div><div class="value" id="origin">載入中</div></div>
        <div class="item"><div class="label">目的地</div><div class="value" id="destination">載入中</div></div>
        <div class="item"><div class="label">到達時間</div><div class="value" id="arrivalTime">載入中</div></div>
        <div class="item"><div class="label">提醒星期</div><div class="value" id="weekdayText">載入中</div></div>
        <div class="item"><div class="label">自動提醒</div><div class="value" id="reminderEnabled">載入中</div></div>
        <div class="item"><div class="label">今天交通</div><div class="value" id="todayTransportMode">載入中</div></div>
        <div class="schedule-list" id="scheduleList"></div>
      </section>
      <section aria-label="一週排程">
        <h2>一週排程</h2>
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

    function renderMembers(members) {
      const container = document.getElementById("members");
      container.innerHTML = "";
      const safeMembers = members && members.length ? members : [{
        displayName: "目前使用者",
        statusColor: "green",
        statusLabel: "休息中",
        statusReason: "尚未取得排程",
        suggestedDepartureTime: null
      }];
      for (const member of safeMembers) {
        const item = document.createElement("div");
        item.className = `member ${member.statusColor || "green"}`;
        const name = document.createElement("div");
        name.className = "member-name";
        name.textContent = member.displayName || "家庭成員";
        const status = document.createElement("div");
        status.className = "member-status";
        status.textContent = member.statusLabel || "休息中";
        const detail = document.createElement("div");
        detail.className = "member-detail";
        const departure = member.suggestedDepartureTime ? `建議出門 ${member.suggestedDepartureTime}` : "";
        detail.textContent = [member.statusReason, departure].filter(Boolean).join("｜") || "今天沒有啟用的排程";
        item.append(name, status, detail);
        container.append(item);
      }
    }

    function renderSchedules(schedules) {
      const container = document.getElementById("scheduleList");
      container.innerHTML = "";
      for (const schedule of schedules || []) {
        const row = document.createElement("div");
        row.className = "schedule-row";
        const primary = document.createElement("strong");
        primary.textContent = schedule.arrivalTime || "未設定";
        const line = document.createTextNode(` 到 ${schedule.destination || "目的地"}`);
        const br = document.createElement("br");
        const week = document.createTextNode(schedule.weekdayText || "尚未設定");
        row.append(primary, line, br, week);
        container.append(row);
      }
    }

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
        renderMembers(payload.members);
        renderSchedules(payload.schedule.schedules);
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
