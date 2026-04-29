"""
╔══════════════════════════════════════════════════════════════════╗
║   FOREX & GOLD NEWS ALERT BOT v5.1 — bot.py  (FIXED)           ║
║   Fixes: Supabase URL, Gemini model, FF Calendar, RSS sources   ║
╚══════════════════════════════════════════════════════════════════╝
"""

import os, sys, re, json, time, random, logging, hashlib, requests
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

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

# ════════════════════════════════════════════════════════════════
#  RANDOM USER-AGENT
# ════════════════════════════════════════════════════════════════
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edge/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1",
]

def get_headers(extra: dict = None) -> dict:
    h = {
        "User-Agent":      random.choice(USER_AGENTS),
        "Accept":          "application/json, text/xml, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Cache-Control":   "no-cache",
        "Connection":      "keep-alive",
    }
    if extra:
        h.update(extra)
    return h


# ════════════════════════════════════════════════════════════════
#  FIX 1: SUPABASE — إصلاح URL الصحيح
# ════════════════════════════════════════════════════════════════
# الخطأ كان: SUPABASE_URL/rest/v1/sent_news  (مكرر /rest/v1 أحياناً)
# الصح: يجب أن يكون SUPABASE_URL = https://xxxx.supabase.co فقط

SUPABASE_TABLE = "sent_news"

def _supabase_headers() -> dict:
    return {
        "apikey":        SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type":  "application/json",
        "Prefer":        "return=minimal",
    }

def _sb_url(path: str) -> str:
    """بناء URL صحيح لـ Supabase — يتجنب التكرار."""
    base = SUPABASE_URL
    # إزالة /rest/v1 إذا كانت موجودة في الـ env
    base = re.sub(r"/rest/v1/?$", "", base)
    return f"{base}/rest/v1/{path}"

def sb_get(path: str) -> list | None:
    if not SUPABASE_URL or not SUPABASE_KEY:
        return None
    try:
        r = requests.get(_sb_url(path), headers=_supabase_headers(), timeout=10)
        if r.status_code == 200:
            return r.json()
        log.warning("⚠️ Supabase GET %d: %s", r.status_code, r.text[:150])
        return None
    except Exception as e:
        log.warning("⚠️ Supabase GET error: %s", e)
        return None

def sb_post(path: str, data: dict) -> bool:
    if not SUPABASE_URL or not SUPABASE_KEY:
        return False
    try:
        r = requests.post(_sb_url(path), headers=_supabase_headers(), json=data, timeout=10)
        return r.status_code in (200, 201, 204)
    except Exception as e:
        log.warning("⚠️ Supabase POST error: %s", e)
        return False

def sb_delete(path: str) -> bool:
    if not SUPABASE_URL or not SUPABASE_KEY:
        return False
    try:
        r = requests.delete(_sb_url(path), headers=_supabase_headers(), timeout=10)
        return r.status_code in (200, 204)
    except Exception as e:
        log.warning("⚠️ Supabase DELETE error: %s", e)
        return False

def news_hash(title: str) -> str:
    clean = re.sub(r"\W+", " ", title.lower()).strip()
    return hashlib.md5(" ".join(clean.split()[:10]).encode()).hexdigest()[:16]

_MEM_HASHES: set[str] = set()

def is_duplicate(title: str) -> bool:
    h = news_hash(title)
    if SUPABASE_URL and SUPABASE_KEY:
        result = sb_get(f"{SUPABASE_TABLE}?hash=eq.{h}&select=hash&limit=1")
        if result is not None:
            return len(result) > 0
    return h in _MEM_HASHES

def mark_sent(title: str, source: str = "") -> None:
    h = news_hash(title)
    _MEM_HASHES.add(h)
    if SUPABASE_URL and SUPABASE_KEY:
        sb_post(SUPABASE_TABLE, {
            "hash":    h,
            "title":   title[:300],
            "source":  source,
            "sent_at": datetime.now(timezone.utc).isoformat(),
        })

def cleanup_old_hashes() -> None:
    if not SUPABASE_URL or not SUPABASE_KEY:
        return
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    ok = sb_delete(f"{SUPABASE_TABLE}?sent_at=lt.{cutoff}")
    log.info("🧹 Supabase cleanup: %s", "✅ OK" if ok else "⚠️ skipped")


# ════════════════════════════════════════════════════════════════
#  FIX 2: GEMINI — تحديث اسم النموذج
# ════════════════════════════════════════════════════════════════
# gemini-1.5-flash → gemini-2.0-flash أو gemini-1.5-flash-latest

