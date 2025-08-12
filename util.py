# -*- coding: utf-8 -*-
import re
import json
import requests
import xml.etree.ElementTree as ET
from typing import Dict, List, Tuple
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup
import pandas as pd
from datetime import datetime

# -----------------------------
# 常數
# -----------------------------
NEWS_URL = "https://tisvcloud.freeway.gov.tw/history/motc20/News.xml"
# 楠梓區 3hr 模組（降雨機率）
CWA_TOWN_PC_URL_TMPL = "https://www.cwa.gov.tw/V8/C/W/Town/MOD/3hr/6400400_3hr_PC.html?T={tstamp}"

# 只要 SectionStart 或 SectionEnd 含有這些字就歸到該分類
SEGMENT_KEYWORDS: List[str] = [
    "仁德",
    "仁德服務區",
    "仁德系統",
    "路竹",
    "高科",
    "岡山",
    "楠梓(北)",
    "楠梓(南)",
]

# -----------------------------
# 共用工具
# -----------------------------
_BRACKETS_PATTERN = re.compile(r"[()\[\]{}（）【】「」『』〈〉《》]")

def normalize_name(s: str) -> str:
    """名稱正規化：去空白（含全形）、去括號。"""
    if s is None:
        return ""
    s = s.strip()
    s = s.replace("\u3000", "")  # 全形空白
    s = _BRACKETS_PATTERN.sub("", s)
    s = re.sub(r"\s+", "", s)
    return s

def make_session() -> requests.Session:
    """附自動重試與 UA 的 session，較不易被擋。"""
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=1.5,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=False  # 相容舊版 urllib3
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({
        "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/116.0.0.0 Safari/537.36")
    })
    return session

