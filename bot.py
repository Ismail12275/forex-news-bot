"""
╔══════════════════════════════════════════════════════════════════╗
║   FOREX & GOLD NEWS ALERT BOT v5 — bot.py                       ║
║   AI-Powered Analysis: Groq (Llama 3) + Gemini                  ║
║   Persistent Dedup: Supabase PostgreSQL                          ║
║   Anti-Block: Random User-Agent                                  ║
║   Smart Filter: High Impact Only                                 ║
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
SUPABASE_URL       = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY       = os.getenv("SUPABASE_KEY", "")

UPCOMING_WINDOW_H  = 4
TARGET_CURRENCY    = "USD"
NY_TZ              = ZoneInfo("America/New_York")


# ════════════════════════════════════════════════════════════════
#  MODULE 0: RANDOM USER-AGENT — تغيير هوية المتصفح عشوائياً
# ════════════════════════════════════════════════════════════════

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.82 Mobile Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edge/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
]

def get_headers(extra: dict = None) -> dict:
    """يُعيد headers عشوائية في كل طلب لتجنب الحجب."""
    ua = random.choice(USER_AGENTS)
    h = {
        "User-Agent":      ua,
        "Accept":          "application/json, text/xml, text/html, */*",
        "Accept-Language": random.choice(["en-US,en;q=0.9", "en-GB,en;q=0.8", "en;q=0.9"]),
        "Accept-Encoding": "gzip, deflate, br",
        "Cache-Control":   "no-cache",
        "Pragma":          "no-cache",
        "DNT":             "1",
        "Connection":      "keep-alive",
    }
    if extra:
        h.update(extra)
    return h


# ════════════════════════════════════════════════════════════════
#  MODULE 1: SUPABASE DEDUPLICATION — منع التكرار الدائم
# ════════════════════════════════════════════════════════════════

SUPABASE_TABLE = "sent_news"

def supabase_request(method: str, endpoint: str, data: dict = None) -> dict | None:
    """طلب HTTP لـ Supabase REST API."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return None
    url = f"{SUPABASE_URL}/rest/v1/{endpoint}"
    headers = {
        "apikey":        SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type":  "application/json",
        "Prefer":        "return=minimal",
    }
    try:
        if method == "GET":
            r = requests.get(url, headers=headers, timeout=10)
        elif method == "POST":
            r = requests.post(url, headers=headers, json=data, timeout=10)
        elif method == "DELETE":
            r = requests.delete(url, headers=headers, timeout=10)
        else:
            return None
        if r.status_code in (200, 201, 204):
            try: return r.json()
            except: return {}
        log.warning("⚠️ Supabase %s error %d: %s", method, r.status_code, r.text[:200])
        return None
    except Exception as e:
        log.warning("⚠️ Supabase connection error: %s", e)
        return None

def news_hash(title: str) -> str:
    clean = re.sub(r"\W+", " ", title.lower()).strip()
    return hashlib.md5(" ".join(clean.split()[:10]).encode()).hexdigest()[:16]

def is_duplicate_supabase(title: str) -> bool:
    """يتحقق من Supabase إذا كان الخبر مرسلاً من قبل."""
    if not SUPABASE_URL:
        return False
    h = news_hash(title)
    result = supabase_request(
        "GET",
        f"{SUPABASE_TABLE}?hash=eq.{h}&select=hash&limit=1"
    )
    return isinstance(result, list) and len(result) > 0

def mark_sent_supabase(title: str, source: str = "") -> None:
    """يحفظ الخبر في Supabase بعد الإرسال."""
    if not SUPABASE_URL:
        return
    now = datetime.now(timezone.utc).isoformat()
    supabase_request("POST", SUPABASE_TABLE, {
        "hash":       news_hash(title),
        "title":      title[:300],
        "source":     source,
        "sent_at":    now,
    })