AI_SYSTEM_PROMPT = """You are a senior financial analyst specializing in Gold (XAUUSD) and USD (DXY) markets.

CRITICAL RULES:
1. Distinguish news TYPE before scoring:
   - ECONOMIC_DATA: NFP, CPI, GDP, Housing, PMI, Retail Sales → affects USD directly
   - MONETARY_POLICY: Fed, FOMC, rate decisions, Powell speech → HIGH impact always
   - GEOPOLITICAL: wars, sanctions, conflicts → affects Gold via safe-haven, USD varies
   - OPINION/ANALYSIS: journalist opinion, forecast, "could", "may" → score MAX 45, relevant=false usually
   - RUMOR/UNVERIFIED: "report says", "sources say", unnamed sources → score MAX 40

2. Economic data logic (DO NOT default Gold=Bullish for everything):
   - Strong data (beats forecast): USD BULLISH, Gold BEARISH/NEUTRAL
   - Weak data (misses forecast): USD BEARISH, Gold BULLISH
   - Housing/Construction beats: USD BULLISH, Gold BEARISH (NOT bullish)
   - Jobs strong: USD BULLISH, Gold BEARISH
   - Inflation hot: Gold BULLISH but also USD BULLISH (MIXED)

3. Geopolitical logic:
   - Active military conflict confirmed: Gold BULLISH, USD MIXED (safe haven both)
   - Ceasefire/peace: Gold BEARISH, USD MIXED
   - Unconfirmed conflict rumor: score MAX 55, mark as unverified

4. Score calibration (be precise, NOT everything is 92):
   - 90-100: FOMC decision, NFP, confirmed war/crisis ONLY
   - 75-89: CPI, GDP, confirmed geopolitical event
   - 60-74: ISM, Retail Sales, sanctions, oil crisis
   - 40-59: minor data, unconfirmed reports
   - <40: opinion, analysis articles, old news → relevant=false

Return ONLY valid JSON, no markdown:
{
  "relevant": true/false,
  "news_type": "ECONOMIC_DATA"|"MONETARY_POLICY"|"GEOPOLITICAL"|"OPINION"|"RUMOR"|"OTHER",
  "score": 0-100,
  "impact_level": "HIGH"|"MEDIUM"|"LOW"|"IGNORE",
  "gold_impact": "BULLISH"|"BEARISH"|"MIXED"|"NEUTRAL",
  "usd_impact": "BULLISH"|"BEARISH"|"MIXED"|"NEUTRAL",
  "reason_en": "max 12 words explaining the specific market logic",
  "reason_ar": "أقل من 12 كلمة مع المنطق الحقيقي",
  "scenarios": "IF X → Gold Y, IF Z → Gold W (max 20 words)",
  "watch": "XAUUSD"|"DXY"|"BOTH"|"NONE",
  "confidence": "HIGH"|"MEDIUM"|"LOW"
}"""

# نماذج Gemini مرتبة حسب الأولوية
GEMINI_MODELS = [
    "gemini-2.0-flash",
    "gemini-1.5-flash-latest",
    "gemini-1.5-flash",
    "gemini-pro",
]

def analyze_with_gemini(title: str, desc: str = "") -> dict | None:
    if not GEMINI_API_KEY:
        return None
    prompt = f"{AI_SYSTEM_PROMPT}\n\nNews: {title}\nDetails: {desc[:200] or 'N/A'}"

    for model in GEMINI_MODELS:
        try:
            r = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}",
                headers={"Content-Type": "application/json"},
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"temperature": 0.1, "maxOutputTokens": 250},
                },
                timeout=20,
            )
            if r.status_code == 404:
                log.debug("Gemini model %s not found, trying next...", model)
                continue
            r.raise_for_status()
            content = r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
            content = re.sub(r"```json|```|\n", "", content).strip()
            result  = json.loads(content)
            log.info("✨ Gemini [%s]: score=%d gold=%s usd=%s",
                     model, result.get("score",0), result.get("gold_impact"), result.get("usd_impact"))
            return result
        except json.JSONDecodeError:
            log.warning("⚠️ Gemini JSON parse error for model %s", model)
            continue
        except Exception as e:
            log.warning("⚠️ Gemini [%s]: %s", model, e)
            continue
    return None

