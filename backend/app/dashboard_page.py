def render_dashboard_html(user_id: int) -> str:
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Smart Commute Dashboard</title>
  <style>
    :root {{
      --bg: #090909;
      --text: #f7f7f2;
      --muted: #b9b9ae;
      --safe: #1fb86a;
      --warning: #f2b84b;
      --urgent: #e84b4b;
      --degraded: #8f969e;
      --error: #b84fd1;
      --surface: #161714;
      --line: #30322b;
    }}

    * {{
      box-sizing: border-box;
    }}

    body {{
      margin: 0;
      min-height: 100vh;
      background: var(--bg);
      color: var(--text);
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      letter-spacing: 0;
      overflow: hidden;
    }}

    .screen {{
      min-height: 100vh;
      display: grid;
      grid-template-rows: auto 1fr auto;
      transition: background-color 180ms ease;
    }}

    .state-safe {{
      --accent: var(--safe);
      --accent-soft: rgba(31, 184, 106, 0.18);
    }}

    .state-warning {{
      --accent: var(--warning);
      --accent-soft: rgba(242, 184, 75, 0.2);
    }}

    .state-urgent {{
      --accent: var(--urgent);
      --accent-soft: rgba(232, 75, 75, 0.24);
    }}

    .state-degraded {{
      --accent: var(--degraded);
      --accent-soft: rgba(143, 150, 158, 0.18);
    }}

    .state-error {{
      --accent: var(--error);
      --accent-soft: rgba(184, 79, 209, 0.2);
    }}

    .topbar {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 24px;
      padding: 26px 42px;
      border-bottom: 1px solid var(--line);
      background: linear-gradient(90deg, var(--accent-soft), transparent 70%);
    }}

    .brand {{
      display: flex;
      align-items: center;
      gap: 14px;
      min-width: 0;
    }}

    .status-dot {{
      width: 18px;
      height: 18px;
      border-radius: 50%;
      background: var(--accent);
      box-shadow: 0 0 24px var(--accent);
      flex: 0 0 auto;
    }}

    .title {{
      font-size: clamp(20px, 2vw, 30px);
      font-weight: 700;
      white-space: nowrap;
    }}

    .clock {{
      font-size: clamp(30px, 5vw, 68px);
      font-weight: 800;
      line-height: 1;
      font-variant-numeric: tabular-nums;
    }}

    .main {{
      display: grid;
      grid-template-columns: minmax(300px, 0.85fr) minmax(360px, 1.15fr);
      gap: 34px;
      padding: 42px;
      min-height: 0;
    }}

    .hero {{
      display: flex;
      flex-direction: column;
      justify-content: center;
      gap: 26px;
      min-width: 0;
    }}

    .state-label {{
      color: var(--accent);
      font-size: clamp(26px, 4vw, 60px);
      font-weight: 850;
      line-height: 1;
    }}

    .countdown {{
      font-size: clamp(58px, 11vw, 168px);
      font-weight: 900;
      line-height: 0.95;
      font-variant-numeric: tabular-nums;
    }}

    .countdown-caption {{
      color: var(--muted);
      font-size: clamp(18px, 2vw, 30px);
    }}

    .details {{
      display: grid;
      grid-template-rows: repeat(4, minmax(0, auto));
      align-content: center;
      gap: 18px;
      min-width: 0;
    }}

    .band {{
      border-left: 6px solid var(--accent);
      background: var(--surface);
      padding: 20px 24px;
      min-width: 0;
    }}

    .label {{
      color: var(--muted);
      font-size: clamp(14px, 1.5vw, 22px);
      margin-bottom: 8px;
    }}

    .value {{
      font-size: clamp(24px, 3vw, 44px);
      font-weight: 760;
      line-height: 1.18;
      overflow-wrap: anywhere;
    }}

    .transport {{
      white-space: pre-line;
      font-size: clamp(20px, 2.3vw, 34px);
      line-height: 1.3;
    }}

    .footer {{
      display: flex;
      justify-content: space-between;
      gap: 18px;
      padding: 20px 42px 28px;
      color: var(--muted);
      border-top: 1px solid var(--line);
      font-size: clamp(14px, 1.4vw, 20px);
    }}

    @keyframes urgentPulse {{
      0%, 100% {{ background-color: #090909; }}
      50% {{ background-color: #2b0808; }}
    }}

    .state-urgent {{
      animation: urgentPulse 1.8s ease-in-out infinite;
    }}

    @media (max-width: 820px) {{
      body {{
        overflow: auto;
      }}

      .screen {{
        min-height: 100vh;
      }}

      .topbar, .footer {{
        padding-left: 22px;
        padding-right: 22px;
      }}

      .main {{
        grid-template-columns: 1fr;
        padding: 26px 22px;
      }}

      .clock {{
        font-size: 38px;
      }}
    }}
  </style>
</head>
<body>
  <main id="screen" class="screen state-error">
    <header class="topbar">
      <div class="brand">
        <span class="status-dot" aria-hidden="true"></span>
        <div class="title">Smart Commute Dashboard</div>
      </div>
      <div id="clock" class="clock">--:--:--</div>
    </header>

    <section class="main">
      <div class="hero">
        <div id="stateLabel" class="state-label">載入中</div>
        <div id="countdown" class="countdown">--</div>
        <div id="countdownCaption" class="countdown-caption">正在取得通勤狀態</div>
      </div>

      <div class="details">
        <section class="band">
          <div class="label">建議出門</div>
          <div id="departure" class="value">--:--</div>
        </section>
        <section class="band">
          <div class="label">目標抵達</div>
          <div id="arrival" class="value">--:--</div>
        </section>
        <section class="band">
          <div class="label">通勤方式</div>
          <div id="transport" class="value transport">尚無資料</div>
        </section>
        <section class="band">
          <div class="label">天氣</div>
          <div id="weather" class="value">--</div>
        </section>
      </div>
    </section>

    <footer class="footer">
      <span id="connection">連線中</span>
      <span id="updatedAt">尚未更新</span>
    </footer>
  </main>

  <script>
    const userId = {int(user_id)};
    const screen = document.getElementById("screen");
    const stateLabel = document.getElementById("stateLabel");
    const countdown = document.getElementById("countdown");
    const countdownCaption = document.getElementById("countdownCaption");
    const departure = document.getElementById("departure");
    const arrival = document.getElementById("arrival");
    const transport = document.getElementById("transport");
    const weather = document.getElementById("weather");
    const connection = document.getElementById("connection");
    const updatedAt = document.getElementById("updatedAt");
    const clock = document.getElementById("clock");

    const stateLabels = {{
      safe: "正常",
      warning: "準備出門",
      urgent: "立刻出門",
      degraded: "離線預估",
      error: "需要設定"
    }};

    let latestPayload = null;
    let ws = null;

    function pad(value) {{
      return String(value).padStart(2, "0");
    }}

    function updateClock() {{
      const now = new Date();
      clock.textContent = `${{pad(now.getHours())}}:${{pad(now.getMinutes())}}:${{pad(now.getSeconds())}}`;
    }}

    function formatCountdown(seconds) {{
      if (seconds === null || seconds === undefined) return "--";
      if (seconds <= 0) return "NOW";
      const totalMinutes = Math.ceil(seconds / 60);
      const hours = Math.floor(totalMinutes / 60);
      const minutes = totalMinutes % 60;
      return hours > 0 ? `${{hours}}:${{pad(minutes)}}` : `${{minutes}} 分`;
    }}

    function formatWeather(info) {{
      if (!info) return "--";
      const text = info.weather_text || "未知";
      const temp = info.temperature !== undefined && info.temperature !== null
        ? `${{info.temperature}}°C`
        : (info.temperature_min !== undefined && info.temperature_max !== undefined
          ? `${{info.temperature_min}}-${{info.temperature_max}}°C`
          : "");
      const pop = info.pop !== undefined && info.pop !== null ? `降雨 ${{info.pop}}%` : "";
      return [text, temp, pop].filter(Boolean).join("，");
    }}

    function render(payload) {{
      latestPayload = payload;
      const state = payload.state || "error";
      screen.className = `screen state-${{state}}`;
      stateLabel.textContent = stateLabels[state] || stateLabels.error;

      if (!payload.ok) {{
        countdown.textContent = "--";
        countdownCaption.textContent = payload.next_step ? "請先完成 LINE 初始設定" : "暫時無法建立通勤狀態";
        departure.textContent = "--:--";
        arrival.textContent = "--:--";
        transport.textContent = payload.reason || "尚無資料";
        weather.textContent = "--";
        updatedAt.textContent = "更新失敗";
        return;
      }}

      countdown.textContent = formatCountdown(payload.seconds_until_departure);
      countdownCaption.textContent = payload.seconds_until_departure <= 0 ? "已到建議出門時間" : "距離建議出門";
      departure.textContent = payload.departure_time || "--:--";
      arrival.textContent = payload.target_arrival_time || "--:--";
      transport.textContent = payload.transport_line || "尚無通勤方式";
      weather.textContent = formatWeather(payload.weather);
      updatedAt.textContent = payload.updated_at ? `更新 ${{new Date(payload.updated_at).toLocaleTimeString("zh-TW", {{ hour12: false }})}}` : "尚未更新";
    }}

    async function fetchStatus() {{
      try {{
        const response = await fetch(`/api/v1/dashboard/status/${{userId}}`, {{ cache: "no-store" }});
        render(await response.json());
        connection.textContent = ws && ws.readyState === WebSocket.OPEN ? "WebSocket 即時更新" : "REST 輪詢更新";
      }} catch (error) {{
        connection.textContent = "連線中斷，正在重試";
      }}
    }}

    function connectWebSocket() {{
      const protocol = window.location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(`${{protocol}}://${{window.location.host}}/api/v1/dashboard/ws/${{userId}}`);
      ws.onopen = () => {{
        connection.textContent = "WebSocket 即時更新";
      }};
      ws.onmessage = (event) => {{
        render(JSON.parse(event.data));
      }};
      ws.onclose = () => {{
        connection.textContent = "WebSocket 已斷線，使用輪詢";
        setTimeout(connectWebSocket, 5000);
      }};
      ws.onerror = () => {{
        connection.textContent = "WebSocket 異常，使用輪詢";
        ws.close();
      }};
    }}

    setInterval(updateClock, 1000);
    setInterval(fetchStatus, 30000);
    updateClock();
    fetchStatus();
    connectWebSocket();
  </script>
</body>
</html>"""