def cleanup_old_hashes() -> None:
    """حذف الأخبار الأقدم من 48 ساعة من Supabase."""
    if not SUPABASE_URL:
        return
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    supabase_request(
        "DELETE",
        f"{SUPABASE_TABLE}?sent_at=lt.{cutoff}"
    )
    log.info("🧹 تنظيف Supabase: حذف الأخبار الأقدم من 48 ساعة")

# Fallback: In-memory dedup إذا لم يتوفر Supabase
_MEM_HASHES: set[str] = set()

def is_duplicate(title: str) -> bool:
    h = news_hash(title)
    if SUPABASE_URL:
        return is_duplicate_supabase(title)
    return h in _MEM_HASHES

def mark_sent(title: str, source: str = "") -> None:
    h = news_hash(title)
    _MEM_HASHES.add(h)
    if SUPABASE_URL:
        mark_sent_supabase(title, source)


# ════════════════════════════════════════════════════════════════
#  MODULE 2: AI ANALYSIS — تحليل ذكي بـ Groq + Gemini
# ════════════════════════════════════════════════════════════════

AI_SYSTEM_PROMPT = """You are a senior financial analyst specializing in Gold (XAUUSD) and USD forex markets.
Analyze the news headline and return ONLY valid JSON with this exact structure:
{
  "relevant": true/false,
  "score": 0-100,
  "impact_level": "HIGH" | "MEDIUM" | "LOW" | "IGNORE",
  "gold_impact": "BULLISH" | "BEARISH" | "MIXED" | "NEUTRAL",
  "usd_impact": "BULLISH" | "BEARISH" | "MIXED" | "NEUTRAL",
  "reason_en": "Brief reason in English (max 15 words)",
  "reason_ar": "سبب موجز بالعربية (أقل من 15 كلمة)",
  "watch": "XAUUSD" | "DXY" | "BOTH" | "NONE"
}
Rules:
- score >= 80 = HIGH (wars, Fed decisions, major economic data)
- score 60-79 = MEDIUM (sanctions, trade disputes, oil shocks)
- score 40-59 = LOW (minor economic data, analyst opinions)
- score < 40 = IGNORE (irrelevant news)
- Return ONLY the JSON object, no markdown, no explanation."""

def analyze_with_groq(title: str, description: str = "") -> dict | None:
    """
    تحليل الخبر بـ Groq (Llama 3) — أسرع نموذج مجاني.
    مجاني تماماً على groq.com.
    """
    if not GROQ_API_KEY:
        return None
    prompt = f"News headline: {title}\nDescription: {description[:200] if description else 'N/A'}"
    try:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type":  "application/json",
            },
            json={
                "model":       "llama3-8b-8192",   # مجاني وسريع جداً
                "messages":    [
                    {"role": "system", "content": AI_SYSTEM_PROMPT},
                    {"role": "user",   "content": prompt},
                ],
                "temperature": 0.1,
                "max_tokens":  200,
            },
            timeout=15,
        )
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"].strip()
        # تنظيف وتحليل الـ JSON
        content = re.sub(r"```json|```", "", content).strip()
        result  = json.loads(content)
        log.info("🤖 Groq: score=%d, gold=%s, usd=%s",
                 result.get("score", 0), result.get("gold_impact"), result.get("usd_impact"))
        return result
    except Exception as e:
        log.warning("⚠️ Groq error: %s", e)
        return None

def analyze_with_gemini(title: str, description: str = "") -> dict | None:
    """
    تحليل الخبر بـ Google Gemini — حصة مجانية كبيرة.
    مجاني على aistudio.google.com.
    """
    if not GEMINI_API_KEY:
        return None
    prompt = (
        f"{AI_SYSTEM_PROMPT}\n\n"
        f"News: {title}\n"
        f"Details: {description[:200] if description else 'N/A'}"
    )
    try:
        r = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}",
            headers={"Content-Type": "application/json"},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.1, "maxOutputTokens": 200},
            },
            timeout=20,
        )
        r.raise_for_status()
        content = r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
        content = re.sub(r"```json|```", "", content).strip()
        result  = json.loads(content)
        log.info("✨ Gemini: score=%d, gold=%s, usd=%s",
                 result.get("score", 0), result.get("gold_impact"), result.get("usd_impact"))
        return result
    except Exception as e:
        log.warning("⚠️ Gemini error: %s", e)
        return None