def analyze_with_groq(title: str, desc: str = "") -> dict | None:
    if not GROQ_API_KEY:
        return None
    prompt = f"News title: {title}\nDetails: {desc[:300] or 'N/A'}\n\nAnalyze this news. Be precise about news_type. Do NOT default gold=bullish for all news."
    try:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
            json={
                "model":       "llama-3.3-70b-versatile",  # أقوى من llama3-8b
                "messages":    [
                    {"role": "system", "content": AI_SYSTEM_PROMPT},
                    {"role": "user",   "content": prompt},
                ],
                "temperature": 0.05,  # أقل = أكثر دقة
                "max_tokens":  350,
            },
            timeout=15,
        )
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"].strip()
        content = re.sub(r"```json|```|\n", "", content).strip()
        result  = json.loads(content)
        log.info("🤖 Groq: type=%s score=%d gold=%s usd=%s conf=%s",
                 result.get("news_type","?"), result.get("score",0),
                 result.get("gold_impact"), result.get("usd_impact"),
                 result.get("confidence","?"))
        return result
    except Exception as e:
        log.warning("⚠️ Groq: %s", e)
        return None

NEWS_RULES = [
    # ── MONETARY POLICY (highest impact, precise) ──────────────────────────
    (["fomc","federal reserve rate decision","rate hike","rate cut","fed funds rate",
      "raises rates","raises interest","hikes rates","rate decision","fed raises",
      "bps hike","basis points"],
     "MONETARY_POLICY", "MIXED","BULLISH",92),  # rate hike = USD up, Gold mixed
    (["powell hawk","tighten monetary","quantitative tighten","hawkish powell",
      "hawkish fed","more hikes","restrictive policy"],
     "MONETARY_POLICY", "BEARISH","BULLISH",88),
    (["powell dove","rate cut","fed pivot","dovish fed","lower rates","rate reduction",
      "cuts rates","easing policy","dovish powell","pauses hikes"],
     "MONETARY_POLICY", "BULLISH","BEARISH",90),

    # ── GEOPOLITICAL (confirmed = high, unconfirmed = medium) ──────────────
    (["military strike confirmed","invasion begins","war declared","nuclear launch","missile attack confirmed"],
     "GEOPOLITICAL", "BULLISH","MIXED",88),
    (["war","armed conflict","troops deployed","military operation"],
     "GEOPOLITICAL", "BULLISH","MIXED",72),  # lower — needs confirmation
    (["ceasefire","peace deal","truce","de-escalat","peace agreement"],
     "GEOPOLITICAL", "BEARISH","MIXED",70),
    (["sanction","embargo","export ban"],
     "GEOPOLITICAL", "BULLISH","MIXED",78),

    # ── ECONOMIC DATA (direction matters — not all gold bullish!) ──────────
    # Inflation: hot = gold UP + usd mixed | cool = gold DOWN
    (["inflation surges","cpi higher than","hot cpi","ppi higher","core cpi beat",
      "inflation accelerates","cpi beats","inflation above","prices rise","cpi above forecast",
      "inflation jumps","inflation unexpectedly"],
     "ECONOMIC_DATA", "BULLISH","MIXED",82),
    (["inflation cools","cpi lower","deflation","disinflation","below forecast cpi",
      "cpi falls","inflation slows","inflation eases","cpi misses","prices fall",
      "cpi drops","inflation drops","cpi below"],
     "ECONOMIC_DATA", "BEARISH","BULLISH",78),

    # Jobs: strong = USD UP, Gold DOWN
    (["payrolls beat","jobs beat","strong nfp","low unemployment","hiring surges",
      "adds jobs","added jobs","job gains","nfp beats","employment rises",
      "unemployment falls","unemployment rate falls","jobless rate falls"],
     "ECONOMIC_DATA", "BEARISH","BULLISH",85),
    (["job losses","layoffs","unemployment rises","weak jobs","nfp miss","payrolls miss",
      "unemployment rises","jobless rate rises","jobs disappoint","employment falls"],
     "ECONOMIC_DATA", "BULLISH","BEARISH",82),

    # GDP / Growth: strong = USD UP, Gold DOWN
    (["gdp beats","strong gdp","economic expansion","growth accelerates","gdp surpasses",
      "gdp higher","gdp growth","economy grows","gdp above"],
     "ECONOMIC_DATA", "BEARISH","BULLISH",75),
    (["gdp contracts","recession","gdp miss","economic contraction","gdp shrinks",
      "gdp below","economy shrinks","negative gdp"],
     "ECONOMIC_DATA", "BULLISH","BEARISH",82),

    # Housing: strong = USD UP (economy healthy), Gold neutral/down
    (["housing starts beat","building permits surged","housing surpasses forecast"],
     "ECONOMIC_DATA", "NEUTRAL","BULLISH",62),
    (["housing starts miss","building permits fell","housing collapse"],
     "ECONOMIC_DATA", "MIXED","BEARISH",62),

    # Retail Sales
    (["retail sales beat","consumer spending surges","retail sales surpasses"],
     "ECONOMIC_DATA", "NEUTRAL","BULLISH",65),
    (["retail sales miss","consumer spending falls","weak retail"],
     "ECONOMIC_DATA", "MIXED","BEARISH",65),

    # ── FINANCIAL CRISIS ──────────────────────────────────────────────────
    (["bank collapse","banking crisis","financial crisis","credit crunch"],
     "GEOPOLITICAL", "BULLISH","BEARISH",88),
    (["debt ceiling","us default","credit downgrade","government shutdown"],
     "GEOPOLITICAL", "BULLISH","BEARISH",85),

    # ── OIL / COMMODITIES ─────────────────────────────────────────────────
    (["oil surges","crude rally","opec cut","energy crisis","oil spike"],
     "OTHER", "MIXED","MIXED",65),

    # ── CHINA / GEOPOLITICAL SECONDARY ───────────────────────────────────
    (["taiwan tension","china military","south china sea"],
     "GEOPOLITICAL", "BULLISH","MIXED",72),
    (["china slowdown","china crisis","china gdp miss"],
     "GEOPOLITICAL", "BULLISH","MIXED",65),
    (["trade deal","tariff removed","trade agreement signed"],
     "GEOPOLITICAL", "BEARISH","BULLISH",68),
    (["tariff","trade war","trade tension"],
     "GEOPOLITICAL", "BULLISH","MIXED",75),
]

