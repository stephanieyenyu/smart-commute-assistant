def render_dashboard_html(user_id: int) -> str:
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>通勤提醒看板</title>
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
      --edge-x: 42px;
      --edge-y: 24px;
      --main-gap: 32px;
      --band-pad-y: 18px;
      --band-pad-x: 22px;
      --title-size: 28px;
      --clock-size: 58px;
      --state-size: 50px;
      --countdown-size: 136px;
      --caption-size: 26px;
      --label-size: 19px;
      --value-size: 34px;
      --transport-size: 28px;
      --footer-size: 18px;
    }}

    * {{
      box-sizing: border-box;
    }}

    body {{
      margin: 0;
      min-height: 100dvh;
      background: var(--bg);
      color: var(--text);
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      letter-spacing: 0;
      overflow: hidden;
    }}

    .screen {{
      min-height: 100dvh;
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
      padding: var(--edge-y) var(--edge-x);
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
      font-size: var(--title-size);
      font-weight: 700;
      white-space: nowrap;
    }}

    .clock {{
      font-size: var(--clock-size);
      font-weight: 800;
      line-height: 1;
      font-variant-numeric: tabular-nums;
    }}

    .main {{
      display: grid;
      grid-template-columns: minmax(300px, 0.85fr) minmax(360px, 1.15fr);
      gap: var(--main-gap);
      width: min(100%, 1440px);
      margin: 0 auto;
      padding: var(--edge-x);
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
      font-size: var(--state-size);
      font-weight: 850;
      line-height: 1;
    }}

    .countdown {{
      font-size: var(--countdown-size);
      font-weight: 900;
      line-height: 0.95;
      font-variant-numeric: tabular-nums;
    }}

    .countdown-caption {{
      color: var(--muted);
      font-size: var(--caption-size);
    }}

    .details {{
      display: grid;
      grid-template-rows: repeat(4, minmax(0, auto));
      align-content: center;
      gap: 16px;
      min-width: 0;
    }}

    .band {{
      border-left: 6px solid var(--accent);
      background: var(--surface);
      padding: var(--band-pad-y) var(--band-pad-x);
      min-width: 0;
    }}

    .label {{
      color: var(--muted);
      font-size: var(--label-size);
      margin-bottom: 8px;
    }}

    .value {{
      font-size: var(--value-size);
      font-weight: 760;
      line-height: 1.18;
      overflow-wrap: anywhere;
    }}

    .transport {{
      white-space: pre-line;
      font-size: var(--transport-size);
      line-height: 1.3;
    }}

    .footer {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 18px;
      padding: 18px var(--edge-x) 24px;
      color: var(--muted);
      border-top: 1px solid var(--line);
      font-size: var(--footer-size);
    }}

    .sound-button {{
      appearance: none;
      border: 1px solid var(--line);
      background: var(--surface);
      color: var(--text);
      padding: 10px 14px;
      font: inherit;
      border-radius: 6px;
      cursor: pointer;
      white-space: nowrap;
    }}

    .sound-button.enabled {{
      border-color: var(--accent);
      color: var(--accent);
    }}

    @keyframes urgentPulse {{
      0%, 100% {{ background-color: #090909; }}
      50% {{ background-color: #2b0808; }}
    }}

    .state-urgent {{
      animation: urgentPulse 1.8s ease-in-out infinite;
    }}

    @media (min-width: 1441px) {{
      :root {{
        --edge-x: 54px;
      }}
    }}

    @media (max-width: 1100px) {{
      :root {{
        --edge-x: 30px;
        --edge-y: 20px;
        --main-gap: 24px;
        --title-size: 24px;
        --clock-size: 48px;
        --state-size: 42px;
        --countdown-size: 108px;
        --caption-size: 22px;
        --label-size: 17px;
        --value-size: 29px;
        --transport-size: 24px;
        --footer-size: 16px;
      }}

      .main {{
        grid-template-columns: minmax(260px, 0.82fr) minmax(320px, 1.18fr);
      }}
    }}

    @media (max-width: 820px) {{
      :root {{
        --edge-x: 22px;
        --edge-y: 18px;
        --main-gap: 22px;
        --band-pad-y: 16px;
        --band-pad-x: 18px;
        --title-size: 21px;
        --clock-size: 38px;
        --state-size: 34px;
        --countdown-size: 76px;
        --caption-size: 20px;
        --label-size: 15px;
        --value-size: 24px;
        --transport-size: 20px;
        --footer-size: 14px;
      }}

      body {{
        overflow: auto;
      }}

      .screen {{
        min-height: 100dvh;
      }}

      .main {{
        grid-template-columns: 1fr;
        padding: 24px var(--edge-x);
      }}

      .hero {{
        gap: 18px;
      }}

      .footer {{
        flex-wrap: wrap;
      }}
    }}

    @media (max-width: 480px) {{
      :root {{
        --edge-x: 16px;
        --title-size: 18px;
        --clock-size: 30px;
        --state-size: 29px;
        --countdown-size: 62px;
        --caption-size: 18px;
        --value-size: 21px;
        --transport-size: 18px;
      }}

      .topbar {{
        gap: 12px;
      }}

      .status-dot {{
        width: 14px;
        height: 14px;
      }}
    }}

    @media (max-height: 700px) and (min-width: 821px) {{
      :root {{
        --edge-x: 28px;
        --edge-y: 16px;
        --main-gap: 22px;
        --clock-size: 42px;
        --state-size: 38px;
        --countdown-size: 96px;
        --caption-size: 20px;
        --band-pad-y: 14px;
        --band-pad-x: 18px;
        --value-size: 27px;
        --transport-size: 22px;
        --footer-size: 15px;
      }}

      .hero {{
        gap: 18px;
      }}

      .details {{
        gap: 12px;
      }}
    }}
  </style>
</head>
<body>
  <main id="screen" class="screen state-error">
    <header class="topbar">
      <div class="brand">
        <span class="status-dot" aria-hidden="true"></span>
        <div class="title">通勤提醒看板</div>
      </div>
      <div id="clock" class="clock">--:--:--</div>
    </header>

    <section class="main">
      <div class="hero">
        <div id="stateLabel" class="state-label">正在準備</div>
        <div id="countdown" class="countdown">--</div>
        <div id="countdownCaption" class="countdown-caption">正在整理今天的通勤資訊</div>
      </div>

      <div class="details">
        <section class="band">
          <div class="label">該出門的時間</div>
          <div id="departure" class="value">--:--</div>
        </section>
        <section class="band">
          <div class="label">想抵達的時間</div>
          <div id="arrival" class="value">--:--</div>
        </section>
        <section class="band">
          <div class="label">今天怎麼去</div>
          <div id="transport" class="value transport">尚無資料</div>
        </section>
        <section class="band">
          <div class="label">出門天氣</div>
          <div id="weather" class="value">--</div>
        </section>
      </div>
    </section>

    <footer class="footer">
      <span id="connection">正在更新</span>
      <button id="soundToggle" class="sound-button" type="button">聲音提醒</button>
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
    const soundToggle = document.getElementById("soundToggle");

    const stateLabels = {{
      safe: "時間還很充裕",
      warning: "可以準備出門了",
      urgent: "現在就出門",
      degraded: "資料暫時不穩",
      error: "先完成設定"
    }};

    let latestPayload = null;
    let ws = null;
    let soundEnabled = localStorage.getItem(`dashboardSoundEnabled:${{userId}}`) === "true";

    function updateSoundToggle() {{
      soundToggle.classList.toggle("enabled", soundEnabled);
      soundToggle.textContent = soundEnabled ? "聲音提醒開啟" : "聲音提醒";
    }}

    function spokenStorageKey(payload, moment) {{
      return [
        "dashboardSpoken",
        userId,
        payload.target_date || "unknown-date",
        payload.departure_time || "unknown-time",
        moment
      ].join(":");
    }}

    async function notifyDepartureVoiceComplete(payload) {{
      try {{
        await fetch(`/api/v1/dashboard/departure-check/${{userId}}`, {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify({{
            target_date: payload.target_date,
            departure_time: payload.departure_time
          }})
        }});
      }} catch (error) {{
        console.warn("departure check notification failed", error);
      }}
    }}

    function speakReminder(message, storageKey, onDone) {{
      if (!soundEnabled || !("speechSynthesis" in window)) return;
      if (localStorage.getItem(storageKey) === "true") return;
      const utterance = new SpeechSynthesisUtterance(message);
      utterance.lang = "zh-TW";
      utterance.rate = 1;
      utterance.pitch = 1;
      if (onDone) {{
        utterance.onend = onDone;
      }}
      window.speechSynthesis.cancel();
      window.speechSynthesis.speak(utterance);
      localStorage.setItem(storageKey, "true");
    }}

    function handleVoiceReminder(payload) {{
      if (!payload.ok || payload.reminder_enabled === false) return;
      const seconds = payload.seconds_until_departure;
      if (seconds === null || seconds === undefined) return;

      if (seconds <= 300 && seconds > 60) {{
        speakReminder("請於五分鐘後出門。", spokenStorageKey(payload, "five-minutes"));
      }}
      if (seconds <= 60 && seconds > 0) {{
        speakReminder("距離出門剩下一分鐘，請準備出門。", spokenStorageKey(payload, "one-minute"));
      }}
      if (seconds <= 0 && seconds >= -60) {{
        speakReminder(
          "已到出門時間，請準時出門。",
          spokenStorageKey(payload, "leave-now"),
          () => notifyDepartureVoiceComplete(payload)
        );
      }}
    }}

    soundToggle.addEventListener("click", () => {{
      soundEnabled = !soundEnabled;
      localStorage.setItem(`dashboardSoundEnabled:${{userId}}`, soundEnabled ? "true" : "false");
      updateSoundToggle();
      if (soundEnabled) {{
        speakReminder("聲音提醒已開啟。", `dashboardSoundReady:${{userId}}:${{Date.now()}}`);
      }}
    }});

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
        countdownCaption.textContent = payload.next_step ? "請先在 LINE 完成基本設定" : "暫時抓不到今天的通勤資訊";
        departure.textContent = "--:--";
        arrival.textContent = "--:--";
        transport.textContent = payload.reason || "還沒有可顯示的資料";
        weather.textContent = "--";
        updatedAt.textContent = "這次更新沒有成功";
        return;
      }}

      countdown.textContent = formatCountdown(payload.seconds_until_departure);
      countdownCaption.textContent = payload.seconds_until_departure <= 0 ? "已經到出門時間" : "距離該出門還有";
      departure.textContent = payload.departure_time || "--:--";
      arrival.textContent = payload.target_arrival_time || "--:--";
      transport.textContent = payload.transport_line || "還沒有通勤建議";
      weather.textContent = formatWeather(payload.weather);
      updatedAt.textContent = payload.updated_at ? `更新 ${{new Date(payload.updated_at).toLocaleTimeString("zh-TW", {{ hour12: false }})}}` : "尚未更新";
      handleVoiceReminder(payload);
    }}

    async function fetchStatus() {{
      try {{
        const response = await fetch(`/api/v1/dashboard/status/${{userId}}`, {{ cache: "no-store" }});
        render(await response.json());
        connection.textContent = ws && ws.readyState === WebSocket.OPEN ? "即時更新中" : "定時更新中";
      }} catch (error) {{
        connection.textContent = "連線不穩，正在重試";
      }}
    }}

    function connectWebSocket() {{
      const protocol = window.location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(`${{protocol}}://${{window.location.host}}/api/v1/dashboard/ws/${{userId}}`);
      ws.onopen = () => {{
        connection.textContent = "即時更新中";
      }};
      ws.onmessage = (event) => {{
        render(JSON.parse(event.data));
      }};
      ws.onclose = () => {{
        connection.textContent = "即時連線中斷，改用定時更新";
        setTimeout(connectWebSocket, 5000);
      }};
      ws.onerror = () => {{
        connection.textContent = "即時連線不穩，改用定時更新";
        ws.close();
      }};
    }}

    setInterval(updateClock, 1000);
    setInterval(fetchStatus, 30000);
    updateSoundToggle();
    updateClock();
    fetchStatus();
    connectWebSocket();
  </script>
</body>
</html>"""
