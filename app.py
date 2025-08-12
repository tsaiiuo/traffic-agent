# app.py
from flask import Flask, request, jsonify
import os
import json
from datetime import datetime, timedelta

import google.generativeai as genai

# === 這裡引用你前面做好的服務 ===
# 需包含：make_session, get_rain_forecast_json, get_news_by_keywords_json, SEGMENT_KEYWORDS
# app.py
from util import (
    make_session,
    get_rain_forecast_json,
    get_news_by_keywords_json,
    SEGMENT_KEYWORDS,
)


app = Flask(__name__)

# -------- Gemini 設定 --------
# GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_API_KEY = ''
if not GEMINI_API_KEY:
    raise RuntimeError("請在環境變數設定 GEMINI_API_KEY")

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel(
    model_name="gemini-2.5-pro",
    system_instruction=(
        "你是台南到高雄的道路通行管理員，請你總結四條道路的資訊，回答我想問的問題，"
        "讓我知道道路使用者知道當下路況屬於壅塞還是暢通，讓我知道有沒有相關的新聞，"
        "最後依照我的模型預測，推估未來會不會持續壅擠還有會維持多久。壅塞程度為1~5級，"
        "超過3的話代表車速開始顯著下降，5則是停滯；新聞也可以當作路況的參考，有施工、改道、車禍的話要提醒用戶。"
    ),
)

# -------- Chat 管理 --------
class ChatManager:
    def __init__(self, model):
        self.model = model
        self.chat = None

    def init_chat(self, history):
        self.chat = self.model.start_chat(history=history)

    def send(self, message):
        if not self.chat:
            raise RuntimeError("Chat not initialized")
        return self.chat.send_message(message)

chat_manager = ChatManager(model)

# -------- 簡單快取（避免每次 /init 都重抓） --------
_cache = {
    "payload": None,
    "expires_at": datetime.min,
}

CACHE_TTL_SECONDS = 120  # 2 分鐘內重用

def build_context_payload(session=None):
    """
    拉取 weather/news，整理成簡潔 JSON 給 LLM。
    之後要加入你 4 路段的「速度/壅塞預測」也在這裡加。
    """
    if session is None:
        session = make_session()

    # 1) 楠梓區 24h 降雨（每小時）
    rain_24h = get_rain_forecast_json(session=session)  # list[{時間, 降雨機率}]

    # 2) 國道一號 News.xml → 依關鍵字分組
    news_by_kw = get_news_by_keywords_json(keywords=SEGMENT_KEYWORDS, session=session)
    # 精簡每個關鍵字只保留最近 N 筆（避免 prompt 太肥）
    TOP_N = 8
    news_by_kw_trimmed = {
        k: v[:TOP_N] for k, v in news_by_kw.items() if v
    }

    # 3) 你自己的「四路段」模型輸出可以往這裡塞（示意）
    #    如果你有 API 或本地預測結果，把結果補進來：
    # road_forecasts = [
    #   {"name": "路段一", "jam_level_series": [...], "speed_series": [...]},
    #   ...
    # ]
    road_forecasts = []  # 目前先留空，等你接上

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "tz": "Asia/Taipei",
        "weather": {
            "location": "高雄市楠梓區",
            "rain_24h_hourly": rain_24h,  # [{時間, 降雨機率}]
        },
        "traffic_news": {
            "keywords": SEGMENT_KEYWORDS,
            "grouped_recent": news_by_kw_trimmed,  # 每關鍵字最多 TOP_N 筆
        },
        "road_forecasts": road_forecasts,  # 由你接入的四路段預測
    }
    return payload