def analyze_keywords(title: str, desc: str = "") -> dict | None:
    text = (title + " " + desc).lower()

    # رفض مقالات الرأي والتحليل الغير مؤكد
    opinion_signals = ["opinion:", "analysis:", "could", "might", "may signal",
                       "analyst says", "report suggests", "sources say", "according to unnamed",
                       "scenario", "what if", "could mean", "may indicate"]
    opinion_count = sum(1 for s in opinion_signals if s in text)
    if opinion_count >= 2:
        return None  # مقال رأي — تجاهل

    best, best_type, gi, ui = 0, "OTHER", "NEUTRAL", "NEUTRAL"
    for kws, ntype, g, u, base in NEWS_RULES:
        hits = sum(1 for k in kws if k in text)
        if hits:
            # زيادة بسيطة فقط للكلمات المتعددة — بدون مبالغة
            s = min(base + (hits - 1) * 3, base + 10)
            if s > best:
                best, best_type, gi, ui = s, ntype, g, u

    if best < 58:
        return None

    # تحديد اتجاه الذهب بناءً على السياق الكامل
    # إذا كان USD bullish مع data قوية → Gold bearish عادةً
    if best_type == "ECONOMIC_DATA" and ui == "BULLISH" and gi == "NEUTRAL":
        gi = "BEARISH"  # اقتصاد قوي = Gold تنخفض غالباً

    # سيناريوهات حسب النوع
    scenarios_map = {
        "ECONOMIC_DATA":   "If data beats → USD up, Gold down | If miss → Gold up",
        "MONETARY_POLICY": "If hawkish → USD up, Gold down | If dovish → Gold up",
        "GEOPOLITICAL":    "If escalates → Gold up | If resolves → Gold down",
        "OTHER":           "Watch price reaction before trading",
    }

    return {
        "relevant":     True,
        "news_type":    best_type,
        "score":        best,
        "impact_level": "HIGH" if best >= 80 else ("MEDIUM" if best >= 65 else "LOW"),
        "gold_impact":  gi,
        "usd_impact":   ui,
        "reason_en":    f"Keyword match: {best_type.lower().replace('_',' ')} event",
        "reason_ar":    f"تحليل مؤشرات: حدث {best_type}",
        "scenarios":    scenarios_map.get(best_type, "Monitor price action"),
        "watch":        "BOTH" if best >= 75 else ("XAUUSD" if gi != "NEUTRAL" else "DXY"),
        "confidence":   "LOW",  # keyword-only = low confidence دائماً
    }