# Fallback: Keyword scoring إذا فشل الـ AI
NEWS_RULES = [
    (["war","military strike","invasion","nuclear threat","missile"],       "BULLISH","MIXED",90),
    (["ceasefire","peace deal","truce","de-escalat"],                       "BEARISH","MIXED",75),
    (["federal reserve","fomc","rate hike","monetary tighten","powell hawk"],"BEARISH","BULLISH",90),
    (["rate cut","dovish fed","fed pivot","lower rate"],                    "BULLISH","BEARISH",90),
    (["sanction","embargo","trade war","tariff","export ban"],              "BULLISH","MIXED",85),
    (["inflation surges","cpi higher","hot inflation","ppi higher"],        "BULLISH","MIXED",80),
    (["inflation cools","deflation","cpi lower","disinflation"],            "BEARISH","BULLISH",75),
    (["recession","gdp contracts","stagflation","economic crisis"],         "BULLISH","BEARISH",85),
    (["debt ceiling","us default","shutdown","credit downgrade"],           "BULLISH","BEARISH",90),
    (["bank collapse","banking crisis","financial crisis","bank run"],      "BULLISH","BEARISH",88),
    (["safe haven","flight to safety","gold demand","buy gold"],            "BULLISH","BEARISH",80),
    (["strong gdp","economic expansion","risk on","stock rally"],           "BEARISH","BULLISH",65),
    (["oil surges","crude rally","opec cut","energy crisis"],               "BULLISH","MIXED",70),
    (["trade deal","trade agreement","tariff removed"],                     "BEARISH","BULLISH",70),
    (["job losses","layoffs","unemployment rises","weak jobs"],             "BULLISH","BEARISH",72),
    (["strong jobs","low unemployment","nonfarm payroll beat"],             "BEARISH","BULLISH",72),
    (["china crisis","china slowdown","taiwan","sino-us tension"],          "BULLISH","MIXED",78),
    (["breaking","urgent","flash","emergency alert"],                       "MIXED","MIXED",65),
]

def analyze_with_keywords(title: str, desc: str = "") -> dict | None:
    text = (title + " " + desc).lower()
    best, gold_i, usd_i = 0, "NEUTRAL", "NEUTRAL"
    for kws, g, u, base in NEWS_RULES:
        hits = sum(1 for k in kws if k in text)
        if hits:
            s = base + (hits - 1) * 5
            if s > best:
                best, gold_i, usd_i = s, g, u
    if best < 55:
        return None
    level = "HIGH" if best >= 80 else ("MEDIUM" if best >= 65 else "LOW")
    return {
        "relevant":     True,
        "score":        best,
        "impact_level": level,
        "gold_impact":  gold_i,
        "usd_impact":   usd_i,
        "reason_en":    "Keyword-based analysis",
        "reason_ar":    "تحليل بناءً على الكلمات المفتاحية",
        "watch":        "BOTH",
    }

def analyze_news(title: str, description: str = "") -> dict | None:
    """
    يجرب: Groq أولاً → Gemini → Keywords fallback
    يُعيد None إذا كان الخبر غير مهم.
    """
    result = analyze_with_groq(title, description)
    if result is None:
        result = analyze_with_gemini(title, description)
    if result is None:
        result = analyze_with_keywords(title, description)
    if result is None:
        return None
    # فلترة صارمة — High و Medium فقط
    if result.get("impact_level") in ("LOW", "IGNORE"):
        return None
    if result.get("score", 0) < 60:
        return None
    return result