# -----------------------------
# 交通事件：下載 + 分組（關鍵字）
# -----------------------------
def download_news(session: requests.Session = None) -> pd.DataFrame:
    """抓取 News.xml（僅國道一號），回傳 DataFrame。"""
    if session is None:
        session = make_session()
    resp = session.get(NEWS_URL, timeout=10)
    resp.raise_for_status()
    xml_text = resp.text

    root = ET.fromstring(xml_text)
    live_events = root.find("LiveEvents")
    if live_events is None:
        return pd.DataFrame()

    events: List[Dict] = []
    for event in live_events.findall("LiveEvent"):
        road = event.findtext("Location/FreeExpressHighway/Road", default='') or ''
        if road != "國道一號":
            continue

        section_start = event.findtext("Location/FreeExpressHighway/SectionStart", default='') or ''
        section_end   = event.findtext("Location/FreeExpressHighway/SectionEnd",   default='') or ''

        row = {
            "EventID": event.findtext("EventID", default='') or '',
            "Title": event.findtext("EventTitle", default='') or '',
            "Description": event.findtext("Description", default='') or '',
            "EffectiveTime": event.findtext("EffectiveTime", default='') or '',
            "Position": event.findtext("Positions", default='') or '',
            "Road": road,
            "Direction": event.findtext("Location/FreeExpressHighway/Direction", default='') or '',
            "SectionStart": section_start,
            "SectionEnd": section_end,
            "ImpactDescription": event.findtext("Impact/Description", default='') or '',
            "Severity": event.findtext("Impact/Severity", default='') or '',
            "BlockedLanes": event.findtext("Impact/BlockedLanes", default='') or '',
            "Source": event.findtext("Source", default='') or '',
            "PublishTime": event.findtext("PublishTime", default='') or '',
            "LastUpdateTime": event.findtext("LastUpdateTime", default='') or '',
        }
        events.append(row)

    df = pd.DataFrame(events)

    # 時間欄位轉 datetime & 去重 & 排序
    for col in ("EffectiveTime", "PublishTime", "LastUpdateTime"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")

    if "EventID" in df.columns:
        df = df.drop_duplicates(subset=["EventID"])

    if "EffectiveTime" in df.columns:
        df = df.sort_values(by=["EffectiveTime"], ascending=False, na_position="last").reset_index(drop=True)

    return df

def group_by_keywords(df: pd.DataFrame, keywords: List[str]) -> Dict[str, List[Dict]]:
    """依 SectionStart/SectionEnd 是否包含任一關鍵字進行分類，回傳 JSON-ready dict。"""
    normalized_keywords = [normalize_name(k) for k in keywords]
    result: Dict[str, List[Dict]] = {k: [] for k in keywords}

    for _, row in df.iterrows():
        ns = normalize_name(row.get("SectionStart", ""))
        ne = normalize_name(row.get("SectionEnd", ""))
        for orig_kw, norm_kw in zip(keywords, normalized_keywords):
            if norm_kw and (norm_kw in ns or norm_kw in ne):
                result[orig_kw].append({
                    "EventID": row.get("EventID", ""),
                    "Title": row.get("Title", ""),
                    "Description": row.get("Description", ""),
                    "Direction": row.get("Direction", ""),
                    "SectionStart": row.get("SectionStart", ""),
                    "SectionEnd": row.get("SectionEnd", ""),
                    "EffectiveTime": row.get("EffectiveTime").isoformat() if pd.notna(row.get("EffectiveTime")) else "",
                    "PublishTime": row.get("PublishTime").isoformat() if pd.notna(row.get("PublishTime")) else "",
                    "LastUpdateTime": row.get("LastUpdateTime").isoformat() if pd.notna(row.get("LastUpdateTime")) else "",
                    "Impact": {
                        "Description": row.get("ImpactDescription", ""),
                        "Severity": row.get("Severity", ""),
                        "BlockedLanes": row.get("BlockedLanes", ""),
                    },
                    "Source": row.get("Source", ""),
                })

    # 段內排序
    for kw in result:
        result[kw].sort(key=lambda x: x.get("EffectiveTime", ""), reverse=True)

    return result

def get_news_by_keywords_json(keywords: List[str] = None, session: requests.Session = None) -> Dict[str, List[Dict]]:
    """高公局 News.xml → 關鍵字分組 → 回傳 JSON（不寫檔）。"""
    if keywords is None:
        keywords = SEGMENT_KEYWORDS
    df = download_news(session=session)
    return group_by_keywords(df, keywords)

# -----------------------------
# 楠梓區未來 24h 降雨機率（JSON）
# -----------------------------
def get_rain_forecast_json(session: requests.Session = None) -> List[Dict]:
    """
    取得高雄市楠梓區未來 24 小時降雨機率（逐小時）。
    回傳 list[dict]：[{ "時間": "...", "降雨機率": 0-100 }, ...]
    """
    if session is None:
        session = make_session()

    # CWA 模組需要 ?T=YYYYmmddHH-M（分鐘十位數）
    now = datetime.now()  # 以系統時區（Asia/Taipei）為準
    tstamp = f"{now.year}{now.month:02d}{now.day:02d}{now.hour:02d}-{now.minute // 10}"
    url = CWA_TOWN_PC_URL_TMPL.format(tstamp=tstamp)

    res = session.get(url, timeout=10)
    res.raise_for_status()
    soup = BeautifulSoup(res.text, "html.parser")

    # 時間列
    time_row = soup.select_one("tr.time")
    if time_row is None:
        raise ValueError("找不到時間列（tr.time）")
    time_cells = time_row.find_all(["th", "td"])[1:]  # 跳過第一格標題
    times = [c.get_text(strip=True) for c in time_cells if c.get_text(strip=True)]

    # 降雨機率列（th#PC3_Po 所在的 tr）
    po_row = None
    for tr in soup.find_all("tr"):
        th = tr.find("th", id="PC3_Po")
        if th:
            po_row = tr
            break
    if po_row is None:
        raise ValueError("找不到降雨機率資料列（th#PC3_Po）")

    # 展開 colspan 成逐時資料
    po_cells = po_row.find_all("td")
    expanded_po: List[int] = []
    for td in po_cells:
        colspan = int(td.get("colspan", 1))
        txt = td.get_text(strip=True).replace("%", "") or "0"
        try:
            value = int(txt)
        except Exception:
            value = 0
        expanded_po.extend([value] * colspan)

    # 對齊長度，只取 24 筆
    length = min(len(times), len(expanded_po))
    times = times[:length]
    expanded_po = expanded_po[:length]
    df = pd.DataFrame({"時間": times, "降雨機率": expanded_po})
    if len(df) > 24:
        df = df.iloc[:24].reset_index(drop=True)

    return df.to_dict(orient="records")

# -----------------------------
# 服務整合（一次拿到兩者）
# -----------------------------
def get_traffic_weather_service(session: requests.Session = None) -> Dict[str, object]:
    """
    一次取得：
      - 楠梓區 24h 降雨：'rain_24h' -> list[dict]
      - 國道一號事件（關鍵字分組）：'news_by_keywords' -> dict[str, list[dict]]
    """
    if session is None:
        session = make_session()
    rain = get_rain_forecast_json(session=session)
    news = get_news_by_keywords_json(session=session)
    return {
        "rain_24h": rain,
        "news_by_keywords": news
    }

# -----------------------------
# 測試
# -----------------------------
# if __name__ == "__main__":
#     # 單獨取其中一個
#     # print(json.dumps(get_rain_forecast_json(), ensure_ascii=False, indent=2))
#     # print(json.dumps(get_news_by_keywords_json(), ensure_ascii=False, indent=2))

#     # 一次全部
#     data = get_traffic_weather_service()
#     print(json.dumps(data, ensure_ascii=False, indent=2))