def analyze_news(title: str, desc: str = "") -> dict | None:
    # فلتر مسبق: تجاهل مقالات الرأي والتحليل قبل استدعاء AI
    text_lower = (title + " " + desc).lower()
    opinion_patterns = [
        "opinion:", "analysis:", "commentary:", "perspective:",
        "what this means", "here's why", "explained:", "breakdown:",
        "should you", "how to trade", "trading guide",
    ]
    if any(p in text_lower for p in opinion_patterns):
        log.debug("🚫 Opinion article skipped: %s", title[:60])
        return None

    result = analyze_with_groq(title, desc)
    if result is None:
        result = analyze_with_gemini(title, desc)
    if result is None:
        result = analyze_keywords(title, desc)
    if result is None:
        return None

    # رفض أخبار RUMOR و OPINION من AI أيضاً
    if result.get("news_type") in ("OPINION", "RUMOR"):
        log.debug("🚫 AI classified as opinion/rumor: %s", title[:60])
        return None

    # رفض بناءً على relevant أو score أو impact
    if not result.get("relevant", True):
        return None
    if result.get("impact_level") in ("LOW", "IGNORE") or result.get("score", 0) < 60:
        return None

    return result


# ════════════════════════════════════════════════════════════════
#  FIX 3: RSS SOURCES — إزالة المصادر المحجوبة + إضافة بدائل
# ════════════════════════════════════════════════════════════════
# Reuters feeds.reuters.com → محجوب على GitHub Actions
# ForexLive → XML encoding error
# DailyFX feeds/all → 403

RSS_SOURCES = [
    # ✅ تعمل على GitHub Actions
    ("https://www.marketwatch.com/rss/topstories",                          "MarketWatch"),
    ("https://www.investing.com/rss/news_25.rss",                           "Investing Gold"),
    ("https://www.investing.com/rss/news_1.rss",                            "Investing Forex"),
    ("https://www.fxstreet.com/rss",                                        "FXStreet"),
    ("https://feeds.bbci.co.uk/news/business/rss.xml",                     "BBC Business"),
    ("https://www.cnbc.com/id/10000664/device/rss/rss.html",               "CNBC Economy"),
    ("https://feeds.content.dowjones.io/public/rss/mw_realtimeheadlines",  "MarketWatch RT"),
    # بدائل جديدة موثوقة
    ("https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=15839135", "CNBC Markets"),
    ("https://finance.yahoo.com/news/rssindex",                             "Yahoo Finance"),
    ("https://www.forexcrunch.com/feed/",                                   "ForexCrunch"),
    ("https://www.fxempire.com/api/v1/en/articles/rss",                    "FX Empire"),
]

RELEVANCE_KW = [
    "gold","xauusd","usd","dollar","fed","federal reserve","fomc",
    "inflation","interest rate","gdp","recession","economy","economic",
    "treasury","bond","yield","war","conflict","sanction","tariff",
    "trade","oil","crude","opec","china","safe haven","powell",
    "jobs","payroll","unemployment","cpi","ppi","debt","crisis",
    "forex","currency","iran","russia","ukraine","nato","geopolit",
]

def fetch_rss(url: str, name: str) -> list[dict]:
    try:
        time.sleep(random.uniform(0.2, 0.8))
        r = requests.get(url, headers=get_headers({"Referer": url}), timeout=20)
        r.raise_for_status()
        # إصلاح encoding إذا كان هناك مشكلة
        content = r.content
        try:
            root = ET.fromstring(content)
        except ET.ParseError:
            # محاولة إصلاح XML غير صالح
            content = re.sub(rb'[\x00-\x08\x0b\x0c\x0e-\x1f]', b'', content)
            try:
                root = ET.fromstring(content)
            except ET.ParseError as e:
                log.warning("⚠️ RSS XML parse [%s]: %s", name, e)
                return []

        items = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
        news  = []
        for item in items[:15]:
            def g(tag):
                el = item.find(tag)
                return (el.text or "").strip() if el is not None else ""
            title = g("title") or g("{http://www.w3.org/2005/Atom}title")
            desc  = g("description") or g("{http://www.w3.org/2005/Atom}summary") or g("content")
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
        log.warning("⚠️ RSS [%s]: %s", name, str(e)[:100])
        return []