# ════════════════════════════════════════════════════════════════
#  MODULE 3: FETCH NEWS — جلب الأخبار
# ════════════════════════════════════════════════════════════════

RSS_SOURCES = [
    ("https://feeds.reuters.com/reuters/businessNews",             "Reuters"),
    ("https://www.marketwatch.com/rss/topstories",                 "MarketWatch"),
    ("https://www.investing.com/rss/news_25.rss",                  "Investing Gold"),
    ("https://www.investing.com/rss/news_1.rss",                   "Investing Forex"),
    ("https://www.forexlive.com/feed/news",                        "ForexLive"),
    ("https://www.fxstreet.com/rss",                               "FXStreet"),
    ("https://www.dailyfx.com/feeds/all",                          "DailyFX"),
    ("https://feeds.bbci.co.uk/news/business/rss.xml",            "BBC Business"),
    ("https://www.cnbc.com/id/10000664/device/rss/rss.html",      "CNBC Economy"),
    ("https://feeds.bloomberg.com/markets/news.rss",               "Bloomberg Markets"),
]

RELEVANCE_KEYWORDS = [
    "gold","xauusd","usd","dollar","fed","federal reserve","fomc",
    "inflation","interest rate","gdp","recession","economy","economic",
    "treasury","bond","yield","war","conflict","sanction","tariff",
    "trade","oil","crude","opec","china","safe haven","powell",
    "jobs","payroll","unemployment","cpi","ppi","debt","crisis",
    "bank","forex","currency","iran","russia","ukraine","nato",
]

def fetch_rss(url: str, name: str) -> list[dict]:
    try:
        time.sleep(random.uniform(0.3, 1.0))  # تأخير عشوائي لتجنب Rate Limiting
        r = requests.get(url, headers=get_headers({"Referer": url}), timeout=20)
        r.raise_for_status()
        root  = ET.fromstring(r.content)
        items = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
        news  = []
        for item in items[:15]:
            def g(tag):
                el = item.find(tag)
                return el.text.strip() if el is not None and el.text else ""
            title = g("title") or g("{http://www.w3.org/2005/Atom}title")
            desc  = g("description") or g("{http://www.w3.org/2005/Atom}summary")
            link  = g("link") or g("{http://www.w3.org/2005/Atom}link")
            pub   = g("pubDate") or g("published") or g("{http://www.w3.org/2005/Atom}published")
            if not title:
                continue
            try:
                from email.utils import parsedate_to_datetime
                dt = parsedate_to_datetime(pub).astimezone(timezone.utc)
            except:
                dt = datetime.now(timezone.utc)
            news.append({
                "title":       title,
                "description": re.sub(r"<[^>]+>", "", desc)[:400],
                "link":        link,
                "source":      name,
                "time_utc":    dt,
            })
        log.info("📰 %s: %d خبر", name, len(news))
        return news
    except Exception as e:
        log.warning("⚠️ RSS [%s]: %s", name, e)
        return []

def fetch_newsapi() -> list[dict]:
    if not NEWS_API_KEY:
        return []
    try:
        r = requests.get(
            "https://newsapi.org/v2/everything",
            params={
                "q":        "gold OR \"federal reserve\" OR \"USD\" OR inflation OR sanctions",
                "language": "en",
                "sortBy":   "publishedAt",
                "pageSize": 20,
                "apiKey":   NEWS_API_KEY,
            },
            headers=get_headers(),
            timeout=15,
        )
        r.raise_for_status()
        news = []
        for a in r.json().get("articles", []):
            try:
                dt = datetime.fromisoformat(a["publishedAt"].replace("Z","+00:00")).astimezone(timezone.utc)
            except:
                dt = datetime.now(timezone.utc)
            news.append({
                "title":       a.get("title",""),
                "description": (a.get("description") or "")[:400],
                "link":        a.get("url",""),
                "source":      a.get("source",{}).get("name","NewsAPI"),
                "time_utc":    dt,
            })
        log.info("📰 NewsAPI: %d خبر", len(news))
        return news
    except Exception as e:
        log.warning("⚠️ NewsAPI: %s", e)
        return []

