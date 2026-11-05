import streamlit as st
import xml.etree.ElementTree as ET
import urllib.request
import asyncio
import edge_tts
import re
import requests
import json
import os
import hashlib
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from google import genai

# ---------------------------------------------------------
# Secure API Key Configuration
# ---------------------------------------------------------
API_KEY = st.secrets.get("GEMINI_API_KEY", os.environ.get("GEMINI_API_KEY", ""))

DB_FILE = "news_db.json"
AUDIO_DIR = "audio_cache"

os.makedirs(AUDIO_DIR, exist_ok=True)

st.set_page_config(page_title="My Diplomat News Hub", layout="wide")
st.title("My Diplomat News Hub")
st.write("คลังข่าวสารและสรุปประเด็นสำคัญ (เตรียมสอบ กต. - โหลดเร็ว ไม่ค้าง)")

if not API_KEY:
    st.error("⚠️ ไม่พบ GEMINI_API_KEY กรุณาตั้งค่า Secrets ในหลังบ้าน Streamlit Cloud ก่อนใช้งาน")
    st.stop()

@st.cache_resource
def get_gemini_client(key):
    return genai.Client(api_key=key)

# ---------------------------------------------------------
# Local JSON Database Engine
# ---------------------------------------------------------
def load_db():
    if not os.path.exists(DB_FILE):
        return {}
    try:
        with open(DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_db(data):
    cutoff_date = datetime.now() - timedelta(days=10)
    filtered_db = {}
    for link, item in data.items():
        try:
            item_date = datetime.strptime(item.get("added_date", datetime.now().strftime("%Y-%m-%d")), "%Y-%m-%d")
            if item_date >= cutoff_date:
                filtered_db[link] = item
            else:
                analysis_info = item.get("analysis") or {}
                audio_file = analysis_info.get("audio_file")
                if audio_file and os.path.exists(audio_file):
                    os.remove(audio_file)
        except Exception:
            filtered_db[link] = item
            
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(filtered_db, f, ensure_ascii=False, indent=2)

db = load_db()

# News Sources
rss_sources = {
    "🇹🇭 MFA - สรุปแถลงข่าวประจำสัปดาห์": "https://www.mfa.go.th/th/content-category/%E0%B8%AA%E0%B8%A3%E0%B8%B8%E0%B8%9B%E0%B9%81%E0%B8%96%E0%B8%A5%E0%B8%87%E0%B8%82%E0%B9%88%E0%B8%B2%E0%B8%A7%E0%B8%9B%E0%B8%A3%E0%B8%B0%E0%B8%88%E0%B8%B3%E0%B8%AA%E0%B8%B1%E0%B8%9B%E0%B8%94%E0%B8%B2%E0%B8%AB%E0%B9%8C",
    "🇹🇭 MFA - ข่าวสารนิเทศ / ข่าวเด่น": "https://www.mfa.go.th/th/content-category/%E0%B8%82%E0%B9%88%E0%B8%B2%E0%B8%AA%E0%B8%B2%E0%B8%A3%E0%B8%B8%E0%B8%9B%E0%B9%81%E0%B8%96%E0%B8%A5%E0%B8%87%E0%B8%82%E0%B9%88%E0%B8%B2%E0%B8%A7%E0%B8%9B%E0%B8%A3%E0%B8%B0%E0%B8%88%E0%B8%B3%E0%B8%AA%E0%B8%B1%E0%B8%9B%E0%B8%94%E0%B8%B2%E0%B8%AB%E0%B9%8C",
    "BBC Thai (ข่าวบีบีซีไทย)": "https://news.google.com/rss/search?q=site:bbc.com/thai&hl=th&gl=TH&ceid=TH:th",
    "Thailand News (Thai PBS)": "https://news.google.com/rss/search?q=site:thaipbs.or.th&hl=th&gl=TH&ceid=TH:th",
    "ASEAN / Asia News (CNA)": "https://www.channelnewsasia.com/api/v1/rss-outbound-feed?_format=xml&category=6511",
    "World News (BBC)": "http://feeds.bbci.co.uk/news/world/rss.xml"
}

# Sidebar Controls