def fetch_newsapi() -> list[dict]:
    if not NEWS_API_KEY:
        return []
    try:
        r = requests.get(
            "https://newsapi.org/v2/everything",
            params={
                "q":        "(gold OR xauusd OR \"federal reserve\" OR inflation OR sanctions OR tariff) AND (USD OR economy)",
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
    all_news = []
    for url, name in RSS_SOURCES:
        all_news.extend(fetch_rss(url, name))
    all_news.extend(fetch_newsapi())
    now_utc = datetime.now(timezone.utc)
    cutoff  = now_utc - timedelta(hours=4)
    filtered = [
        n for n in all_news
        if n["time_utc"] >= cutoff
        and any(kw in (n["title"]+" "+n["description"]).lower() for kw in RELEVANCE_KW)
    ]
    log.info("📦 بعد الفلترة: %d / %d", len(filtered), len(all_news))
    return filtered


# ════════════════════════════════════════════════════════════════
#  FIX 4: ECONOMIC CALENDAR — روابط بديلة لـ ForexFactory
# ════════════════════════════════════════════════════════════════

TRANSLATIONS = {
    "Non-Farm Employment Change":"التغير في الوظائف غير الزراعية",
    "Non-Farm Payrolls":"الرواتب غير الزراعية",
    "Unemployment Rate":"معدل البطالة",
    "CPI m/m":"مؤشر أسعار المستهلك (شهري)",
    "Core CPI m/m":"مؤشر أسعار المستهلك الأساسي",
    "GDP q/q":"الناتج المحلي الإجمالي",
    "Federal Funds Rate":"سعر الفائدة الفيدرالي",
    "FOMC Statement":"بيان الاحتياطي الفيدرالي",
    "FOMC Press Conference":"مؤتمر الاحتياطي الفيدرالي",
    "ISM Manufacturing PMI":"مؤشر PMI التصنيعي",
    "ISM Services PMI":"مؤشر PMI للخدمات",
    "Retail Sales m/m":"مبيعات التجزئة",
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
        if v in (None,"","N/A","nan"): return None
        return float(re.sub(r"[%KMBkb,\s]","",str(v)).strip())
    except: return None

def fmtv(v, u="") -> str:
    if v is None: return "—"
    s = f"{v:,.1f}" if abs(v)>=1000 else (f"{v:.2f}" if abs(v)>=10 else f"{v:.3f}")
    return s+u

def fmtt(dt: datetime) -> str:
    return dt.astimezone(NY_TZ).strftime("%I:%M %p ET")

# روابط ForexFactory البديلة
FF_CALENDAR_URLS = [
    # الرابط الرسمي بتاريخ محدد
    "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
    # بديل بالتاريخ الصريح
    f"https://nfs.faireconomy.media/ff_calendar_{datetime.now(timezone.utc).strftime('%Y%m%d')}.json",
    # بديل آخر
    "https://cdn-nfs.faireconomy.media/ff_calendar_thisweek.json",
]

def fetch_ff_calendar() -> list[dict]:
    """جلب التقويم من ForexFactory مع عدة روابط بديلة."""
    events = []
    for url in FF_CALENDAR_URLS:
        try:
            time.sleep(random.uniform(0.3, 0.8))
            r = requests.get(
                url,
                headers=get_headers({"Referer": "https://www.forexfactory.com/"}),
                timeout=20,
            )
            if r.status_code == 404:
                log.debug("FF URL 404: %s", url)
                continue
            r.raise_for_status()
            if not r.text.strip():
                log.debug("FF URL empty: %s", url)
                continue
            data = r.json()
            if not isinstance(data, list) or len(data) == 0:
                continue
            log.info("✅ FF Calendar from: %s (%d events)", url.split("/")[-1], len(data))
            for item in data:
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
                    "source":   "ForexFactory",
                })
            if events:
                break  # نجح — لا داعي لتجربة الروابط الأخرى
        except Exception as e:
            log.warning("⚠️ FF Calendar [%s]: %s", url.split("/")[-1], str(e)[:80])

    return events

def fetch_fmp_calendar() -> list[dict]:
    if not FMP_API_KEY:
        return []
    try:
        now = datetime.now(timezone.utc)
        r = requests.get(
            "https://financialmodelingprep.com/api/v3/economic_calendar",
            params={
                "from":   now.strftime("%Y-%m-%d"),
                "to":     (now+timedelta(days=1)).strftime("%Y-%m-%d"),
                "apikey": FMP_API_KEY,
            },
            headers=get_headers(),
            timeout=15,
        )
        r.raise_for_status()
        events = []
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
                "source":   "FMP",
            })
        log.info("✅ FMP Calendar: %d events", len(events))
        return events
    except Exception as e:
        log.warning("⚠️ FMP Calendar: %s", e)
        return []