def fetch_all_news() -> list[dict]:
    """جلب من جميع المصادر مع فلترة أولية."""
    all_news = []
    for url, name in RSS_SOURCES:
        all_news.extend(fetch_rss(url, name))
    all_news.extend(fetch_newsapi())

    now_utc = datetime.now(timezone.utc)
    cutoff  = now_utc - timedelta(hours=4)

    # فلترة: آخر 4 ساعات + صلة مالية
    filtered = [
        n for n in all_news
        if n["time_utc"] >= cutoff
        and any(kw in (n["title"]+" "+n["description"]).lower() for kw in RELEVANCE_KEYWORDS)
    ]
    log.info("📦 الأخبار بعد الفلترة الأولية: %d / %d", len(filtered), len(all_news))
    return filtered


# ════════════════════════════════════════════════════════════════
#  MODULE 4: ECONOMIC CALENDAR — التقويم الاقتصادي
# ════════════════════════════════════════════════════════════════

TRANSLATIONS = {
    "Non-Farm Employment Change":"التغير في الوظائف غير الزراعية",
    "Non-Farm Payrolls":"الرواتب غير الزراعية",
    "Unemployment Rate":"معدل البطالة",
    "CPI m/m":"مؤشر أسعار المستهلك (شهري)",
    "Core CPI m/m":"مؤشر أسعار المستهلك الأساسي",
    "GDP q/q":"الناتج المحلي الإجمالي",
    "Federal Funds Rate":"سعر الفائدة الفيدرالي",
    "FOMC Statement":"بيان لجنة الاحتياطي الفيدرالي",
    "FOMC Press Conference":"مؤتمر الاحتياطي الفيدرالي",
    "ISM Manufacturing PMI":"مؤشر PMI التصنيعي",
    "ISM Services PMI":"مؤشر PMI للخدمات",
    "Retail Sales m/m":"مبيعات التجزئة (شهري)",
    "PPI m/m":"مؤشر أسعار المنتجين",
    "Trade Balance":"الميزان التجاري",
    "Consumer Confidence":"ثقة المستهلك",
    "Initial Jobless Claims":"طلبات إعانة البطالة",
    "ADP Non-Farm Employment Change":"تقرير ADP للوظائف",
    "JOLTs Job Openings":"فرص العمل JOLTs",
    "PCE Price Index m/m":"مؤشر أسعار PCE",
    "Durable Goods Orders m/m":"طلبيات السلع المعمرة",
}

def translate(name: str) -> str:
    for en, ar in TRANSLATIONS.items():
        if en.lower() in name.lower():
            return ar
    return name

def safe_float(v) -> float | None:
    try:
        if v in (None,"","N/A"): return None
        return float(re.sub(r"[%KMBkb,\s]","",str(v)).strip())
    except: return None

def fmtv(v, u="") -> str:
    if v is None: return "—"
    s = f"{v:,.1f}" if abs(v)>=1000 else (f"{v:.2f}" if abs(v)>=10 else f"{v:.3f}")
    return s+u

def fmtt(dt): return dt.astimezone(NY_TZ).strftime("%I:%M %p ET")

