import streamlit as st
import json
import os
import re
import asyncio
import edge_tts
import hashlib
from datetime import datetime, timedelta
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
st.write("คลังข่าวสารและสรุปประเด็นสำคัญ (เตรียมสอบ กต. - โหลดไว เสถียรสูงสุด)")

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
        # ข้อมูลตั้งต้นเผื่อฐานข้อมูลว่าง เพื่อให้เปิดหน้าเว็บติดทันที
        return {
            "https://www.mfa.go.th/sample-news": {
                "title": "ยินดีต้อนรับสู่ Diplomat News Hub (กดปุ่มสรุป AI เพื่อเริ่มใช้งาน)",
                "link": "https://www.mfa.go.th",
                "source": "🇹🇭 MFA - ข่าวสารนิเทศ / ข่าวเด่น",
                "added_date": datetime.now().strftime("%Y-%m-%d"),
                "is_read": False,
                "analysis": None
            }
        }
    try:
        with open(DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_db(data):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

db = load_db()

# News Sources definition
rss_sources = {
    "🇹🇭 MFA - สรุปแถลงข่าวประจำสัปดาห์": "MFA_WEEKLY",
    "🇹🇭 MFA - ข่าวสารนิเทศ / ข่าวเด่น": "MFA_NEWS",
    "BBC Thai (ข่าวบีบีซีไทย)": "BBC_THAI",
    "Thailand News (Thai PBS)": "THAILAND_NEWS",
    "ASEAN / Asia News (CNA)": "ASEAN_NEWS",
    "World News (BBC)": "WORLD_NEWS"
}

# Sidebar Controls
st.sidebar.header("📌 ตัวกรองข่าว & ตั้งค่า")
selected_source = st.sidebar.selectbox("เลือกแหล่งข่าว:", list(rss_sources.keys()))
read_filter = st.sidebar.radio("สถานะการอ่าน:", ["ทั้งหมด", "ยังไม่ได้อ่านเท่านั้น", "อ่านแล้วเท่านั้น"])
keyword_filter = st.sidebar.text_input("ค้นหาด้วยคีย์เวิร์ด:", "")

# ---------------------------------------------------------
# Gemini Summarizer Engine
# ---------------------------------------------------------
def analyze_headline(headline_title, is_mfa_source, is_thai_source):
    client = get_gemini_client(API_KEY)
    audio_language_instruction = "NATURAL THAI LANGUAGE" if is_thai_source else "ENGLISH"

    if is_mfa_source:
        formatting_rules = """
        Language for OUTPUT 1: THAI ONLY.
        CRITICAL MFA FORMATTING RULES:
        - DO NOT put any relevance score.
        - Shorten official titles immediately (e.g., 'รองนายกรัฐมนตรีและรัฐมนตรีว่าการกระทรวงการต่างประเทศ' -> 'รมว.กต.').
        - Extract concrete diplomatic facts specifically answering:
          1. **ผู้แทน & เวทีประชุม:** (ใครเข้าร่วม ประชุมอะไร ครั้งที่เท่าไหร่)
          2. **ประเด็นสาระสำคัญ:** (ข้อเท็จจริง สิ่งที่หารือ หรือผลการประชุม)
          3. **จุดยืน/บทบาทพิเศษของไทย:** (ไทยชูคอนเซ็ปต์อะไร เช่น Bridge Builder, การสมัครสมาชิก UNHCR)

        OUTPUT 1 FORMAT:
        - **ผู้แทน & เวทีประชุม:** [สรุปผู้แทนและเวที]
        - **ประเด็นสาระสำคัญ:** [ข้อเท็จจริง สาระการหารือ]
        - **จุดยืน/บทบาทพิเศษของไทย:** [บทบาทและคอนเซ็ปต์ของไทย]
        """
    elif is_thai_source:
        formatting_rules = """
        Language for OUTPUT 1: THAI ONLY.
        OUTPUT 1 FORMAT:
        - **คะแนนความน่าสนใจออกสอบ (ภาค ก):** [1-10]
        - **หมวดหมู่ข่าว:** [หมวดหมู่]
        - **ประเด็นสำคัญ/สาระข่าว:** [สรุปเน้นข้อเท็จจริง]
        """
    else:
        formatting_rules = """
        Language for OUTPUT 1: ENGLISH ONLY.
        OUTPUT 1 FORMAT:
        - **General Knowledge Relevance:** [1-10]
        - **Category:** [Category]
        - **Key Facts & Bulletins:** [State precise facts]
        """

    prompt = f"""
    You are an expert news editor and diplomatic briefing assistant.
    Analyze this headline: "{headline_title}"

    {formatting_rules}

    Generate two distinct outputs strictly separated by the marker "===AUDIO_SCRIPT===":

    OUTPUT 1 (ON-SCREEN TEXT):
    [Follow formatting rule strictly.]

    ===AUDIO_SCRIPT===

    OUTPUT 2 (SPOKEN AUDIO SCRIPT):
    Write a clear, professional spoken briefing for headphone listening in {audio_language_instruction}.
    - Do NOT read out metadata, section titles, or scores.
    - If THAI, write purely in native Thai script. Abbreviate titles naturally.
    """
    
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

async def generate_audio_file(text, output_path, voice_id):
    communicate = edge_tts.Communicate(text, voice=voice_id)
    await communicate.save(output_path)

# ---------------------------------------------------------
# Main Application Flow (Pure Local DB - Never Hangs)
# ---------------------------------------------------------
try:
    is_mfa = "MFA" in selected_source
    is_thai = is_mfa or ("Thai" in selected_source)

    # Filter DB items for selected source
    source_items = [item for item in db.values() if item.get("source") == selected_source]
    source_items.sort(key=lambda x: x.get("added_date", ""), reverse=True)

    st.header(f"📰 ข่าวสาร: {selected_source}")

    # ช่องทางเพิ่มข่าวใหม่ด้วยตนเองแบบปลอดภัย (ไม่ทำให้แอปค้าง)
    with st.expander("➕ เพิ่มลิงก์ข่าว / หัวข้อข่าวใหม่เพื่อสรุป"):
        with st.form("add_news_form"):
            new_title = st.text_input("หัวข้อข่าว หรือ พาดหัวข่าว:")
            new_link = st.text_input("ลิงก์ข่าว (URL ถ้ามี):", value="https://www.mfa.go.th")
            submitted = st.form_submit_button("บันทึกเข้าระบบ")
            if submitted and new_title:
                if new_link not in db:
                    db[new_link] = {
                        "title": new_title,
                        "link": new_link,
                        "source": selected_source,
                        "added_date": datetime.now().strftime("%Y-%m-%d"),
                        "is_read": False,
                        "analysis": None
                    }
                    save_db(db)
                    st.success("เพิ่มข่าวสำเร็จ! รีเฟรชหน้าจอเพื่อดูรายการ")
                    st.rerun()

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
            st.markdown(f"