def fetch_calendar() -> list[dict]:
    ff  = fetch_ff_calendar()
    fmp = fetch_fmp_calendar()
    # دمج وإزالة تكرار
    seen, merged = {}, []
    for ev in ff + fmp:
        key = ev["name"][:15].lower() + ev["time_utc"].strftime("%Y%m%d%H")
        if key not in seen:
            seen[key] = True
            merged.append(ev)
    log.info("📅 التقويم الكلي: %d أحداث USD عالية التأثير", len(merged))
    return merged

_CAL_SENT: set[str] = set()

def has_new_actual(ev: dict) -> bool:
    if ev.get("actual") is None:
        return False
    key = f"cal_{news_hash(ev['name'])}_{ev['time_utc'].strftime('%Y%m%d')}"
    if key in _CAL_SENT:
        return False
    _CAL_SENT.add(key)
    return True


# ════════════════════════════════════════════════════════════════
#  MESSAGE BUILDERS
# ════════════════════════════════════════════════════════════════

IMPACT_EMOJI = {
    "BULLISH": "🟢 BULLISH | صاعد",
    "BEARISH": "🔴 BEARISH | هابط",
    "MIXED":   "🟡 MIXED   | متذبذب",
    "NEUTRAL": "⚪ NEUTRAL | محايد",
}

NEWS_TYPE_LABEL = {
    "ECONOMIC_DATA":   "📈 Economic Data",
    "MONETARY_POLICY": "🏦 Monetary Policy",
    "GEOPOLITICAL":    "🌍 Geopolitical",
    "RUMOR":           "❓ Unverified Report",
    "OPINION":         "💬 Opinion/Analysis",
    "OTHER":           "📰 Market News",
}

CONFIDENCE_LABEL = {
    "HIGH":   "✅ High Confidence",
    "MEDIUM": "⚠️ Medium Confidence",
    "LOW":    "🔶 Low Confidence (keywords only)",
}

def build_news_msg(item: dict) -> str:
    a    = item["analysis"]
    now  = datetime.now(NY_TZ).strftime("%I:%M %p ET")
    score = a.get("score", 0)
    icon = "🚨" if score >= 80 else ("⚡" if score >= 65 else "📢")
    lvl  = "🔴 HIGH IMPACT" if a["impact_level"] == "HIGH" else "🟡 MEDIUM IMPACT"
    ntype = NEWS_TYPE_LABEL.get(a.get("news_type", "OTHER"), "📰 Market News")
    conf  = CONFIDENCE_LABEL.get(a.get("confidence", "LOW"), "")

    lines = [
        f"{icon} *BREAKING NEWS | خبر عاجل*",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"📡 *{item['source']}*   🕐 {now}",
        f"🏷 {ntype}",
        "",
        f"📰 *{item['title'][:200]}*",
    ]
    if item.get("description") and len(item["description"]) > 30:
        lines.append(f"_{item['description'][:150]}..._")
    lines += [
        "",
        f"*{lvl}*   Score: `{score}/100`   {conf}",
        "",
        "*📊 Expected Impact | التأثير المتوقع:*",
        f"🥇 Gold  | الذهب:   {IMPACT_EMOJI.get(a['gold_impact'], a['gold_impact'])}",
        f"💵 USD   | الدولار: {IMPACT_EMOJI.get(a['usd_impact'], a['usd_impact'])}",
        "",
        f"💡 _{a.get('reason_en', '')}_",
        f"💡 _{a.get('reason_ar', '')}_",
    ]

    # إضافة سيناريوهات إذا كانت موجودة
    if a.get("scenarios") and a.get("confidence") != "LOW":
        lines += ["", f"🔀 *Scenarios:* _{a['scenarios']}_"]

    lines += [
        "",
        f"⚠️ *Watch {a.get('watch', 'XAUUSD')} volatility*",
    ]
    if item.get("link"):
        lines.append(f"\n🔗 [Read more]({item['link']})")
    lines += ["━━━━━━━━━━━━━━━━━━━━━━━━━━━", "🤖 _Forex Alert Bot v5.2_"]
    return "\n".join(lines)