def fetch_calendar() -> list[dict]:
    events = []
    for url in [
        "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
        "https://nfs.faireconomy.media/ff_calendar_nextweek.json",
    ]:
        try:
            time.sleep(random.uniform(0.2, 0.8))
            r = requests.get(url, headers=get_headers({"Referer":"https://www.forexfactory.com/"}), timeout=20)
            r.raise_for_status()
            for item in r.json():
                if (item.get("country") or "").upper() != TARGET_CURRENCY: continue
                if (item.get("impact") or "").lower() != "high": continue
                try:
                    dt = datetime.fromisoformat(item["date"]).astimezone(timezone.utc)
                except: continue
                events.append({
                    "name":     item.get("title","Unknown"),
                    "time_utc": dt,
                    "actual":   safe_float(item.get("actual")),
                    "forecast": safe_float(item.get("forecast")),
                    "previous": safe_float(item.get("previous")),
                    "unit":     "",
                })
        except Exception as e:
            log.warning("⚠️ Calendar error [%s]: %s", url.split("/")[-1], e)

    if FMP_API_KEY:
        try:
            now = datetime.now(timezone.utc)
            r = requests.get(
                "https://financialmodelingprep.com/api/v3/economic_calendar",
                params={"from":now.strftime("%Y-%m-%d"),"to":(now+timedelta(days=1)).strftime("%Y-%m-%d"),"apikey":FMP_API_KEY},
                headers=get_headers(), timeout=15,
            )
            for item in (r.json() if isinstance(r.json(),list) else []):
                if (item.get("currency") or "").upper() != TARGET_CURRENCY: continue
                if (item.get("impact") or "").lower() != "high": continue
                try:
                    raw = (item.get("date") or "").replace(" ","T")
                    if "+" not in raw and not raw.endswith("Z"): raw += "+00:00"
                    dt = datetime.fromisoformat(raw).astimezone(timezone.utc)
                except: continue
                events.append({
                    "name":     item.get("event","Unknown"),
                    "time_utc": dt,
                    "actual":   safe_float(item.get("actual")),
                    "forecast": safe_float(item.get("estimate")),
                    "previous": safe_float(item.get("previous")),
                    "unit":     item.get("unit",""),
                })
        except Exception as e:
            log.warning("⚠️ FMP Calendar: %s", e)

    # إزالة تكرار التقويم
    seen, merged = {}, []
    for ev in events:
        key = ev["name"][:15].lower() + ev["time_utc"].strftime("%Y%m%d%H")
        if key not in seen:
            seen[key] = True
            merged.append(ev)

    log.info("📅 التقويم: %d أحداث USD عالية التأثير", len(merged))
    return merged

def has_new_actual(ev: dict, sent: set) -> bool:
    """يتحقق إذا كان الحدث يحتوي على قيمة Actual جديدة لم تُرسل."""
    if ev.get("actual") is None:
        return False
    key = f"cal_{news_hash(ev['name'])}_{ev['time_utc'].strftime('%Y%m%d')}"
    if key in sent:
        return False
    sent.add(key)
    return True


# ════════════════════════════════════════════════════════════════
#  MODULE 5: MESSAGE BUILDERS — بناء الرسائل
# ════════════════════════════════════════════════════════════════

IMPACT_EMOJI = {
    "BULLISH": "🟢 BULLISH صاعد",
    "BEARISH": "🔴 BEARISH هابط",
    "MIXED":   "🟡 MIXED متذبذب",
    "NEUTRAL": "⚪ NEUTRAL محايد",
}

def build_news_msg(item: dict) -> str:
    a    = item["analysis"]
    now  = datetime.now(NY_TZ).strftime("%I:%M %p ET")
    icon = "🚨" if a["score"] >= 80 else ("⚡" if a["score"] >= 65 else "📢")
    lvl  = "🔴 HIGH IMPACT" if a["impact_level"]=="HIGH" else "🟡 MEDIUM IMPACT"

    lines = [
        f"{icon} *BREAKING NEWS | خبر عاجل*",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"📡 *{item['source']}*   🕐 {now}",
        "",
        f"📰 *{item['title']}*",
    ]
    if item.get("description") and len(item["description"]) > 20:
        lines.append(f"_{item['description'][:150]}..._")
    lines += [
        "",
        f"*{lvl}*  |  Score: `{a['score']}/100`",
        "",
        "*📊 Expected Impact | التأثير المتوقع:*",
        f"🥇 Gold  | الذهب:   {IMPACT_EMOJI.get(a['gold_impact'], a['gold_impact'])}",
        f"💵 USD   | الدولار: {IMPACT_EMOJI.get(a['usd_impact'],  a['usd_impact'])}",
        "",
        f"💡 *{a.get('reason_en','')}*",
        f"💡 *{a.get('reason_ar','')}*",
        "",
        f"⚠️ *Watch {a.get('watch','XAUUSD')} volatility | راقب التذبذب*",
    ]
    if item.get("link"):
        lines.append(f"\n🔗 [Read more]({item['link']})")
    lines += [
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "🤖 _Forex Alert Bot v5 | AI-Powered_",
    ]
    return "\n".join(lines)

