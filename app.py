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

# ---------------------------------------------------------
# ALWAYS RENDER SIDEBAR FIRST (ไม่โดนข้ามแน่นอน)
# ---------------------------------------------------------
st.sidebar.header("📌 ตัวกรองข่าว & ตั้งค่า")
selected_source = st.sidebar.selectbox("เลือกแหล่งข่าว:", list(rss_sources.keys()))
read_filter = st.sidebar.radio("สถานะการอ่าน:", ["ทั้งหมด", "ยังไม่ได้อ่านเท่านั้น", "อ่านแล้วเท่านั้น"])
keyword_filter = st.sidebar.text_input("ค้นหาด้วยคีย์เวิร์ด:", "")

selected_url = rss_sources[selected_source]

# ---------------------------------------------------------
# Gemini Summarizer Engine
# ---------------------------------------------------------
def analyze_headline(headline_title, is_mfa_source, is_thai_source):
    client = get_gemini_client(API_KEY)
    audio_lang = "NATURAL THAI LANGUAGE" if is_thai_source else "ENGLISH"

    if is_mfa_source:
        formatting_rules = (
            "Language for OUTPUT 1: THAI ONLY.\n"
            "CRITICAL MFA FORMATTING RULES:\n"
            "- DO NOT put any relevance score.\n"
            "- Shorten official titles immediately (e.g., 'รองนายกรัฐมนตรีและรัฐมนตรีว่าการกระทรวงการต่างประเทศ' -> 'รมว.กต.').\n"
            "- Extract concrete diplomatic facts specifically answering:\n"
            "  1. **ผู้แทน & เวทีประชุม:** (ใครเข้าร่วม ประชุมอะไร ครั้งที่เท่าไหร่)\n"
            "  2. **ประเด็นสาระสำคัญ:** (ข้อเท็จจริง สิ่งที่หารือ หรือผลการประชุม)\n"
            "  3. **จุดยืน/บทบาทพิเศษของไทย:** (ไทยชูคอนเซ็ปต์อะไร เช่น Bridge Builder, การสมัครสมาชิก UNHCR)\n\n"
            "OUTPUT 1 FORMAT:\n"
            "- **ผู้แทน & เวทีประชุม:** [สรุปผู้แทนและเวที]\n"
            "- **ประเด็นสาระสำคัญ:** [ข้อเท็จจริง สาระการหารือ]\n"
            "- **จุดยืน/บทบาทพิเศษของไทย:** [บทบาทและคอนเซ็ปต์ของไทย]"
        )
    elif is_thai_source:
        formatting_rules = (
            "Language for OUTPUT 1: THAI ONLY.\n"
            "OUTPUT 1 FORMAT:\n"
            "- **คะแนนความน่าสนใจออกสอบ (ภาค ก):** [1-10]\n"
            "- **หมวดหมู่ข่าว:** [เช่น กีฬา, เศรษฐกิจ, สังคม, เหตุการณ์สำคัญ]\n"
            "- **ประเด็นสำคัญ/สาระข่าว:** [สรุปเน้นข้อเท็จจริง ใคร ทำอะไร ที่ไหน ตัวเลข หรือเกร็ดความรู้]"
        )
    else:
        formatting_rules = (
            "Language for OUTPUT 1: ENGLISH ONLY.\n"
            "OUTPUT 1 FORMAT:\n"
            "- **General Knowledge Relevance:** [1-10]\n"
            "- **Category:** [e.g., Regional Affairs, Global Economy]\n"
            "- **Key Facts & Bulletins:** [State precise facts, dates, names, numbers, decisions.]"
        )

    prompt = (
        f"You are an expert news editor and diplomatic briefing assistant.\n"
        f"Analyze this headline: '{headline_title}'\n\n"
        f"{formatting_rules}\n\n"
        f"Generate two distinct outputs strictly separated by the marker '===AUDIO_SCRIPT===':\n\n"
        f"OUTPUT 1 (ON-SCREEN TEXT):\n[Follow formatting rule strictly.]\n\n"
        f"===AUDIO_SCRIPT===\n\n"
        f"OUTPUT 2 (SPOKEN AUDIO SCRIPT):\n"
        f"Write a clear, professional spoken briefing for headphone listening in {audio_lang}.\n"
        f"- Do NOT read out metadata, section titles, or scores.\n"
        f"- If THAI, write purely in native Thai script. Abbreviate titles naturally."
    )
    
    models_to_try = ['gemini-2.5-flash', 'gemini-2.5-flash-lite']
    for model_name in models_to_try:
        try:
            response = client.models.generate_content(model=model_name, contents=prompt)
            return response.text
        except Exception:
            continue
            
    return "AI Analysis Error: Unable to summarize at this moment."

def split_analysis(full_text):
    if "===AUDIO_SCRIPT===" in full_text:
        parts = full_text.split("===AUDIO_SCRIPT===")
        return parts[0].strip(), parts[1].strip()
    return full_text, full_text