def build_calendar_msg(released: list[dict], upcoming: list[dict]) -> str:
    now_ny  = datetime.now(NY_TZ)
    days_ar = ["الإثنين","الثلاثاء","الأربعاء","الخميس","الجمعة","السبت","الأحد"]
    lines   = [
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "📊 *ECONOMIC CALENDAR | التقويم*",
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
                "   💡 *NEUTRAL ⚪*"
            ),"",
        ]
    if not released:
        lines += ["   _لا أحداث صادرة | No released events_",""]

    now_utc = datetime.now(timezone.utc)
    lines  += [f"⏳ *UPCOMING | قادم* _(next {UPCOMING_WINDOW_H}h)_","─────────────────────────"]
    for ev in upcoming:
        dm  = int((ev["time_utc"]-now_utc).total_seconds()/60)
        eta = f"{dm}m" if dm<60 else f"{dm//60}h {dm%60}m"
        u   = ev.get("unit","")
        lines += [
            f"⚡ *{ev['name']}*",
            f"   _{translate(ev['name'])}_",
            f"   🕐 {fmtt(ev['time_utc'])} _(in {eta})_",
            f"   Forecast: `{fmtv(ev.get('forecast'),u)}`  Prev: `{fmtv(ev.get('previous'),u)}`","",
        ]
    if not upcoming:
        lines += [f"   _لا أحداث قادمة خلال {UPCOMING_WINDOW_H}h_",""]

    lines += ["━━━━━━━━━━━━━━━━━━━━━━━━━━━","🤖 _Forex Alert Bot v5.2_"]
    return "\n".join(lines)


# ════════════════════════════════════════════════════════════════
#  TELEGRAM
# ════════════════════════════════════════════════════════════════

def send_telegram(msg: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not CHAT_ID:
        log.error("❌ TOKEN أو CHAT_ID مفقود")
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id":CHAT_ID,"text":msg,"parse_mode":"Markdown","disable_web_page_preview":True},
            timeout=15,
        )
        data = r.json()
        if not data.get("ok"):
            log.error("❌ Telegram: %s", data)
            return False
        log.info("✅ تم الإرسال! id=%s", data["result"]["message_id"])
        return True
    except Exception as e:
        log.error("❌ Telegram: %s", e)
        return False


# ════════════════════════════════════════════════════════════════
#  MAIN
# ════════════════════════════════════════════════════════════════

def run() -> None:
    now_utc = datetime.now(timezone.utc)
    log.info("⚡ Forex Alert Bot v5.2 — %s UTC", now_utc.strftime("%Y-%m-%d %H:%M"))
    log.info("🤖 AI: %s | 💾 Dedup: %s",
             "Groq" if GROQ_API_KEY else ("Gemini" if GEMINI_API_KEY else "Keywords"),
             "Supabase" if (SUPABASE_URL and SUPABASE_KEY) else "In-Memory")

    if not TELEGRAM_BOT_TOKEN or not CHAT_ID:
        log.error("❌ TELEGRAM_BOT_TOKEN أو CHAT_ID مفقود!"); sys.exit(1)

    cleanup_old_hashes()
    total_sent = 0

    # ── PART 1: Breaking News ──────────────────────────────────
    log.info("━━━ الأخبار العاجلة ━━━")
    raw_news  = fetch_all_news()
    analyzed  = []
    for item in raw_news:
        if is_duplicate(item["title"]):
            continue
        result = analyze_news(item["title"], item["description"])
        if result:
            item["analysis"] = result
            analyzed.append(item)
        if len(analyzed) >= 3:
            break

    log.info("📊 أخبار مؤهلة: %d", len(analyzed))
    for item in sorted(analyzed, key=lambda x: x["analysis"]["score"], reverse=True):
        if send_telegram(build_news_msg(item)):
            mark_sent(item["title"], item["source"])
            total_sent += 1
            time.sleep(2)

    # ── PART 2: Economic Calendar ──────────────────────────────
    log.info("━━━ التقويم الاقتصادي ━━━")
    events  = fetch_calendar()
    cutoff  = now_utc + timedelta(hours=UPCOMING_WINDOW_H)

    released    = sorted([e for e in events if e["time_utc"]<=now_utc and has_new_actual(e)], key=lambda e: e["time_utc"])
    upcoming_30 = [e for e in events if now_utc < e["time_utc"] <= now_utc+timedelta(minutes=35)]
    upcoming_4h = sorted([e for e in events if now_utc < e["time_utc"] <= cutoff], key=lambda e: e["time_utc"])

    if released or upcoming_30:
        log.info("📅 إرسال التقويم — صادر:%d قادم30m:%d", len(released), len(upcoming_30))
        if send_telegram(build_calendar_msg(released, upcoming_4h)):
            total_sent += 1
    else:
        log.info("⏭️ تخطي التقويم — لا جديد")

    log.info("✅ انتهى — %d رسائل أُرسلت", total_sent)


if __name__ == "__main__":
    run()