def build_calendar_msg(released: list[dict], upcoming: list[dict]) -> str:
    now_ny  = datetime.now(NY_TZ)
    days_ar = ["الإثنين","الثلاثاء","الأربعاء","الخميس","الجمعة","السبت","الأحد"]
    lines   = [
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "📊 *ECONOMIC CALENDAR | التقويم الاقتصادي*",
        f"🗓 {now_ny.strftime('%A %d %b')} | {days_ar[now_ny.weekday()]}",
        f"🕐 {now_ny.strftime('%I:%M %p ET')}",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━","",
        "✅ *RELEASED | صادر*","─────────────────────────",
    ]
    for ev in released:
        a, f, p = ev.get("actual"), ev.get("forecast"), ev.get("previous")
        u = ev.get("unit","")
        s = "BULLISH" if (a and f and a>f) else ("BEARISH" if (a and f and a<f) else "NEUTRAL")
        lines += [
            f"📌 *{ev['name']}*",
            f"   _{translate(ev['name'])}_",
            f"   🕐 {fmtt(ev['time_utc'])}",
            f"   ┌ Actual:   `{fmtv(a,u)}`",
            f"   ├ Forecast: `{fmtv(f,u)}`",
            f"   └ Previous: `{fmtv(p,u)}`",
            (
                "   💡 *USD BULLISH 🟢* → الذهب قد ينخفض 🔴" if s=="BULLISH" else
                "   💡 *USD BEARISH 🔴* → الذهب قد يرتفع 🟢" if s=="BEARISH" else
                "   💡 *NEUTRAL ⚪* → لا توجه واضح"
            ),"",
        ]
    if not released:
        lines += ["   _لا أحداث صادرة بعد_",""]

    lines += [f"⏳ *UPCOMING | قادم* _(next {UPCOMING_WINDOW_H}h)_","─────────────────────────"]
    now_utc = datetime.now(timezone.utc)
    for ev in upcoming:
        dm  = int((ev["time_utc"]-now_utc).total_seconds()/60)
        eta = f"{dm}m" if dm<60 else f"{dm//60}h {dm%60}m"
        u   = ev.get("unit","")
        lines += [
            f"⚡ *{ev['name']}*",
            f"   _{translate(ev['name'])}_",
            f"   🕐 {fmtt(ev['time_utc'])} _(in {eta})_",
            f"   Forecast: `{fmtv(ev.get('forecast'),u)}` | Prev: `{fmtv(ev.get('previous'),u)}`","",
        ]
    if not upcoming:
        lines += [f"   _لا أحداث قادمة خلال {UPCOMING_WINDOW_H} ساعات_",""]

    lines += ["━━━━━━━━━━━━━━━━━━━━━━━━━━━","🤖 _Forex Alert Bot v5_"]
    return "\n".join(lines)


# ════════════════════════════════════════════════════════════════
#  MODULE 6: TELEGRAM SENDER
# ════════════════════════════════════════════════════════════════