def scrape_mfa_news(url):
    headers = {"User-Agent": "Mozilla/5.0"}
    ignored_keywords = ["ประวัติ", "โครงสร้าง", "นโยบาย", "ภารกิจ", "อำนาจหน้าที่", "ผู้บริหาร", "ติดต่อ", "คำถามที่พบบ่อย"]
    try:
        response = requests.get(url, headers=headers, timeout=5)
        response.encoding = 'utf-8'
        soup = BeautifulSoup(response.text, 'html.parser')
        articles = []
        for card in soup.find_all('a', href=True):
            href = card['href']
            if '/content/' in href:
                title = card.get_text(strip=True)
                if title and len(title) > 12:
                    if not any(bad_word in title for bad_word in ignored_keywords):
                        full_link = f"https://www.mfa.go.th{href}" if href.startswith('/') else href
                        if not any(a['link'] == full_link for a in articles):
                            articles.append({"title": title, "link": full_link})
            if len(articles) >= 5:
                break
        return articles
    except Exception:
        return []

def parse_rss_feed(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    xml_data = urllib.request.urlopen(req, timeout=5).read()
    root = ET.fromstring(xml_data)
    articles = []
    for item in root.findall('./channel/item'):
        title = item.find('title').text if item.find('title') is not None else "No Title"
        link = item.find('link').text if item.find('link') is not None else "#"
        articles.append({"title": title, "link": link})
        if len(articles) >= 5:
            break
    return articles

async def generate_audio_file(text, output_path, voice_id):
    communicate = edge_tts.Communicate(text, voice=voice_id)
    await communicate.save(output_path)

# ---------------------------------------------------------
# Main Application Flow
# ---------------------------------------------------------
is_mfa = "MFA" in selected_source
is_thai = is_mfa or ("Thai" in selected_source)

col_head, col_fetch = st.columns([3, 1])
with col_head:
    st.header(f"📰 ข่าวสาร: {selected_source}")
with col_fetch:
    if st.button("🔄 ดึงข่าวใหม่ล่าสุด", use_container_width=True):
        with st.spinner("กำลังเชื่อมต่อดึงข่าวใหม่..."):
            fresh_articles = scrape_mfa_news(selected_url) if is_mfa else parse_rss_feed(selected_url)
            today_str = datetime.now().strftime("%Y-%m-%d")
            db_updated = False
            for art in fresh_articles:
                link = art['link']
                title = art['title']
                if link not in db:
                    db_updated = True
                    db[link] = {
                        "title": title,
                        "link": link,
                        "source": selected_source,
                        "added_date": today_str,
                        "is_read": False,
                        "analysis": None
                    }
            if db_updated:
                save_db(db)
            st.rerun()

# Filter DB items
source_items = [item for item in db.values() if item.get("source") == selected_source]
source_items.sort(key=lambda x: x.get("added_date", ""), reverse=True)

displayed_count = 0
for item in source_items:
    title = item['title']
    link = item['link']
    is_read = item.get('is_read', False)
    
    if read_filter == "ยังไม่ได้อ่านเท่านั้น" and is_read:
        continue
    if read_filter == "อ่านแล้วเท่านั้น" and not is_read:
        continue
        
    if keyword_filter and keyword_filter.lower() not in title.lower():
        continue

    displayed_count += 1
    
    status_badge = "✅ อ่านแล้ว" if is_read else "🔵 ข่าวใหม่"
    date_badge = item.get("added_date", "")
    
    col_title, col_btn = st.columns([4, 1])
    with col_title:
        st.markdown(f"### • [{title}]({link})")
        st.caption(f"วันที่บันทึก: {date_badge} | สถานะ: {status_badge}")
    
    with col_btn:
        btn_label = "Mark Unread" if is_read else "✓ อ่านแล้ว"
        if st.button(btn_label, key=f"read_btn_{link}"):
            db[link]['is_read'] = not is_read
            save_db(db)
            st.rerun()

    analysis_data = item.get("analysis")
    
    if not analysis_data:
        if st.button("✨ สรุปเนื้อหาด้วย AI", key=f"sum_btn_{link}"):
            with st.spinner("Gemini กำลังวิเคราะห์ประเด็นข่าว..."):
                raw_analysis = analyze_headline(title, is_mfa, is_thai)
                screen_text, audio_script = split_analysis(raw_analysis)
                
                db[link]['analysis'] = {
                    "screen_text": screen_text,
                    "audio_script": audio_script,
                    "audio_file": None
                }
                save_db(db)
                st.rerun()
    else:
        screen_text = analysis_data.get("screen_text")
        audio_script = analysis_data.get("audio_script", "")
        
        if screen_text:
            st.info(screen_text)

        if st.button("🔊 ฟังเสียงสรุปข่าว", key=f"audio_btn_{link}"):
            with st.spinner("กำลังเตรียมเสียงบรรยาย..."):
                clean_audio_text = re.sub(r'[*#]', '', audio_script)
                link_hash = hashlib.md5(link.encode('utf-8')).hexdigest()[:10]
                audio_filename = os.path.join(AUDIO_DIR, f"audio_{link_hash}.mp3")
                voice_id = "th-TH-NiwatNeural" if is_thai else "en-GB-RyanNeural"
                
                try:
                    asyncio.run(generate_audio_file(clean_audio_text, audio_filename, voice_id))
                    if os.path.exists(audio_filename):
                        with open(audio_filename, "rb") as audio_bytes:
                            st.audio(audio_bytes.read(), format="audio/mp3")
                except Exception:
                    st.error("ไม่สามารถสร้างไฟล์เสียงได้ในขณะนี้")

    st.write("---")

if displayed_count == 0:
    st.info("ยังไม่มีข่าวในหมวดหมู่นี้ในคลัง สามารถกดปุ่ม '🔄 ดึงข่าวใหม่ล่าสุด' มุมขวาบนเพื่ออัปเดตข่าวใหม่ได้ทันทีครับ")
