def generate_summary(title, description, full_text):
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
    
    headers = {"Content-Type": "application/json"}
    payload = {
        "contents": [{
            "parts": [{"text": prompt}]
        }]
    }

    # 1. เช็กรายชื่อโมเดลทั้งหมดที่ API Key ตัวนี้มีสิทธิ์ใช้งานจริงก่อน
    list_url = f"https://generativelanguage.googleapis.com/v1beta/models?key={API_KEY}"
    available_models = []
    
    try:
        list_res = requests.get(list_url, timeout=5)
        if list_res.status_code == 200:
            models_data = list_res.json().get("models", [])
            for m in models_data:
                # กรองเฉพาะโมเดลที่รองรับ generateContent
                if "generateContent" in m.get("supportedGenerationMethods", []):
                    # เก็บเฉพาะชื่อโมเดล (เช่น models/gemini-2.0-flash -> gemini-2.0-flash)
                    m_name = m.get("name", "").replace("models/", "")
                    if m_name:
                        available_models.append(m_name)
    except Exception:
        pass

    # ถ้าดึงไม่สำเร็จ ให้ใส่ชื่อโมเดลสำรองไว้ทดลอง
    if not available_models:
        available_models = ["gemini-2.0-flash", "gemini-1.5-flash", "gemini-pro"]

    # 2. วนลูปยิง API ด้วยโมเดลที่ API Key นี้มีสิทธิ์จริงๆ
    last_error = ""
    for model_name in available_models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={API_KEY}"
        try:
            res = requests.post(url, headers=headers, json=payload, timeout=15)
            res_json = res.json()
            
            if res.status_code == 200:
                candidate = res_json.get("candidates", [])[0]
                return candidate.get("content", {}).get("parts", [])[0].get("text", "")
            else:
                last_error = f"[{model_name}] {res_json.get('error', {}).get('message', 'HTTP Error')}"
        except Exception as e:
            last_error = f"[{model_name}] {str(e)}"
            continue

    st.error(f"❌ API Key นี้ไม่สามารถเรียกใช้โมเดลใดๆ ได้เลย รายละเอียด: {last_error}")
    st.stop()
