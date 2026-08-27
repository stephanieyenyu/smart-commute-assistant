#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Generate the four smart-commute-assistant diagrams in the same draw.io
conventions used by stephanieyenyu/AMR_System/docs/diagrams.

Conventions lifted from that repo:
  · Component diagram — UML module shapes, layered containers, accent #2f6096,
    legend note + rationale note, bottom component/code mapping table,
    provenance footer with commit hash.
  · ER diagram — real shape=table with tableRow/partialRectangle cells,
    narrow PK/NN/REF marker column, Chinese section divider rows,
    "table_name　中文名" headers, ER crow's-foot edges.
  · State machines — Mermaid export palette, curved edges, Trebuchet MS,
    "english_state\\n中文說明" node labels, attached Chinese note nodes.

Output: ./system-architecture.drawio, er-diagram.drawio,
        reminder-state-machine.drawio, decision-flow.drawio
"""

from xml.sax.saxutils import escape as _esc


def escape(v):
    return _esc(str(v), {'"': "&quot;"})

COMMIT = "e10e6d9"

# ── AMR_System palette and styles ───────────────────────────────────────────
BLUE = "#2f6096"

S_TITLE = "text;html=1;align=left;verticalAlign=middle;fontSize=16;fontStyle=1;fontColor=#333333;"
S_SUB = "text;html=1;align=left;verticalAlign=middle;fontSize=11;fontColor=#666666;"
S_FOOT = "text;html=1;align=left;verticalAlign=middle;fontSize=10;fontColor=#888888;"
S_TH = "text;html=1;align=left;verticalAlign=middle;fontSize=11;fontStyle=1;fontColor=#333333;"
S_TD = "text;html=1;align=left;verticalAlign=middle;fontSize=10;fontColor=#333333;"
S_SECT = "text;html=1;align=left;verticalAlign=middle;fontSize=13;fontStyle=1;fontColor=#333333;"

S_MODULE = ("shape=module;jettyWidth=10;jettyHeight=5;whiteSpace=wrap;html=1;"
            "fillColor=#efefef;strokeColor=#999999;fontSize=11;align=left;"
            "verticalAlign=top;spacingLeft=18;spacingTop=6;")
S_MODULE_OFF = ("shape=module;jettyWidth=10;jettyHeight=5;whiteSpace=wrap;html=1;"
                "fillColor=#f7f7f7;strokeColor=#bbbbbb;dashed=1;fontSize=11;align=left;"
                "verticalAlign=top;spacingLeft=18;spacingTop=6;fontColor=#888888;")
S_CONT = ("rounded=0;whiteSpace=wrap;html=1;fillColor=#fbfcfe;strokeColor=#2f6096;"
          "strokeWidth=2;verticalAlign=top;align=left;spacingLeft=16;spacingTop=10;"
          "fontSize=15;fontStyle=1;fontColor=#2f6096;")
S_NOTE = ("shape=note;whiteSpace=wrap;html=1;fillColor=#fbfbf5;strokeColor=#b8b39a;"
          "fontSize=10;size=12;align=left;verticalAlign=top;spacingLeft=4;spacingTop=2;")
S_DB = ("shape=cylinder3;whiteSpace=wrap;html=1;boundedLbl=1;backgroundOutline=1;size=10;"
        "fillColor=#ffffff;strokeColor=#666666;fontSize=11;verticalAlign=middle;")
S_IFACE = ("shape=providedRequiredInterface;html=1;outlineConnect=0;strokeColor=#2f6096;"
           "fillColor=#ffffff;strokeWidth=1.5;direction=east;")
S_ILABEL = "text;html=1;align=left;verticalAlign=middle;fontSize=10;fontColor=#2f6096;"

E_SOLID = ("endArrow=none;html=1;rounded=0;strokeColor=#2f6096;strokeWidth=1;"
           "edgeStyle=orthogonalEdgeStyle;")
E_DASH = ("endArrow=open;endSize=8;html=1;rounded=0;strokeColor=#999999;dashed=1;"
          "edgeStyle=orthogonalEdgeStyle;fontSize=10;labelBackgroundColor=#ffffff;")
E_FLOW = ("endArrow=open;endSize=8;html=1;rounded=1;strokeColor=#2f6096;strokeWidth=1.5;"
          "edgeStyle=orthogonalEdgeStyle;fontSize=10;labelBackgroundColor=#ffffff;")

# ER
S_ERGROUP = ("rounded=0;whiteSpace=wrap;html=1;fillColor=#f7f9fc;strokeColor=#2f6096;"
             "dashed=1;strokeWidth=2;verticalAlign=top;align=left;spacingLeft=14;"
             "spacingTop=8;fontSize=14;fontStyle=1;fontColor=#666666;")
S_TABLE = ("shape=table;startSize=32;container=1;collapsible=0;childLayout=tableLayout;"
           "html=1;whiteSpace=wrap;fillColor=#ffffff;strokeColor=#444444;fontSize=12;"
           "fontStyle=1;align=center;verticalAlign=middle;")
S_TABLE_HL = ("shape=table;startSize=32;container=1;collapsible=0;childLayout=tableLayout;"
              "html=1;whiteSpace=wrap;fillColor=#ffffff;strokeColor=#2f6096;strokeWidth=2;"
              "fontSize=12;fontStyle=1;align=center;verticalAlign=middle;fontColor=#2f6096;")
S_ROW = ("shape=tableRow;horizontal=0;startSize=0;swimlaneHead=0;swimlaneBody=0;"
         "fillColor=none;collapsible=0;dropTarget=0;points=[[0,0.5],[1,0.5]];"
         "portConstraint=eastwest;top=0;left=0;right=0;bottom=0;")
S_KEY = ("shape=partialRectangle;connectable=0;fillColor=#f4f4f4;align=left;"
         "verticalAlign=middle;strokeColor=none;html=1;whiteSpace=wrap;fontSize=10;"
         "fontColor=#888888;spacingLeft=6;overflow=hidden;fontStyle=2;")
S_FIELD = ("shape=partialRectangle;connectable=0;fillColor=none;align=left;"
           "verticalAlign=middle;strokeColor=none;html=1;whiteSpace=wrap;fontSize=11;"
           "spacingLeft=6;overflow=hidden;")
S_DIV = ("shape=partialRectangle;connectable=0;fillColor=#eef2f7;align=left;"
         "verticalAlign=middle;strokeColor=none;html=1;whiteSpace=wrap;fontSize=10;"
         "fontStyle=1;fontColor=#2f6096;spacingLeft=6;overflow=hidden;")
E_ER = ("edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;fontSize=10;strokeColor=#444444;"
        "endArrow=ERoneToMany;endFill=0;startArrow=ERone;startFill=0;"
        "labelBackgroundColor=#ffffff;")

# Mermaid state machine
M_STATE = ("rounded=1;absoluteArcSize=1;arcSize=10;html=1;whiteSpace=wrap;strokeWidth=1;"
           "fillColor=light-dark(#ECECFF,#1f2020);strokeColor=light-dark(#9370DB,#cccccc);"
           "fontColor=light-dark(#333333,#cccccc);"
           "fontFamily=Trebuchet MS,Verdana,Arial,sans-serif;fontSize=14;")
M_TERM = ("rounded=1;absoluteArcSize=1;arcSize=10;html=1;whiteSpace=wrap;strokeWidth=2.5;"
          "fillColor=light-dark(#ECECFF,#1f2020);strokeColor=light-dark(#9370DB,#cccccc);"
          "fontColor=light-dark(#333333,#cccccc);"
          "fontFamily=Trebuchet MS,Verdana,Arial,sans-serif;fontSize=14;")
M_DEC = ("rhombus;whiteSpace=wrap;html=1;strokeWidth=1;"
         "fillColor=light-dark(#ECECFF,#1f2020);strokeColor=light-dark(#9370DB,#cccccc);"
         "fontColor=light-dark(#333333,#cccccc);"
         "fontFamily=Trebuchet MS,Verdana,Arial,sans-serif;fontSize=12;")
M_EXIT = ("rounded=1;absoluteArcSize=1;arcSize=10;html=1;whiteSpace=wrap;strokeWidth=1;"
          "fillColor=#fdf0ef;strokeColor=#b23b3b;fontColor=#b23b3b;"
          "fontFamily=Trebuchet MS,Verdana,Arial,sans-serif;fontSize=12;")
M_NOTE = ("rounded=0;whiteSpace=wrap;html=1;dashed=1;fillColor=#fbfbf5;strokeColor=#b8b39a;"
          "fontColor=#666666;align=left;verticalAlign=top;spacingLeft=8;spacingTop=6;"
          "fontFamily=Trebuchet MS,Verdana,Arial,sans-serif;fontSize=11;")
M_DOT = ("ellipse;html=1;fillColor=light-dark(#ECECFF,#1f2020);"
         "strokeColor=light-dark(#333333,#cccccc);strokeWidth=2;centerRadius=4;"
         "centerColor=light-dark(#9370DB,#cccccc);")
M_EDGE = ("curved=1;startArrow=none;endArrow=classic;endSize=9;fillColor=none;"
          "strokeColor=light-dark(#333333,#cccccc);html=1;fontSize=12;"
          "labelBackgroundColor=#E8E8E88D;"
          "fontFamily=Trebuchet MS,Verdana,Arial,sans-serif;"
          "fontColor=light-dark(#333333,#cccccc);")
M_EDGE_D = M_EDGE + "dashed=1;"
M_EDGE_NOTE = ("curved=0;startArrow=none;endArrow=none;dashed=1;strokeColor=#b8b39a;html=1;")


# ── builder ─────────────────────────────────────────────────────────────────
class Doc:
    def __init__(self, name, w, h):
        self.name, self.w, self.h = name, w, h
        self.parts = []
        self.n = 0

    def _id(self, hint=None):
        self.n += 1
        return hint or f"c{self.n}"

    def box(self, val, style, x, y, w, h, ident=None, parent="1"):
        i = self._id(ident)
        self.parts.append(
            f'        <mxCell id="{i}" value="{escape(val)}" style="{style}" vertex="1" parent="{parent}">\n'
            f'          <mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry" />\n'
            f'        </mxCell>')
        return i

    def edge(self, src, tgt, style, val="", pts=None, ident=None):
        i = self._id(ident)
        geo = '<mxGeometry relative="1" as="geometry">'
        if pts:
            geo += "\n            <Array as=\"points\">"
            for px, py in pts:
                geo += f'\n              <mxPoint x="{px}" y="{py}" />'
            geo += "\n            </Array>"
        geo += "\n          </mxGeometry>"
        self.parts.append(
            f'        <mxCell id="{i}" value="{escape(val)}" style="{style}" edge="1" '
            f'parent="1" source="{src}" target="{tgt}">\n          {geo}\n        </mxCell>')
        return i

    def table(self, title, x, y, w, rows, highlight=False, ident=None, keyw=44, rowh=24):
        """rows: list of (marker, text) or ('#', 'section title')"""
        tid = self._id(ident)
        h = 32 + rowh * len(rows)
        self.parts.append(
            f'        <mxCell id="{tid}" value="{escape(title)}" '
            f'style="{S_TABLE_HL if highlight else S_TABLE}" vertex="1" parent="1">\n'
            f'          <mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry" />\n'
            f'        </mxCell>')
        for idx, (marker, text) in enumerate(rows):
            rid = f"{tid}r{idx}"
            self.parts.append(
                f'        <mxCell id="{rid}" value="" style="{S_ROW}" vertex="1" parent="{tid}">\n'
                f'          <mxGeometry y="{32 + idx * rowh}" width="{w}" height="{rowh}" as="geometry" />\n'
                f'        </mxCell>')
            if marker == "#":
                self.parts.append(
                    f'        <mxCell id="{rid}a" value="{escape(text)}" style="{S_DIV}" '
                    f'vertex="1" parent="{rid}">\n'
                    f'          <mxGeometry width="{w}" height="{rowh}" as="geometry" />\n'
                    f'        </mxCell>')
            else:
                self.parts.append(
                    f'        <mxCell id="{rid}a" value="{escape(marker)}" style="{S_KEY}" '
                    f'vertex="1" parent="{rid}">\n'
                    f'          <mxGeometry width="{keyw}" height="{rowh}" as="geometry" />\n'
                    f'        </mxCell>')
                self.parts.append(
                    f'        <mxCell id="{rid}b" value="{escape(text)}" style="{S_FIELD}" '
                    f'vertex="1" parent="{rid}">\n'
                    f'          <mxGeometry x="{keyw}" width="{w - keyw}" height="{rowh}" as="geometry" />\n'
                    f'        </mxCell>')
        return tid, h

    def save(self, path):
        body = "\n".join(self.parts)
        xml = (
            '<mxfile host="app.diagrams.net">\n'
            f'  <diagram name="{escape(self.name)}" id="{escape(self.name)}">\n'
            f'    <mxGraphModel dx="1675" dy="772" grid="1" gridSize="10" guides="1" '
            f'tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" '
            f'pageWidth="{self.w}" pageHeight="{self.h}" math="0" shadow="0">\n'
            '      <root>\n'
            '        <mxCell id="0" />\n'
            '        <mxCell id="1" parent="0" />\n'
            f'{body}\n'
            '      </root>\n'
            '    </mxGraphModel>\n'
            '  </diagram>\n'
            '</mxfile>\n')
        open(path, "w", encoding="utf-8").write(xml)
        print(f"  {path}")


# ════════════════════════════════════════════════════════════════════════════
# 1. 元件圖
# ════════════════════════════════════════════════════════════════════════════
def component():
    d = Doc("Component Diagram", 1560, 1420)
    d.box("Component Diagram — Smart Commute Assistant　元件圖", S_TITLE, 40, 24, 800, 26)
    d.box("球＋插槽＝介面：球端提供，插槽端需要。介面名稱標於符號旁。　灰色虛線框＝程式碼中存在但未佈署。",
          S_SUB, 40, 52, 1000, 20)

    # 左側外部
    ext_line = d.box("<b>«external»</b><br>LINE 使用者端<br>文字指令 · Quick Reply", S_MODULE, 60, 150, 200, 64)
    ext_plat = d.box("<b>«external»</b><br>LINE Platform<br>Messaging API v3 · LIFF · Webhook", S_MODULE, 60, 268, 200, 76)
    ext_dash = d.box("<b>«external»</b><br>看板瀏覽器<br>WebSocket · 語音提示", S_MODULE, 60, 398, 200, 64)

    # 主容器
    d.box("smart-commute-assistant　單一 uvicorn 行程", S_CONT, 300, 110, 620, 620)
    l_api = d.box("<b>«component»　介面層</b><br>webhook.py（驗簽 · 25 個文字指令 · 3 個 postback）<br>"
                  "liff_routes.py · dashboard_ws.py · family.py<br>main.py（37 條路由）· 看板靜態檔",
                  S_MODULE, 340, 168, 540, 88)
    l_core = d.box("<b>«component»　決策核心</b><br>service.py<br>_compute_today_plan<br>"
                   "choose_commute_option_with_override<br>calculate_departure_time_by_mode_fast",
                   S_MODULE, 340, 300, 300, 112)
    l_sched = d.box("<b>«component»　排程器</b><br>reminder_scheduler.py<br>reminder_timing.py<br>"
                    "APScheduler · Asia/Taipei<br>30 秒 tick · 21:00 cron",
                    S_MODULE, 680, 300, 200, 112)
    l_prov = d.box("<b>«component»　對外通訊</b><br>maps_client.py · tdx_bus.py<br>"
                   "metro_basic.py · weather.py<br>全部經 safe_call(timeout)",
                   S_MODULE, 340, 470, 300, 88)
    l_obs = d.box("<b>«component»　觀測</b><br>integrations/<br>api_health.py<br>"
                  "<font color='#2f6096'>對外呼叫的唯一紀錄點</font>",
                  S_MODULE, 680, 470, 200, 88)
    l_data = d.box("<b>«component»　資料存取</b>　models.py · db.py · crud.py · schema_guard.py",
                   S_MODULE, 340, 610, 540, 50)

    db = d.box("PostgreSQL（Render）<br>10 張表 · 15 個 Alembic 版本", S_DB, 480, 790, 260, 64)

    # 右側外部服務
    e_routes = d.box("<b>«external»</b><br>Google Routes API<br>google.routes.transit · walk", S_MODULE, 1000, 150, 280, 64)
    e_geo = d.box("<b>«external»</b><br>Google Geocoding API<br>google.geocode", S_MODULE, 1000, 240, 280, 56)
    e_bus = d.box("<b>«external»</b><br>TDX 公車<br>tdx.bus.auth", S_MODULE, 1000, 322, 280, 56)
    e_metro = d.box("<b>«external»</b><br>TDX 捷運<br>tdx.metro.auth", S_MODULE, 1000, 404, 280, 56)
    e_cwa = d.box("<b>«external»</b><br>中央氣象署 CWA<br>cwa.weather.city", S_MODULE, 1000, 486, 280, 56)

    # 未佈署
    d.box("<b>«component»　未佈署</b><br>celery_app.py · tasks.py<br>redis_cache.py<br>"
          "render.yaml 未宣告 worker 與 Redis 服務<br>"
          "REDIS_URL 落回 localhost，實際走行程內快取",
          S_MODULE_OFF, 1000, 590, 280, 88)

    # 介面標籤
    d.box("<b>IWebhook</b>　POST /webhooks/line 驗簽", S_ILABEL, 264, 256, 240, 16)
    d.box("<b>ILinePush</b>　推播 · Quick Reply", S_ILABEL, 100, 356, 220, 16)
    d.box("<b>IDashboardWS</b>　狀態廣播", S_ILABEL, 264, 386, 200, 16)
    d.box("<b>IPlan</b>　凍結今日計畫", S_ILABEL, 646, 288, 160, 16)
    d.box("<b>IProvider</b>　路線 · 公車 · 捷運 · 天氣", S_ILABEL, 646, 458, 240, 16)
    d.box("<b>IRepository</b>　SQLAlchemy Session", S_ILABEL, 600, 686, 240, 16)

    # 連線
    d.edge(ext_line, ext_plat, E_SOLID)
    d.edge(ext_plat, l_api, E_SOLID)
    d.edge(ext_dash, l_api, E_SOLID)
    d.edge(l_api, l_core, E_SOLID)
    d.edge(l_sched, l_core, E_SOLID)
    d.edge(l_sched, l_api, E_SOLID)
    d.edge(l_core, l_prov, E_SOLID)
    d.edge(l_core, l_data, E_SOLID, pts=[(330, 356), (330, 635)])
    d.edge(l_prov, l_obs, E_SOLID)
    d.edge(l_obs, l_data, E_SOLID, pts=[(780, 584)])
    d.edge(l_data, db, E_SOLID)
    d.edge(l_prov, e_routes, E_DASH, "並行送出", pts=[(950, 514), (950, 182)])
    d.edge(l_prov, e_geo, E_DASH, "", pts=[(960, 514), (960, 268)])
    d.edge(l_prov, e_bus, E_DASH, "", pts=[(970, 514), (970, 350)])
    d.edge(l_prov, e_metro, E_DASH, "", pts=[(940, 514), (940, 432)])
    d.edge(l_prov, e_cwa, E_DASH, "", pts=[(930, 514)])
    d.edge(l_obs, db, E_DASH, "每次呼叫寫一列", pts=[(900, 514), (900, 822)])

    # 註記
    d.box("<b>圖例</b><br><br><b>«component»</b>　元件（模組圖示）<br>"
          "實線　組裝連接器<br>灰虛線　依賴（外部呼叫）<br>"
          "灰虛線框　程式碼中存在但未佈署<br><br>"
          "來源檔名標於元件內。",
          S_NOTE, 60, 490, 200, 150)

    d.box("<b>兩個設計選擇</b><br><br>"
          "<b>對外呼叫只有一個紀錄點。</b><br>"
          "四個供應商的呼叫全部經過<br>"
          "log_api_health（6 個端點標籤 ·<br>"
          "23 個呼叫點），因此每次失敗都<br>"
          "留下一列。docs/metrics.md 的<br>"
          "數字由這些列推導，而非讀應用<br>"
          "程式日誌。<br><br>"
          "<b>排程器與 web 同一行程。</b><br>"
          "沒有鎖也沒有領導者選舉，第二<br>"
          "個 Render 實例會讓每則提醒送<br>"
          "兩次。現階段接受，不是已解決。",
          S_NOTE, 60, 660, 200, 260)

    # 對照表
    d.box("元件與檔案對照", S_SECT, 40, 970, 400, 24)
    cols = [(40, 190), (240, 300), (550, 960)]
    heads = ["元件", "職責", "對應程式碼"]
    for (cx, cw), htxt in zip(cols, heads):
        d.box(f"<b>{htxt}</b>", S_TH, cx, 1004, cw, 26)
    table_rows = [
        ("介面層", "webhook 驗簽、指令分派、LIFF、WebSocket",
         "webhook.py（COMMAND_ALIASES 25 鍵）、liff_routes.py、dashboard_ws.py、family.py、main.py"),
        ("決策核心", "排程解析 → 座標守衛 → 並行查詢 → 模式選擇 → 出發時間",
         "service.py — _compute_today_plan、choose_commute_option_with_override、calculate_departure_time_by_mode_fast"),
        ("排程器", "30 秒 tick 推進三階段提醒；21:00 每日簡報",
         "reminder_scheduler.py（2 個 job）、reminder_timing.py（WAIT／SEND／SKIP_STALE／ALREADY_SENT）"),
        ("對外通訊", "四個供應商客戶端，逐一設逾時",
         "maps_client.py、tdx_bus.py、metro_basic.py、weather.py；全部包在 service.safe_call(timeout)"),
        ("觀測", "對外呼叫的延遲、狀態碼、錯誤訊息",
         "integrations/api_health.py — log_api_health()；6 個端點標籤、23 個呼叫點"),
        ("資料存取", "ORM、連線、啟動時結構檢查",
         "models.py（10 張表）、db.py、crud.py、schema_guard.py（ensure_runtime_schema，與 Alembic 職責重疊）"),
    ]
    y = 1036
    for a, b, c in table_rows:
        d.box(a, S_TD, cols[0][0], y, cols[0][1], 28)
        d.box(b, S_TD, cols[1][0], y, cols[1][1], 28)
        d.box(c, S_TD, cols[2][0], y, cols[2][1], 28)
        y += 32

    d.box(f"來源：backend/app/ @ {COMMIT}（已逐項比對程式碼）　·　"
          "README 舊版描述的 api/ core/ models/ schemas/ services/ worker/ 六層目錄並不存在，實際為平鋪結構",
          S_FOOT, 40, y + 14, 1200, 20)
    d.save("system-architecture.drawio")


# ════════════════════════════════════════════════════════════════════════════
# 2. ER 圖
# ════════════════════════════════════════════════════════════════════════════
def er():
    d = Doc("ER Diagram", 1560, 1600)
    d.box("ER Diagram — Smart Commute Assistant　資料模型", S_TITLE, 40, 24, 800, 26)
    d.box("PK 主鍵　NN 非空　FK 已宣告 ForeignKey　·　欄位格式：欄位名　型別　中文說明",
          S_SUB, 40, 52, 900, 20)

    d.box("PostgreSQL（Render）　單一資料庫", S_ERGROUP, 40, 90, 1480, 1360)

    t_house, _ = d.table("households　家戶", 70, 130, 280, [
        ("PK", "id　INTEGER"),
        ("NN", "invite_code　VARCHAR　UNIQUE"),
        ("", "name　VARCHAR"),
        ("", "created_at / updated_at　DATETIME"),
    ], ident="thouse")

    t_user, h_user = d.table("users　使用者", 70, 300, 280, [
        ("PK", "id　INTEGER"),
        ("NN", "line_user_id　VARCHAR　UNIQUE·INDEX"),
        ("", "display_name　VARCHAR"),
        ("FK", "household_id　INTEGER　可為 NULL"),
        ("", "created_at　DATETIME"),
    ], ident="tuser")

    t_dest, _ = d.table("commute_destinations　常用目的地", 70, 500, 280, [
        ("PK", "id　INTEGER"),
        ("FK", "user_id　INTEGER"),
        ("", "destination_name　VARCHAR"),
    ], ident="tdest")

    t_fg, _ = d.table("family_groups　家庭群組", 70, 650, 280, [
        ("PK", "id　INTEGER"),
        ("NN", "name　VARCHAR"),
        ("NN", "invite_token　VARCHAR　UNIQUE·INDEX"),
        ("", "created_at　DATETIME"),
    ], ident="tfg")

    t_fm, _ = d.table("family_members　群組成員", 70, 820, 280, [
        ("PK", "id　INTEGER"),
        ("FK", "group_id　INTEGER"),
        ("FK", "user_id　INTEGER"),
        ("", "nickname　VARCHAR"),
        ("", "joined_at　DATETIME"),
    ], ident="tfm")

    t_prof, _ = d.table("commute_profiles　通勤設定檔", 420, 130, 400, [
        ("PK", "id　INTEGER"),
        ("FK", "user_id　INTEGER　UNIQUE（1:1）"),
        ("#", "地點　共 35 欄，此處摘錄"),
        ("", "home_*　address · lat · lng · city · township"),
        ("", "office_*　address · lat · lng · city · township"),
        ("", "selected_bus_stop_*　id · name · lat · lng"),
        ("", "selected_metro_station_*　id · name · lat · lng"),
        ("#", "偏好"),
        ("", "preferred_arrival_time　VARCHAR"),
        ("", "preferred_mode　VARCHAR"),
        ("", "transport_preference　JSON"),
        ("", "max_walk_mins　INTEGER"),
        ("", "active_weekdays　JSON　0＝週一"),
        ("", "reminder_enabled　BOOLEAN"),
        ("#", "對話狀態"),
        ("", "pending_field　VARCHAR　多輪輸入暫存"),
    ], ident="tprof")

    t_sched, _ = d.table("commute_schedules　通勤排程", 420, 600, 400, [
        ("PK", "id　INTEGER"),
        ("FK", "user_id　INTEGER"),
        ("#", "起訖點　決策時覆蓋 profile 的座標"),
        ("", "origin_name / origin_address　VARCHAR"),
        ("", "origin_lat / origin_lng　FLOAT"),
        ("", "dest_name / dest_address　VARCHAR"),
        ("", "dest_lat / dest_lng　FLOAT"),
        ("#", "生效條件"),
        ("", "time　VARCHAR　目標抵達 'HH:MM'"),
        ("", "days　JSON　0＝週一"),
        ("", "is_active　BOOLEAN"),
        ("", "reminder_enabled　BOOLEAN"),
    ], ident="tsched")

    t_ovr, _ = d.table("commute_overrides　當日覆蓋與提醒狀態", 880, 910, 420, [
        ("PK", "id　INTEGER"),
        ("FK", "user_id　INTEGER"),
        ("FK", "schedule_id　INTEGER　可為 NULL"),
        ("NN", "target_date　DATE　INDEX"),
        ("#", "當日意圖覆蓋"),
        ("", "target_arrival_time　VARCHAR"),
        ("", "transport_mode_override　VARCHAR"),
        ("", "commute_disabled / commute_enabled　BOOLEAN"),
        ("#", "凍結計畫　只算一次，之後每 tick 讀取"),
        ("", "frozen_plan_key　VARCHAR"),
        ("", "frozen_departure_time　VARCHAR　＝ T"),
        ("", "frozen_reminder_text　TEXT"),
        ("", "reminder_prepared_at　DATETIME"),
        ("#", "送出守衛　at-most-once 的實作方式"),
        ("", "monitor_one_hour_sent_at　DATETIME"),
        ("", "monitor_five_min_sent_at　DATETIME"),
        ("", "departure_question_sent_at　DATETIME"),
        ("", "departed_at　DATETIME　終止條件"),
        ("", "nightly_brief_sent_at　DATETIME"),
        ("", "alert_status　VARCHAR　pending／acknowledged"),
    ], ident="tovr")

    t_log, _ = d.table("commute_logs　決策紀錄", 880, 130, 420, [
        ("PK", "id　INTEGER"),
        ("FK", "user_id　INTEGER"),
        ("NN", "date　DATE　INDEX"),
        ("#", "特徵 features　決策當下的狀態"),
        ("", "day_of_week　INTEGER"),
        ("", "is_holiday　BOOLEAN"),
        ("", "target_arrival_time　VARCHAR"),
        ("", "weather_condition　VARCHAR"),
        ("", "rain_prob　INTEGER"),
        ("", "temp　FLOAT"),
        ("", "gmaps_traffic_duration　INTEGER"),
        ("", "tdx_bus_eta　INTEGER"),
        ("#", "行動 action　政策選了什麼"),
        ("", "suggested_departure_time　VARCHAR"),
        ("", "suggested_transport　VARCHAR"),
        ("#", "結果 outcome　實際發生什麼"),
        ("", "actual_departure_time　VARCHAR"),
        ("", "actual_transport　VARCHAR"),
        ("", "actual_arrival_time　VARCHAR"),
        ("", "is_late　BOOLEAN　　← 標籤"),
    ], highlight=True, ident="tlog")

    t_api, _ = d.table("api_health_logs　外部 API 觀測", 420, 980, 400, [
        ("PK", "id　INTEGER"),
        ("NN", "endpoint　VARCHAR　INDEX　6 種標籤"),
        ("NN", "timestamp　DATETIME　INDEX"),
        ("", "latency_ms　INTEGER"),
        ("", "status_code　INTEGER"),
        ("", "error_message　VARCHAR"),
    ], highlight=True, ident="tapi")

    V = "exitX=0.5;exitY=1;exitDx=0;exitDy=0;entryX=0.5;entryY=0;entryDx=0;entryDy=0;"
    L = "exitX=0;exitY=0.5;exitDx=0;exitDy=0;entryX=0;entryY=0.5;entryDx=0;entryDy=0;"
    R = "exitX=1;exitY=0.5;exitDx=0;exitDy=0;entryX=0;entryY=0.5;entryDx=0;entryDy=0;"
    d.edge(t_house, t_user, E_ER + V, "1 : N")
    d.edge(t_user, t_dest, E_ER + V, "1 : N")
    d.edge(t_fg, t_fm, E_ER + V, "1 : N")
    d.edge(t_user, t_fm, E_ER + L, "1 : N", pts=[(56, 376), (56, 896)])
    d.edge(t_user, t_prof, E_ER + R, "1 : 1", pts=[(385, 376), (385, 400)])
    d.edge(t_user, t_sched, E_ER + R, "1 : N", pts=[(385, 376), (385, 700)])
    d.edge(t_user, t_log, E_ER + R, "1 : N", pts=[(385, 376), (385, 573), (850, 573)])
    d.edge(t_sched, t_ovr, E_ER + R, "1 : N　可為 NULL", pts=[(850, 860), (850, 1050)])

    d.box("<b>commute_logs 是刻意做成的標註資料集</b><br><br>"
          "欄位分成（特徵、行動、結果）三段，因為規則式引擎<br>"
          "從一開始就打算可被學習式政策取代：決策當下引擎<br>"
          "看到的每個值，都與它選了什麼、以及實際發生什麼<br>"
          "記在同一列。<br><br>"
          "<font color='#b23b3b'><b>這個結構給不了的東西。</b>結果欄位是自陳的——<br>"
          "actual_departure_time 來自使用者點「已出門」，<br>"
          "is_late 沒有獨立觀測。忘記點的那天產生的是 NULL，<br>"
          "而不是一個負例。因此該看的是欄位填充率，不是列數。</font>",
          S_NOTE, 880, 680, 420, 190)

    d.box("<b>api_health_logs 沒有外鍵，是刻意的</b><br><br>"
          "不帶使用者參照，所以刪除使用者不影響它，也可以<br>"
          "直接公開而不需去識別化。docs/metrics.md 的外部<br>"
          "可靠度數字全部由這張表推導。",
          S_NOTE, 420, 1190, 400, 92)

    d.box("<b>全域慣例</b><br><br>"
          "<b>時間是字串。</b>target_arrival_time、suggested_departure_time、<br>"
          "actual_departure_time、commute_schedules.time 皆為 VARCHAR 'HH:MM'，<br>"
          "不是 TIME。任何運算都要先轉型，且可能存在格式錯誤的值。<br><br>"
          "<b>星期以 0 為週一</b>，對齊 Python datetime.weekday()。<br><br>"
          "<b>全部為 Asia/Taipei</b>。單一服務、單一時區，沒有跨服務比較的問題。<br><br>"
          "<font color='#b23b3b'><b>未宣告 ON DELETE。</b>外鍵存在但沒有 cascade，刪除使用者<br>"
          "會留下孤兒列，需由應用層先清。</font>",
          S_NOTE, 70, 1010, 280, 240)

    d.box(f"來源：backend/app/models.py @ {COMMIT}（已逐項比對程式碼）　·　"
          "10 張表 · 15 個 Alembic 版本　·　部分結構另由 schema_guard.ensure_runtime_schema() 於啟動時補建",
          S_FOOT, 40, 1490, 1300, 20)
    d.save("er-diagram.drawio")


# ════════════════════════════════════════════════════════════════════════════
# 3. 提醒狀態機
# ════════════════════════════════════════════════════════════════════════════
def reminder():
    d = Doc("Reminder State Machine", 1400, 1180)
    d.box("Reminder State Machine　提醒狀態機", S_TITLE, 40, 24, 700, 26)
    d.box("一列 commute_overrides ＝ 一位使用者 × 一組排程 × 一天。T ＝ frozen_departure_time。",
          S_SUB, 40, 52, 900, 20)

    x, w = 140, 190
    start = d.box("", M_DOT, x + w / 2 - 7, 110, 14, 14)
    s0 = d.box("unprepared\n未凍結", M_STATE, x, 150, w, 50)
    s1 = d.box("armed\n已凍結待命", M_STATE, x, 250, w, 50)
    s2 = d.box("one_hour_sent\n已送 T−60 分提醒", M_STATE, x, 360, w, 54)
    s3 = d.box("five_min_sent\n已送 T−5 分提醒", M_STATE, x, 474, w, 54)
    s4 = d.box("question_sent\n已問「您出門了嗎？」", M_STATE, x, 588, w, 54)
    s5 = d.box("departed\n已出門（終止）", M_TERM, x, 702, w, 50)
    s6 = d.box("stale\n逾時放棄（終止 · 靜默）", M_TERM, 430, 702, 200, 50)
    end = d.box("", M_DOT, x + w / 2 - 7, 790, 14, 14)

    d.edge(start, s0, M_EDGE, "排程今日生效")
    d.edge(s0, s1, M_EDGE, "freeze_today_reminder_payload()\n失敗後每 300 秒才重試")
    d.edge(s1, s2, M_EDGE, "落在 [T−3600, T−3525)\n且 monitor_one_hour_sent_at 為 NULL")
    d.edge(s2, s3, M_EDGE, "落在 [T−300, T−225)\n且 monitor_five_min_sent_at 為 NULL")
    d.edge(s3, s4, M_EDGE, "落在 [T, T+120]\n且 departure_question_sent_at 為 NULL\n同時觸發看板語音提示")
    d.edge(s4, s5, M_EDGE, "使用者點「已出門」")
    d.edge(s4, s6, M_EDGE_D, "now ＞ T+120")
    d.edge(s5, end, M_EDGE)
    d.edge(s1, s5, M_EDGE_D, "任何時點 departed_at 被寫入\n即直接跳到終止",
           pts=[(390, 275), (390, 727)])

    n1 = d.box("三個階段是可跳過的，不是循序的。<br>"
               "每條轉移各自獨立守衛。使用者若在出門前<br>"
               "20 分鐘才設排程，就不會進入 T−60 的窗，<br>"
               "而 T−5 的階段照常觸發。畫成鏈狀只因為<br>"
               "那是常見路徑，不是狀態本身有序。<br><br>"
               "已過窗的階段不補送。",
               M_NOTE, 430, 360, 300, 110)
    d.edge(s2, n1, M_EDGE_NOTE)

    n2 = d.box("凍結一次的理由：tick 每 30 秒跑一次，<br>"
               "若每次重算路線，等於每位使用者每天約<br>"
               "2,880 次對外 API 呼叫。改為算一次寫進<br>"
               "frozen_* 三欄，之後每次 tick 只是純粹的<br>"
               "時間戳比較。",
               M_NOTE, 430, 250, 300, 90)
    d.edge(s1, n2, M_EDGE_NOTE)

    # 時間窗
    d.box("三個觸發窗", S_SECT, 790, 110, 300, 24)
    d.box("", "endArrow=block;html=1;strokeWidth=2;strokeColor=#666666;", 0, 0, 0, 0)  # placeholder removed below
    d.parts.pop()
    d.parts.append(
        '        <mxCell id="axis" value="" style="endArrow=block;html=1;strokeWidth=2;'
        'strokeColor=#666666;" edge="1" parent="1">\n'
        '          <mxGeometry relative="1" as="geometry">\n'
        '            <mxPoint x="800" y="230" as="sourcePoint" />\n'
        '            <mxPoint x="1330" y="230" as="targetPoint" />\n'
        '          </mxGeometry>\n'
        '        </mxCell>')
    d.box("one_hour<br>75 秒", "rounded=0;whiteSpace=wrap;html=1;fillColor=#ECECFF;"
          "strokeColor=#9370DB;fontSize=10;fontFamily=Trebuchet MS,Verdana,Arial,sans-serif;",
          830, 205, 60, 50)
    d.box("T−3600", S_FOOT, 826, 258, 70, 16)
    d.box("five_min<br>75 秒", "rounded=0;whiteSpace=wrap;html=1;fillColor=#ECECFF;"
          "strokeColor=#9370DB;fontSize=10;fontFamily=Trebuchet MS,Verdana,Arial,sans-serif;",
          1050, 205, 60, 50)
    d.box("T−300", S_FOOT, 1048, 258, 70, 16)
    d.box("departure<br>120 秒", "rounded=0;whiteSpace=wrap;html=1;fillColor=#ECECFF;"
          "strokeColor=#9370DB;fontSize=10;fontFamily=Trebuchet MS,Verdana,Arial,sans-serif;",
          1170, 205, 90, 50)
    d.box("T", S_FOOT, 1168, 258, 40, 16)
    d.box("逾時放棄", "rounded=0;whiteSpace=wrap;html=1;fillColor=#fdf0ef;strokeColor=#b23b3b;"
          "dashed=1;fontSize=10;fontColor=#b23b3b;"
          "fontFamily=Trebuchet MS,Verdana,Arial,sans-serif;", 1270, 205, 60, 50)
    d.box("T+120 之後", S_FOOT, 1262, 258, 80, 16)

    for i in range(9):
        cx = 806 + i * 26
        col = "#b23b3b" if 830 <= cx <= 890 else "#666666"
        d.box("", f"ellipse;html=1;fillColor={col};strokeColor=none;", cx, 226, 8, 8)
    d.box("tick 每 30 秒　·　紅點＝落在窗內", S_FOOT, 806, 288, 300, 16)

    d.box("<b>常數</b>　reminder_scheduler.py<br><br>"
          "SCHEDULER_TICK_SECONDS = 30<br>"
          "EXACT_TRIGGER_WINDOW_SECONDS = 75<br>"
          "STALE_REMINDER_GRACE_SECONDS = 120<br>"
          "PREPARE_RETRY_SECONDS = 300<br>"
          "MORNING_MONITOR_OFFSETS = { one_hour: 3600, five_min: 300 }<br>"
          "NIGHTLY_BRIEF = 21:00 Asia/Taipei",
          S_NOTE, 790, 330, 380, 120)

    d.box("<b>不變式</b><br><br>"
          "<b>至少一次。</b>窗 75 秒 ＞ tick 30 秒，保證每個窗內至少落一個 tick，<br>"
          "沒有階段會被漏掉。<br><br>"
          "<b>至多一次。</b>送出前檢查 *_sent_at、送出時寫入，因此落在同一個窗內<br>"
          "的兩到三個 tick 只會產生一則訊息。<br><br>"
          "<b>兩者合起來</b>才是「每階段每天恰好一次」。不需要鎖、佇列或精準<br>"
          "時間觸發器，而且缺任一半都不成立。<br><br>"
          "<font color='#b23b3b'><b>失效模式。</b>為了錄影把 offset 壓縮（例如 one_hour 改 90 秒、<br>"
          "five_min 改 30 秒）卻不動 75 秒的窗，三個窗會重疊，三個階段在同一<br>"
          "個 tick 全部成立而一起送出。錯不在階段邏輯，而在於把窗當成常數，<br>"
          "但它其實是 offset 間距的函數。</font>",
          M_NOTE, 790, 480, 540, 220)

    d.box("<b>另一個 job</b>　nightly_brief_job<br><br>"
          "CronTrigger(hour=21, minute=0, timezone=\"Asia/Taipei\")。與上面的 tick 及本狀態機無關，<br>"
          "由 nightly_brief_sent_at 與 nightly_brief_plan_key 守衛。<br>"
          "用 cron 而非時間窗，是因為它只有一個觸發時刻，沒有 offset 運算會算錯。",
          S_NOTE, 790, 730, 540, 90)

    d.box("<b>早期草稿畫錯的地方</b>　記錄下來，因為落差本身才是有用的部分<br><br>"
          "1. 畫成三個排程 job。實際只有兩個 job，三個階段是同一個 tick 內的三條分支。<br>"
          "2. 畫成出門詢問會重試到被確認為止。實際只送一次，T+120 之後即放棄。<br>"
          "3. 畫成每階段各自重算路線。實際只凍結一次，之後每次 tick 都是讀取。",
          S_NOTE, 140, 870, 700, 100)

    d.box(f"來源：backend/app/reminder_scheduler.py、reminder_timing.py @ {COMMIT}（已逐項比對程式碼）",
          S_FOOT, 40, 1010, 900, 20)
    d.save("reminder-state-machine.drawio")


# ════════════════════════════════════════════════════════════════════════════
# 4. 決策流程圖
# ════════════════════════════════════════════════════════════════════════════
def decision():
    d = Doc("Decision Flow", 1400, 1440)
    d.box("Decision Flow　決策流程　_compute_today_plan()", S_TITLE, 40, 24, 800, 26)
    d.box("規則式行為政策，不含任何學習元件。每個對外呼叫都包在 safe_call(timeout) 中，逾時或例外一律回傳 None。",
          S_SUB, 40, 52, 1000, 20)

    x, w = 150, 300
    start = d.box("", M_DOT, x + w / 2 - 7, 100, 14, 14)
    n1 = d.box("解析排程\n依星期與 is_active 篩選，再取 schedule_id 或第一筆",
               M_STATE, x, 140, w, 54)
    d1 = d.box("找到排程？", M_DEC, x + 70, 220, 160, 70)
    x1 = d.box("no_schedule_for_date\n依日期回覆今天／明天／該日無排程", M_EXIT, 550, 228, 280, 54)

    n2 = d.box("以排程座標覆蓋 profile\n排程剛編輯過的值必須贏過 profile 的舊快取",
               M_STATE, x, 320, w, 54)
    d2 = d.box("起點、終點、\n抵達時間齊全？", M_DEC, x + 60, 400, 180, 80)
    x2 = d.box("setup_incomplete\nnext_step ＝ schedule", M_EXIT, 550, 418, 280, 44)

    d3 = d.box("四個座標\n皆非 NULL？", M_DEC, x + 60, 510, 180, 80)
    x3 = d.box("coords_missing\n指出是住家還是目的地失敗，並要求在地圖上選點而非打字",
               M_EXIT, 550, 520, 280, 60)

    n4 = d.box("決定當日意圖\n抵達時間 ← override ?? 排程　模式 ← 強制 ?? 當日設定 ?? auto",
               M_STATE, x, 620, w, 54)
    n5 = d.box("asyncio.gather　外層並行\n天氣 2.2 秒　路線與班次 4.8 秒", M_STATE, x, 700, w, 50)
    n6 = d.box("內層並行　依模式決定送出哪幾支\n"
               "一律　Google 路線（allowed_travel_modes）4.2 秒\n"
               "shortest 另加　限公車 4.2 秒 · 限捷運 4.2 秒\n"
               "auto／shortest／bus　TDX 公車即時 2.5 秒\n"
               "auto／shortest／metro　TDX 捷運 3.5 秒",
               M_STATE, x, 780, w, 96)
    n7 = d.box("模式選擇", M_STATE, x, 906, w, 34)
    n7a = d.box("shortest\n以 duration_minutes 排序三個候選取最小\n三者皆缺 → 退回 Google 建議",
                M_STATE, 60, 970, 240, 66)
    n7b = d.box("auto\n捷運 → 公車 → Google，取第一個能建立的\n不比較、不評分",
                M_STATE, 320, 970, 240, 66)
    n7c = d.box("bus／metro\n強制。快照不可用時回傳只帶懲罰常數的空殼\nbus_to_metro 未實作，直接回 Google",
                M_STATE, 580, 970, 240, 66)

    n8 = d.box("天氣緩衝\n降雨機率 ≥80 → +10　≥60 → +8　≥40 → +5\n"
               "否則描述含 雨／雷／陣雨／雷雨／豪雨／大雨 → +6　其餘 0",
               M_STATE, x, 1080, w, 66)
    n9 = d.box("出發時間\nlatest_on_time ＝ 抵達 −（路線時間 ＋ 天氣緩衝）",
               M_STATE, x, 1180, w, 50)
    out = d.box("寫入 frozen_departure_time / frozen_reminder_text / frozen_plan_key\n"
                "並回覆 LINE　→ 見 reminder-state-machine",
                M_TERM, x, 1260, w, 54)

    d.edge(start, n1, M_EDGE)
    d.edge(n1, d1, M_EDGE)
    d.edge(d1, x1, M_EDGE_D, "否")
    d.edge(d1, n2, M_EDGE, "是")
    d.edge(n2, d2, M_EDGE)
    d.edge(d2, x2, M_EDGE_D, "否")
    d.edge(d2, d3, M_EDGE, "是")
    d.edge(d3, x3, M_EDGE_D, "否")
    d.edge(d3, n4, M_EDGE, "是")
    d.edge(n4, n5, M_EDGE)
    d.edge(n5, n6, M_EDGE)
    d.edge(n6, n7, M_EDGE)
    d.edge(n7, n7a, M_EDGE)
    d.edge(n7, n7b, M_EDGE)
    d.edge(n7, n7c, M_EDGE)
    d.edge(n7b, n8, M_EDGE, "best_option")
    d.edge(n8, n9, M_EDGE)
    d.edge(n9, out, M_EDGE)

    d.box("<b>這道守衛之所以存在，是因為它曾經不存在</b><br><br>"
          "地址字串有值，不代表地理編碼成功。Google Geocoding<br>"
          "在免費試用到期後停止運作，home_lat／home_lng 回傳<br>"
          "None，一路帶進下游的格式字串，而每一次失敗都被<br>"
          "safe_call 吞掉。使用者看到的是一則看起來合理的<br>"
          "Google 通用建議，沒有任何東西表現得像壞掉。<br><br>"
          "修法不是把地理編碼的錯誤處理寫好，而是在這個前提<br>"
          "真正成為必要條件的位置明確檢查、並在那裡大聲失敗。<br>"
          "記於 docs/known-issues.md A-1。",
          S_NOTE, 870, 500, 380, 170)

    d.box("<b>「最短時間」為什麼要打三支而不是一支</b><br><br>"
          "不限交通方式的 Google 路線查詢，回傳的是 Google 自己<br>"
          "認為最好的那一組行程，並不會告訴你「堅持搭公車」要多<br>"
          "久、「堅持搭捷運」要多久——其他模式根本不在回應裡。<br><br>"
          "要比較模式，就得用不同的 allowed_travel_modes 把同一個<br>"
          "問題問三次。回傳 Google 的預設再標成「最短」會是一支<br>"
          "呼叫，而且是錯的。<br><br>"
          "三支並行送出，所以誠實的版本與偷懶的版本牆鐘時間相同，<br>"
          "約 4.2 秒而非 12.6 秒。",
          S_NOTE, 870, 800, 380, 170)

    d.box("<b>降級：各分支收到 None 時代表什麼</b><br><br>"
          "safe_call 會攔下 TimeoutError 與所有例外，印一行後回傳 None，<br>"
          "不往上拋。因此「拿不到答案」的意義由各呼叫端各自決定：<br><br>"
          "<b>天氣 → None</b>　代入 { 緩衝 0, 天氣「未知」}。<br>"
          "　下雨天的建議會比應有的更趕。<font color='#b23b3b'>降級方向偏向樂觀，是錯的方向。</font><br><br>"
          "<b>模式選擇 → None</b>　代入 { mode: google_transit, source: auto }。<br><br>"
          "<b>Google 路線 → None</b>　沒有路段也沒有時長，<br>"
          "　路線時間退回常數 DEFAULT_COMMUTE_MINUTES。使用者不會被告知<br>"
          "　這個估計值是預設值。<br><br>"
          "<b>TDX 公車／捷運快照 → None</b>　該選項不會被建立，auto 靜默落到<br>"
          "　下一順位。與「這裡本來就沒有公車路線」無法區分。<br><br>"
          "<b>地理編碼 → None</b>　在步驟三被攔下並顯示。<br>"
          "　<font color='#2f6096'>五條路徑中唯一會產生可見錯誤，而不是一個較安靜答案的分支。</font><br><br>"
          "<font color='#b23b3b'><b>值得命名的模式。</b>五條降級路徑有四條會產出一個看起來合理、<br>"
          "但建立在缺失資料上的答案。系統不會倒，使用者也看不出來。<br>"
          "每次呼叫都寫進 api_health_logs，所以失敗在資料裡是看得見的——<br>"
          "只是目前沒有任何東西在回應當下讀那些列來標註答案。</font>",
          S_NOTE, 870, 1010, 460, 330)

    d.box(f"來源：backend/app/service.py、weather.py @ {COMMIT}（已逐項比對程式碼）",
          S_FOOT, 40, 1380, 900, 20)
    d.save("decision-flow.drawio")


if __name__ == "__main__":
    print("generating:")
    component()
    er()
    reminder()
    decision()
