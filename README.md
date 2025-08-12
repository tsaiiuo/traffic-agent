# 台南-高雄道路通行智慧助理 (Flask + Gemini API)

本專案是一個基於 **Flask** 與 **Google Gemini API** 的智慧道路助理服務，  
會自動整合 **高公局國道事件新聞** 與 **中央氣象署未來 24 小時降雨預報（高雄市楠梓區）**，  
提供即時路況背景，並結合使用者的提問，回覆道路壅塞狀況、新聞提醒與壅塞預測。

---

## 功能特色

- **即時天氣**：抓取中央氣象署楠梓區未來 24 小時逐小時降雨機率。
- **道路新聞**：抓取高公局 `News.xml`，依指定關鍵字（路段名稱）分組整理近期事件。
- **智慧回答**：將天氣與新聞資料餵入 Google Gemini 模型，依指令回覆道路狀況與預測。
- **REST API 介面**：
  - `/init`：初始化對話，將最新資料送入模型作為上下文。
  - `/chat`：與模型進行對話問答。

---

## 專案結構

project:
 -  app.py # Flask 主程式（API 入口）
 -  util.py # 天氣/新聞整合服務
 -  requirements.txt # 套件需求清單
 -  README.md # 專案說明文件

---

## 安裝與執行

### 1. 建立虛擬環境

```bash
python3 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt

flask
google-generativeai
pandas
beautifulsoup4
requests
urllib3
```
