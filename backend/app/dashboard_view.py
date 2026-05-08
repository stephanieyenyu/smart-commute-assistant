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
    .dashboard-section {
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
    .countdown {
      font-weight: 850;
      color: #facc15;
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

    <section class="summary dashboard-section" aria-label="今天交通">
        <h2>今天交通</h2>
        <div class="item"><div class="label">預估出門時間</div><div class="value" id="estimatedDepartureTime">載入中</div></div>
        <div class="item"><div class="label">出門倒數</div><div class="value countdown" id="departureCountdown">載入中</div></div>
        <div class="item"><div class="label">目標抵達時間</div><div class="value" id="targetArrivalTime">載入中</div></div>
        <div class="item"><div class="label">預估通勤時間</div><div class="value" id="estimatedCommuteTime">載入中</div></div>
        <div class="item"><div class="label">完整交通方式</div><div class="value" id="fullTransport">載入中</div></div>
        <div class="item"><div class="label">當前天氣</div><div class="value" id="currentWeather">載入中</div></div>
    </section>

    <section class="summary dashboard-section" aria-label="今日排程">
        <h2 id="scheduleTitle">今日排程</h2>
        <div class="schedule-list" id="scheduleList"></div>
    </section>
  </main>
  <script>
    const params = new URLSearchParams(location.search);
    const userId = params.get("userId") || "";
    const view = params.get("view") || "personal";
    const refreshMs = 30000;
    let countdownTimer = null;
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
      const safeSchedules = schedules || [];
      if (!safeSchedules.length) {
        const row = document.createElement("div");
        row.className = "schedule-row";
        row.textContent = "沒有可顯示的今日或明日排程";
        container.append(row);
        return;
      }
      for (const schedule of safeSchedules) {
        const row = document.createElement("div");
        row.className = "schedule-row";
        const primary = document.createElement("strong");
        primary.textContent = schedule.arrivalTime || "未設定";
        const line = document.createTextNode(` 從 ${schedule.origin || "出發地"} 到 ${schedule.destination || "目的地"}`);
        const br = document.createElement("br");
        const week = document.createTextNode(schedule.weekdayText || "尚未設定");
        row.append(primary, line, br, week);
        container.append(row);
      }
    }

    function startDepartureCountdown(commute) {
      if (countdownTimer) clearInterval(countdownTimer);
      const timeText = commute && commute.estimatedDepartureTime;
      const dateText = commute && commute.targetDate;
      if (!timeText || !dateText) {
        text("departureCountdown", "尚未取得出門時間");
        return;
      }
      const target = new Date(`${dateText}T${timeText}:00+08:00`);
      const render = () => {
        const diffMs = target.getTime() - Date.now();
        if (!Number.isFinite(diffMs)) {
          text("departureCountdown", "時間格式錯誤");
          return;
        }
        if (diffMs <= 0) {
          text("departureCountdown", "已到預估出門時間");
          return;
        }
        const totalSeconds = Math.floor(diffMs / 1000);
        const hours = Math.floor(totalSeconds / 3600);
        const minutes = Math.floor((totalSeconds % 3600) / 60);
        const seconds = totalSeconds % 60;
        const minuteText = `${minutes}`.padStart(2, "0");
        const secondText = `${seconds}`.padStart(2, "0");
        text("departureCountdown", `${hours}:${minuteText}:${secondText}`);
      };
      render();
      countdownTimer = setInterval(render, 1000);
    }

    function renderCommute(commute) {
      text("estimatedDepartureTime", commute && commute.estimatedDepartureTime);
      text("targetArrivalTime", commute && commute.targetArrivalTime);
      text("estimatedCommuteTime", commute && commute.estimatedCommuteTime);
      text("fullTransport", commute && commute.fullTransport);
      text("currentWeather", commute && commute.currentWeather);
      startDepartureCountdown(commute);
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
        text("scheduleTitle", payload.schedule.displayScheduleTitle || "今日排程");
        renderMembers(payload.members);
        renderSchedules(payload.schedule.displaySchedules);
        renderCommute(payload.commute);
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
