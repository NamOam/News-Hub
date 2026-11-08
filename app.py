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
import base64

# ---------------------------------------------------------
# Secure API Key & GitHub Config
# ---------------------------------------------------------
API_KEY = st.secrets.get("GEMINI_API_KEY", os.environ.get("GEMINI_API_KEY", ""))
GITHUB_TOKEN = st.secrets.get("GITHUB_TOKEN", os.environ.get("GITHUB_TOKEN", ""))
GITHUB_REPO = st.secrets.get("GITHUB_REPO", "NamOam/News-Hub")

NEWS_DB_FILE = "news_db.json"
USER_STATUS_FILE = "read_status.json"
AUDIO_DIR = "audio_cache"

os.makedirs(AUDIO_DIR, exist_ok=True)

st.set_page_config(page_title="My Diplomat News Hub", layout="wide")
st.title("My Diplomat News Hub")
st.write("คลังข่าวสารและสรุปประเด็นสำคัญ (เตรียมสอบ กต. - ระบบแยกสมุดจดสถานะ ซิงค์ถาวร)")

if not API_KEY:
    st.error("⚠️ ไม่พบ GEMINI_API_KEY กรุณาตั้งค่า Secrets ในหลังบ้าน Streamlit Cloud ก่อนใช้งาน")
    st.stop()

@st.cache_resource
def get_gemini_client(key):
    return genai.Client(api_key=key)