def send_telegram(msg: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not CHAT_ID:
        log.error("❌ TELEGRAM_BOT_TOKEN أو CHAT_ID مفقود!")
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={
                "chat_id":                  CHAT_ID,
                "text":                     msg,
                "parse_mode":               "Markdown",
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
        data = r.json()
        if not data.get("ok"):
            log.error("❌ Telegram: %s", data)
            return False
        log.info("✅ تم الإرسال! id=%s", data["result"]["message_id"])
        return True
    except Exception as e:
        log.error("❌ Telegram failed: %s", e)
        return False


# ════════════════════════════════════════════════════════════════
#  MAIN — الدالة الرئيسية
# ════════════════════════════════════════════════════════════════

def run() -> None:
    now_utc = datetime.now(timezone.utc)
    log.info("⚡ Forex Alert Bot v5 — %s UTC", now_utc.strftime("%Y-%m-%d %H:%M"))
    log.info("🤖 AI: %s | 💾 Dedup: %s",
             "Groq" if GROQ_API_KEY else ("Gemini" if GEMINI_API_KEY else "Keywords"),
             "Supabase" if SUPABASE_URL else "In-Memory")

    # ── Validate secrets ──────────────────────────────────────
    if not TELEGRAM_BOT_TOKEN or not CHAT_ID:
        log.error("❌ TELEGRAM_BOT_TOKEN أو CHAT_ID مفقود!")
        sys.exit(1)

    # ── Supabase cleanup ──────────────────────────────────────
    cleanup_old_hashes()
    cal_sent: set[str] = set()
    total_sent = 0

    # ════ PART 1: Breaking News ═══════════════════════════════
    log.info("━━━ جلب الأخبار العاجلة ━━━")
    raw_news = fetch_all_news()

    # تحليل بالـ AI + فلترة التكرار — أهم 3 أخبار فقط
    analyzed = []
    for item in raw_news:
        if is_duplicate(item["title"]):
            log.debug("🔁 تكرار: %s", item["title"][:60])
            continue
        analysis = analyze_news(item["title"], item["description"])
        if analysis:
            item["analysis"] = analysis
            analyzed.append(item)
        if len(analyzed) >= 3:  # حد أقصى 3 أخبار/run لمنع الـ Spam
            break

    log.info("📊 أخبار مؤهلة للإرسال: %d", len(analyzed))

    for item in sorted(analyzed, key=lambda x: x["analysis"]["score"], reverse=True):
        msg = build_news_msg(item)
        if send_telegram(msg):
            mark_sent(item["title"], item["source"])
            total_sent += 1
            time.sleep(2)

    # ════ PART 2: Economic Calendar ════════════════════════════
    log.info("━━━ التقويم الاقتصادي ━━━")
    events  = fetch_calendar()
    cutoff  = now_utc + timedelta(hours=UPCOMING_WINDOW_H)

    # ⚡ إرسال التقويم فقط إذا:
    # 1. يوجد حدث جديد صدر (Actual جديد)
    # 2. أو يوجد حدث قادم خلال 30 دقيقة
    released = sorted(
        [e for e in events if e["time_utc"] <= now_utc and has_new_actual(e, cal_sent)],
        key=lambda e: e["time_utc"],
    )
    upcoming_30m = [
        e for e in events
        if now_utc < e["time_utc"] <= now_utc + timedelta(minutes=35)
    ]
    upcoming_4h = sorted(
        [e for e in events if now_utc < e["time_utc"] <= cutoff],
        key=lambda e: e["time_utc"],
    )

    should_send_calendar = bool(released or upcoming_30m)

    if should_send_calendar:
        log.info("📅 إرسال التقويم — صادر: %d | قادم 30m: %d",
                 len(released), len(upcoming_30m))
        cal_msg = build_calendar_msg(released, upcoming_4h)
        if send_telegram(cal_msg):
            total_sent += 1
    else:
        log.info("⏭️ تخطي التقويم — لا جديد في هذه الجولة")

    log.info("✅ انتهى — إجمالي الرسائل المرسلة: %d", total_sent)


if __name__ == "__main__":
    run()