def payload_to_prompt(payload: dict) -> str:
    """
    將整合後的 payload 轉為高品質的 Agent 指令。
    - 明確角色/任務/規範/輸出格式
    - 僅輸出 JSON（方便前端解析）
    - 以壅塞等級 1~5 為核心指標
    """
    compact_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    instruction = (
        "你是一位「台南至高雄道路通行智慧助理」，具備即時交通監控與壅塞預測能力。\n\n"
        "【角色任務】\n"
        "1. 接收並理解一份 JSON（包含：四個路段的壅塞/速度預測、楠梓區未來24小時逐小時降雨機率、依關鍵字分組的近期道路新聞）。\n"
        "2. 使用上述資料回答使用者關於『當下與未來道路狀況』的問題。\n"
        "3. 提供清楚、精簡、可採取行動的建議。\n\n"
        "【資料使用規範】\n"
        "- 優先以 JSON 內的實際數據為依據；若資料缺失，請明確標註「資料不足」。\n"
        "- 以天氣與新聞作為輔助判斷依據（例如：施工/事故/降雨 → 壅塞可能加劇或延長）。\n"
        "- 請勿臆測未提供的細節。\n\n"
        "【壅塞判斷標準】\n"
        "- 壅塞等級：1~5（1=暢通；2=稍壅；3=明顯減速；4=嚴重壅塞；5=近停滯）。\n"
        "- 等級 ≥3 時需特別提醒；若未來連續多個時段 ≥3，請估計壅塞持續時間與可能緩解時點。\n\n"
        "【輸出格式（只輸出 JSON）】\n"
        "{\n"
        '  "current_status": {"summary": "...","jam_level": 1},\n'
        '  "forecast": {"trend": "...","expected_duration": "..."},\n'
        '  "weather_impact": "...",\n'
        '  "news_alerts": ["...","..."],\n'
        '  "recommendations": ["...","..."]\n'
        "}\n\n"
        "【回覆規範】\n"
        "- `current_status`：根據當下數據的簡短摘要與壅塞等級（整數1~5）。\n"
        "- `forecast`：壅塞變化趨勢與預估持續時間（若無法估計，請說明原因）。\n"
        "- `weather_impact`：降雨對交通的可能影響（無影響則填「無顯著影響」）。\n"
        "- `news_alerts`：與壅塞相關的重要新聞（事故/施工/改道），以簡短標題列出。\n"
        "- `recommendations`：具體可執行建議（改道、提前/延後出發、避開路段等）。\n"
        "- 僅輸出 JSON；不要加入額外說明或前後綴文字。\n\n"
        "【資料如下（JSON，請完整閱讀並作為唯一依據）】\n"
    )

    return instruction + compact_json


def get_cached_payload(session=None):
    now = datetime.now()
    if _cache["payload"] is not None and now < _cache["expires_at"]:
        return _cache["payload"]
    payload = build_context_payload(session=session)
    _cache["payload"] = payload
    _cache["expires_at"] = now + timedelta(seconds=CACHE_TTL_SECONDS)
    return payload


# -------- 路由 --------
@app.route("/init", methods=["POST"])
def init():
    """
    初始化對話：抓取 weather/news（+ 你之後的四路段預測），
    生成一段簡潔 JSON 當作上下文，建立 chat。
    """
    try:
        payload = get_cached_payload()
        prompt = payload_to_prompt(payload)

        chat_manager.init_chat(history=[
            {"role": "user", "parts": [prompt]},
            {"role": "model", "parts": ["我已了解背景，請開始提問。"]},
        ])
        return jsonify({"message": "Chat session initialized.", "context_ts": payload["generated_at"]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/chat", methods=["POST"])
def chat_with_model():
    data = request.get_json(silent=True) or {}
    user_input = data.get("text")
    if not user_input:
        return jsonify({"error": "No input provided"}), 400

    try:
        # 如果你希望在每次問答前自動刷新資料，也可在這裡重抓並追加 system note
        # payload = get_cached_payload()
        # _ = chat_manager.send("[system note] 自動刷新資料完成。")

        response = chat_manager.send(user_input)
        return jsonify({"output": response.text})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True})


if __name__ == "__main__":
    # export GEMINI_API_KEY=...
    app.run(host="0.0.0.0", port=8080, debug=True)