# ---------------------------------------------------------
# Dual Storage Engine
# ---------------------------------------------------------
def load_json_file(filename):
    if os.path.exists(filename):
        try:
            with open(filename, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_local_json(filename, data):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_user_status():
    if GITHUB_TOKEN and GITHUB_REPO:
        try:
            url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{USER_STATUS_FILE}"
            headers = {"Authorization": f"token {GITHUB_TOKEN}"}
            res = requests.get(url, headers=headers, timeout=3)
            if res.status_code == 200:
                content_json = json.loads(base64.b64decode(res.json()['content']).decode('utf-8'))
                save_local_json(USER_STATUS_FILE, content_json)
                return content_json
        except Exception:
            pass
    return load_json_file(USER_STATUS_FILE)

def sync_user_status_to_github(data):
    save_local_json(USER_STATUS_FILE, data)
    
    if GITHUB_TOKEN and GITHUB_REPO:
        try:
            url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{USER_STATUS_FILE}"
            headers = {"Authorization": f"token {GITHUB_TOKEN}"}
            
            get_res = requests.get(url, headers=headers, timeout=3)
            sha = get_res.json().get('sha') if get_res.status_code == 200 else None
            
            file_content_str = json.dumps(data, ensure_ascii=False, indent=2)
            encoded_content = base64.b64encode(file_content_str.encode('utf-8')).decode('utf-8')
            
            payload = {
                "message": "Auto-sync user read status [skip ci]",
                "content": encoded_content,
                "branch": "main"
            }
            if sha:
                payload["sha"] = sha
                
            requests.put(url, headers=headers, json=payload, timeout=3)
        except Exception:
            pass

news_db = load_json_file(NEWS_DB_FILE)
user_status = load_user_status()

# ---------------------------------------------------------
# RSS & MFA Scrapers
# ---------------------------------------------------------
rss_sources = {
    "BBC Thai (ข่าวบีบีซีไทย)": "https://news.google.com/rss/search?q=site:bbc.com/thai&hl=th&gl=TH&ceid=TH:th",
    "Thailand News (Thai PBS)": "https://news.google.com/rss/search?q=site:thaipbs.or.th&hl=th&gl=TH&ceid=TH:th",
    "ASEAN / Asia News (CNA)": "https://www.channelnewsasia.com/api/v1/rss-outbound-feed?_format=xml&category=6511",
    "World News (BBC)": "http://feeds.bbci.co.uk/news/world/rss.xml",
    "🇹🇭 MFA - สรุปแถลงข่าวประจำสัปดาห์": "https://www.mfa.go.th/th/content-category/%E0%B8%AA%E0%B8%A3%E0%B8%B8%E0%B8%9B%E0%B9%81%E0%B8%96%E0%B8%A5%E0%B8%87%E0%B8%82%E0%B9%88%E0%B8%B2%E0%B8%A7%E0%B8%9B%E0%B8%A3%E0%B8%B0%E0%B8%88%E0%B8%B3%E0%B8%AA%E0%B8%B1%E0%B8%9B%E0%B8%94%E0%B8%B2%E0%B8%AB%E0%B9%8C",
    "🇹🇭 MFA - ข่าวสารนิเทศ / ข่าวเด่น": "https://www.mfa.go.th/th/content-category/%E0%B8%82%E0%B9%88%E0%B8%B2%E0%B8%AA%E0%B8%B2%E0%B8%A3%E0%B8%B8%E0%B8%9B%E0%B9%81%E0%B8%96%E0%B8%A5%E0%B8%87%E0%B8%82%E0%B9%88%E0%B8%B2%E0%B8%A7"
}

def clean_html(raw_html):
    if not raw_html:
        return ""
    cleantext = re.sub(re.compile('<.*?>'), '', raw_html)
    return cleantext.strip()

def scrape_mfa_web(url, source_name):
    items = []
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        req = urllib.request.Request(url, headers=headers)
        html = urllib.request.urlopen(req, timeout=8).read().decode('utf-8')
        soup = BeautifulSoup(html, 'html.parser')
        
        cards = soup.find_all('div', class_=re.compile(r'content-card|article-card|news-card|card'))
        if not cards:
            cards = soup.find_all('a', href=re.compile(r'/th/content/'))

        for card in cards[:15]:
            title, link, pub_date = "", "", ""
            if card.name == 'a':
                link = card['href']
                title = card.get_text(strip=True)
            else:
                a_tag = card.find('a', href=True)
                if a_tag:
                    link = a_tag['href']
                    title = a_tag.get_text(strip=True)
                title_tag = card.find(re.compile(r'h[1-6]'))
                if title_tag:
                    title = title_tag.get_text(strip=True)

            if link and not link.startswith('http'):
                link = "https://www.mfa.go.th" + link

            date_tag = card.find(class_=re.compile(r'date|time'))
            if date_tag:
                pub_date = date_tag.get_text(strip=True)

            if title and link:
                item_id = hashlib.md5(link.encode('utf-8')).hexdigest()
                items.append({
                    "id": item_id,
                    "title": title,
                    "link": link,
                    "pub_date": pub_date or "MFA Website",
                    "description": title,
                    "source": source_name
                })
    except Exception:
        pass
    return items

def fetch_rss_feed(url, source_name):
    if "mfa.go.th" in url:
        return scrape_mfa_web(url, source_name)
    items = []
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        xml_data = urllib.request.urlopen(req, timeout=5).read()
        root = ET.fromstring(xml_data)
        for item in root.findall('.//item')[:15]:
            title = item.find('title').text if item.find('title') is not None else "No Title"
            link = item.find('link').text if item.find('link') is not None else ""
            pub_date = item.find('pubDate').text if item.find('pubDate') is not None else ""
            desc = item.find('description').text if item.find('description') is not None else ""
            item_id = hashlib.md5(link.encode('utf-8')).hexdigest()
            items.append({
                "id": item_id,
                "title": title,
                "link": link,
                "pub_date": pub_date,
                "description": clean_html(desc),
                "source": source_name
            })
    except Exception:
        pass
    return items

def update_all_news():
    all_new_items = {}
    for name, url in rss_sources.items():
        fetched = fetch_rss_feed(url, name)
        for item in fetched:
            all_new_items[item['id']] = item
    news_db.update(all_new_items)
    save_local_json(NEWS_DB_FILE, news_db)

# ---------------------------------------------------------
# Web Article Scraper & Gemini AI Summarizer
# ---------------------------------------------------------
def scrape_full_article(url):
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        html = urllib.request.urlopen(req, timeout=6).read().decode('utf-8')
        soup = BeautifulSoup(html, 'html.parser')
        paragraphs = soup.find_all('p')
        text = ' '.join([p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 30])
        return text[:4000] if text else ""
    except Exception:
        return ""

def generate_summary(title, description, full_text):
    client = get_gemini_client(API_KEY)
    content_to_analyze = full_text if len(full_text) > 200 else f"{title}\n{description}"
    prompt = f"""
    คุณคือผู้เชี่ยวชาญด้านการต่างประเทศและการวิเคราะห์ข่าวกรอง (Diplomatic Intelligence Analyst)
    โปรดสรุปข่าวต่อไปนี้เพื่อเตรียมสอบนักการทูต / กระทรวงการต่างประเทศ (มุ่งเน้นสาระสำคัญ นโยบายต่างประเทศ และผลกระทบเชิงยุทธศาสตร์):
    
    หัวข้อข่าว: {title}
    เนื้อหาข่าว: {content_to_analyze}
    
    กรุณาสรุปเป็นภาษาไทย 3 หัวข้อหลัก ดังนี้:
    1. 📌 สรุปสาระสำคัญ (Core Summary - ไม่เกิน 3 บรรทัด)
    2. 🌐 บริบททางการเมือง/การต่างประเทศ/เศรษฐกิจ (Diplomatic & Strategic Context)
    3. 💡 ประเด็นวิเคราะห์สำหรับสอบ กต. (Key Takeaways for MFA Exam)
    """
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt
    )
    return response.text

