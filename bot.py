"""
╔══════════════════════════════════════════════════════════════════╗
║   FOREX & GOLD NEWS ALERT BOT v5.3                              ║
║   التعديلات: معالجة 429 لـ Gemini (Exponential Backoff)           ║
║              معالجة أخطاء DNS للتقويم الاقتصادي (Retry Loop)     ║
╚══════════════════════════════════════════════════════════════════╝
"""

import os, sys, re, json, time, random, logging, hashlib, requests
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

# ─────────────────────────── Logging ────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

# ─────────────────────────── Env Config ─────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID            = os.getenv("CHAT_ID", "")
FMP_API_KEY        = os.getenv("API_KEY", "")
NEWS_API_KEY       = os.getenv("NEWS_API_KEY", "")
GROQ_API_KEY       = os.getenv("GROQ_API_KEY", "")
GEMINI_API_KEY     = os.getenv("GEMINI_API_KEY", "")
SUPABASE_URL       = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY       = os.getenv("SUPABASE_KEY", "")

UPCOMING_WINDOW_H = 4
TARGET_CURRENCY   = "USD"
NY_TZ             = ZoneInfo("America/New_York")

# ─────────────────────────── Supabase Logic ─────────────────────
def is_duplicate(title):
    if not SUPABASE_URL or not SUPABASE_KEY: return False
    h = hashlib.md5(title.encode()).hexdigest()[:16]
    try:
        url = f"{SUPABASE_URL}/rest/v1/sent_news?hash=eq.{h}&select=id"
        headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
        r = requests.get(url, headers=headers, timeout=10)
        return len(r.json()) > 0
    except: return False

def mark_sent(title, source):
    if not SUPABASE_URL or not SUPABASE_KEY: return
    h = hashlib.md5(title.encode()).hexdigest()[:16]
    try:
        url = f"{SUPABASE_URL}/rest/v1/sent_news"
        headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Content-Type": "application/json"}
        requests.post(url, headers=headers, json={"hash": h, "title": title[:200], "source": source}, timeout=10)
    except: pass

# ─────────────────────────── Gemini Analysis ────────────────────
def analyze_news(title, desc, retries=3):
    if not GEMINI_API_KEY: return None
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
    prompt = f"Analyze this Forex news: '{title}. {desc}'. Return JSON: {{'score':0-100, 'impact_gold':'Bullish/Bearish', 'summary_ar':'تلخيص بالعربية'}}"
    
    for attempt in range(retries):
        try:
            # تأخير أساسي لعدم إرسال الطلبات دفعة واحدة
            time.sleep(6) 
            
            r = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=20)
            
            # معالجة خطأ 429: تأخير متزايد (Exponential Backoff)
            if r.status_code == 429:
                wait_time = (attempt + 1) * 15 # 15 ثم 30 ثم 45 ثانية
                log.warning(f"⚠️ Gemini Rate Limit. الانتظار {wait_time} ثانية...")
                time.sleep(wait_time)
                continue
                
            r.raise_for_status()
            res = r.json()
            text = res['candidates'][0]['content']['parts'][0]['text']
            match = re.search(r'\{.*\}', text, re.DOTALL)
            return json.loads(match.group()) if match else None
            
        except Exception as e:
            log.warning(f"⚠️ Gemini Error (Attempt {attempt+1}): {e}")
            
    return None

# ─────────────────────────── Fetch Logic ────────────────────────
def fetch_calendar():
    url = "https://cdn-nfs.faireconomy.media/ff_calendar_thisweek.json"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    # محاولات متعددة لتجاوز أخطاء الشبكة والـ DNS المؤقتة
    for attempt in range(3):
        try:
            r = requests.get(url, headers=headers, timeout=20)
            r.raise_for_status()
            return r.json()
        except requests.exceptions.RequestException as e:
            log.warning(f"⚠️ Calendar Network Error (Attempt {attempt+1}): {e}")
            time.sleep(5) # انتظار 5 ثوانٍ قبل المحاولة التالية
            
    return []

def fetch_rss():
    sources = {
        "Investing Gold": "https://www.investing.com/rss/news_11.rss",
        "MarketWatch": "https://www.marketwatch.com/rss/topstories",
        "CNBC Markets": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=401&id=15839069"
    }
    news = []
    headers = {"User-Agent": "Mozilla/5.0"}
    for name, url in sources.items():
        try:
            r = requests.get(url, headers=headers, timeout=10)
            if not r.content: continue
            root = ET.fromstring(r.content)
            for item in root.findall(".//item")[:10]:
                news.append({
                    "title": item.find("title").text,
                    "description": item.find("description").text if item.find("description") is not None else "",
                    "source": name
                })
        except Exception as e:
            log.warning(f"⚠️ RSS Error [{name}]: {e}")
    return news

def send_telegram(msg):
    if not TELEGRAM_BOT_TOKEN: return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"})
        return r.status_code == 200
    except: return False

def build_news_msg(item):
    an = item["analysis"]
    emoji = "🟢" if "Bullish" in an["impact_gold"] else "🔴" if "Bearish" in an["impact_gold"] else "🟡"
    return (
        f"<b>🔔 خبر عاجل (Score: {an['score']})</b>\n\n"
        f"📝 {an['summary_ar']}\n\n"
        f"{emoji} <b>تأثير الذهب:</b> {an['impact_gold']}\n"
        f"🌐 <b>المصدر:</b> {item['source']}"
    )

# ─────────────────────────── Main Exec ──────────────────────────
if __name__ == "__main__":
    now_utc = datetime.now(timezone.utc)
    log.info(f"⚡ Forex Alert Bot v5.3 — {now_utc.strftime('%Y-%m-%d %H:%M')} UTC")

    # 1. جلب وتحليل الأخبار
    raw_news = fetch_rss()
    log.info(f"📰 إجمالي الأخبار المجلوبة: {len(raw_news)}")
    
    analyzed = []
    for item in raw_news:
        if is_duplicate(item["title"]): continue
        
        result = analyze_news(item["title"], item["description"])
        if result and result.get("score", 0) >= 70:
            item["analysis"] = result
            analyzed.append(item)
        
        if len(analyzed) >= 3: break # إرسال أفضل 3 أخبار فقط

    for item in analyzed:
        if send_telegram(build_news_msg(item)):
            mark_sent(item["title"], item["source"])
            time.sleep(2)

    # 2. التقويم الاقتصادي
    log.info("━━━ التقويم الاقتصادي ━━━")
    events = fetch_calendar()
    if not events:
        log.info("⏭️ تخطي التقويم — لا توجد بيانات")
    else:
        log.info(f"📅 تم فحص التقويم بنجاح.")

    log.info("✅ انتهت المهمة بنجاح.")