# ---------------------------------------------------------
# TTS Audio Generator
# ---------------------------------------------------------
async def text_to_speech_async(text, output_file):
    voice = "th-TH-NiwatNeural" if re.search(r'[\u0E00-\u0E7F]', text) else "en-US-ChristopherNeural"
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(output_file)

def get_audio_file(text, item_id):
    clean_text_for_audio = re.sub(r'[*#_`]', '', text)
    file_path = os.path.join(AUDIO_DIR, f"{item_id}.mp3")
    if not os.path.exists(file_path):
        asyncio.run(text_to_speech_async(clean_text_for_audio[:1000], file_path))
    return file_path

# ---------------------------------------------------------
# Streamlit UI
# ---------------------------------------------------------
st.sidebar.header("📌 เมนูและการตั้งค่า")
selected_source = st.sidebar.selectbox("เลือกแหล่งข่าว:", ["ทุกแหล่งข่าว"] + list(rss_sources.keys()))

if st.sidebar.button("🔄 ดึงข่าวใหม่สดๆ"):
    with st.spinner("กำลังดึงข่าวสดล่าสุดจากทุกแหล่ง..."):
        update_all_news()
        st.success("อัปเดตข่าวใหม่เรียบร้อย!")
        st.rerun()

filter_status = st.sidebar.radio("ตัวกรองสถานะ:", ["ทั้งหมด", "ยังไม่ได้อ่าน", "อ่านแล้ว"])

all_items = list(news_db.values())
if selected_source != "ทุกแหล่งข่าว":
    all_items = [i for i in all_items if i.get('source') == selected_source]

if filter_status == "ยังไม่ได้อ่าน":
    all_items = [i for i in all_items if not user_status.get(i.get('id', hashlib.md5(i.get('link', '').encode('utf-8')).hexdigest()), {}).get('read', False)]
elif filter_status == "อ่านแล้ว":
    all_items = [i for i in all_items if user_status.get(i.get('id', hashlib.md5(i.get('link', '').encode('utf-8')).hexdigest()), {}).get('read', False)]

st.subheader(f"📰 รายการข่าว ({len(all_items)} ข่าว)")

if not all_items:
    st.info("ไม่มีรายการข่าวที่จะแสดง สามารถกดปุ่ม '🔄 ดึงข่าวใหม่สดๆ' ด้านข้างได้เลยครับ")

for item in all_items:
    item_id = item.get('id') or hashlib.md5((item.get('link') or item.get('title', '')).encode('utf-8')).hexdigest()
    st_item = user_status.get(item_id, {})
    is_read = st_item.get('read', False)
    summary_text = st_item.get('summary', "")

    read_badge = "✅ [อ่านแล้ว]" if is_read else "🆕 [ยังไม่อ่าน]"
    
    with st.expander(f"{read_badge} {item.get('title', 'ไม่มีหัวข้อ')} - ({item.get('source', '')})"):
        st.write(f"**วันที่:** {item.get('pub_date', '')}")
        st.write(f"**ลิงก์ต้นทาง:** [{item.get('link', '')}]({item.get('link', '')})")
        st.write(f"**รายละเอียดสังเขป:** {item.get('description', '')}")

        col1, col2 = st.columns([1, 1])
        with col1:
            if is_read:
                if st.button("Mark as Unread", key=f"unread_{item_id}"):
                    if item_id in user_status:
                        user_status[item_id]['read'] = False
                        sync_user_status_to_github(user_status)
                        st.rerun()
            else:
                if st.button("✓ อ่านแล้ว", key=f"read_{item_id}"):
                    if item_id not in user_status:
                        user_status[item_id] = {}
                    user_status[item_id]['read'] = True
                    sync_user_status_to_github(user_status)
                    st.rerun()

        with col2:
            if st.button("✨ สรุปเนื้อหาด้วย AI (Gemini)", key=f"sum_{item_id}"):
                with st.spinner("AI กำลังวิเคราะห์ข่าวและสรุปประเด็นสำหรับสอบ กต. ..."):
                    full_text = scrape_full_article(item.get('link', ''))
                    summary_result = generate_summary(item.get('title', ''), item.get('description', ''), full_text)
                    if item_id not in user_status:
                        user_status[item_id] = {}
                    user_status[item_id]['summary'] = summary_result
                    user_status[item_id]['read'] = True
                    sync_user_status_to_github(user_status)
                    st.rerun()

        if summary_text:
            st.markdown("---")
            st.markdown("### 🤖 บทสรุปและประเด็นวิเคราะห์โดย AI")
            st.markdown(summary_text)

            if st.button("🔊 ฟังเสียงสรุปข่าว (TTS)", key=f"tts_{item_id}"):
                with st.spinner("กำลังแปลงข้อความสรุปเป็นเสียง..."):
                    audio_file = get_audio_file(summary_text, item_id)
                    st.audio(audio_file)
