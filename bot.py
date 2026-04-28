"""
╔══════════════════════════════════════════════════════════════════╗
║   FOREX & GOLD NEWS ALERT BOT v4 — bot.py                       ║
║   Economic Calendar + Breaking News Analysis                     ║
║   Sources: ForexFactory | FMP | DailyFX | RSS | NewsAPI         ║
║   Languages: Arabic + English                                    ║
╚══════════════════════════════════════════════════════════════════╝
"""

import os, sys, re, json, logging, hashlib, requests, xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

# ─────────────────────────── Logging ────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

# ─────────────────────────── Config ─────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID            = os.getenv("CHAT_ID", "")
FMP_API_KEY        = os.getenv("API_KEY", "")
NEWS_API_KEY       = os.getenv("NEWS_API_KEY", "")   # newsapi.org free key
UPCOMING_WINDOW_H  = 4
TARGET_CURRENCY    = "USD"
NY_TZ              = ZoneInfo("America/New_York")

# ────── Deduplication: keep sent hashes in memory (GitHub Actions = stateless)
# For persistent dedup across runs, store in a file committed to repo or use
# GitHub Actions cache. Here we deduplicate within a single run session.
_SENT_HASHES: set[str] = set()

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/xml, */*",
}


# ════════════════════════════════════════════════════════════════
#  TRANSLATIONS — قاموس الترجمة
# ════════════════════════════════════════════════════════════════

TRANSLATIONS = {
    "Non-Farm Employment Change":     "التغير في الوظائف غير الزراعية",
    "Non-Farm Payrolls":              "الرواتب غير الزراعية",
    "Unemployment Rate":              "معدل البطالة",
    "CPI m/m":                        "مؤشر أسعار المستهلك (شهري)",
    "Core CPI m/m":                   "مؤشر أسعار المستهلك الأساسي",
    "GDP q/q":                        "الناتج المحلي الإجمالي",
    "Federal Funds Rate":             "سعر الفائدة الفيدرالي",
    "FOMC Statement":                 "بيان لجنة الاحتياطي الفيدرالي",
    "FOMC Press Conference":          "مؤتمر الاحتياطي الفيدرالي",
    "ISM Manufacturing PMI":          "مؤشر PMI التصنيعي",
    "ISM Services PMI":               "مؤشر PMI للخدمات",
    "Retail Sales m/m":               "مبيعات التجزئة (شهري)",
    "Core Retail Sales m/m":          "مبيعات التجزئة الأساسية",
    "PPI m/m":                        "مؤشر أسعار المنتجين",
    "Trade Balance":                  "الميزان التجاري",
    "Consumer Confidence":            "ثقة المستهلك",
    "Initial Jobless Claims":         "طلبات إعانة البطالة",
    "ADP Non-Farm Employment Change": "تقرير ADP للوظائف",
    "JOLTs Job Openings":             "فرص العمل JOLTs",
    "PCE Price Index m/m":            "مؤشر أسعار PCE",
    "Core PCE Price Index m/m":       "مؤشر PCE الأساسي",
    "Durable Goods Orders m/m":       "طلبيات السلع المعمرة",
    "Building Permits":               "تصاريح البناء",
    "New Home Sales":                 "مبيعات المنازل الجديدة",
    "Existing Home Sales":            "مبيعات المنازل القائمة",
}

def translate(name: str) -> str:
    for en, ar in TRANSLATIONS.items():
        if en.lower() in name.lower():
            return ar
    return name


# ════════════════════════════════════════════════════════════════
#  NEWS IMPACT SCORING ENGINE
#  محرك تحليل وتقييم الأخبار
# ════════════════════════════════════════════════════════════════

# كل قاعدة: (كلمات مفتاحية, تأثير على الذهب, تأثير على USD, نقاط الأهمية)
NEWS_RULES = [
    # ── Federal Reserve / الاحتياطي الفيدرالي ──────────────────
    (["federal reserve", "fed rate", "fomc", "powell", "interest rate hike",
      "rate decision", "monetary policy tighten"],
     "BEARISH", "BULLISH", 90),

    (["fed cut", "rate cut", "dovish fed", "fed pivot", "pause rate",
      "lower interest rate"],
     "BULLISH", "BEARISH", 90),

    (["fed hold", "fed pause", "rates unchanged", "hold rates"],
     "NEUTRAL", "NEUTRAL", 60),

    # ── War / Conflict / الحرب والتوترات ──────────────────────
    (["war", "military strike", "invasion", "conflict escalat", "missile attack",
      "nuclear threat", "troops deploy", "armed conflict", "warfare"],
     "BULLISH", "MIXED", 95),

    (["ceasefire", "peace deal", "truce", "de-escalat", "diplomatic solution"],
     "BEARISH", "MIXED", 80),

    # ── Geopolitical / جيوسياسي ──────────────────────────────
    (["sanction", "embargo", "trade war", "tariff", "trade restriction",
      "export ban", "import duty"],
     "BULLISH", "MIXED", 85),

    (["trade deal", "trade agreement", "tariff removed", "trade truce"],
     "BEARISH", "BULLISH", 75),

    # ── Inflation / التضخم ────────────────────────────────────
    (["inflation surges", "inflation rises", "cpi higher", "ppi higher",
      "hot inflation", "inflation above"],
     "BULLISH", "MIXED", 80),

    (["inflation cools", "inflation drops", "deflation", "cpi lower",
      "disinflation"],
     "BEARISH", "BULLISH", 80),

    # ── Recession / الركود ───────────────────────────────────
    (["recession", "economic slowdown", "gdp contracts", "gdp shrinks",
      "negative growth", "stagflation"],
     "BULLISH", "BEARISH", 85),

    (["strong gdp", "gdp growth", "economic expansion", "strong economy"],
     "BEARISH", "BULLISH", 70),

    # ── US Debt / Fiscal / الديون والمالية ───────────────────
    (["debt ceiling", "us default", "government shutdown", "fiscal crisis",
      "credit downgrade", "fitch downgrade", "moody downgrade"],
     "BULLISH", "BEARISH", 90),

    (["debt deal", "budget approved", "deficit reduced", "fiscal surplus"],
     "BEARISH", "BULLISH", 65),

    # ── Banking Crisis / الأزمات المصرفية ────────────────────
    (["bank collapse", "bank run", "banking crisis", "svb", "credit suisse",
      "financial crisis", "bank failure", "systemic risk"],
     "BULLISH", "BEARISH", 92),

    # ── Oil / النفط ──────────────────────────────────────────
    (["oil price surge", "oil jumps", "crude rally", "opec cut",
      "energy crisis", "oil supply shock"],
     "BULLISH", "MIXED", 70),

    (["oil price drops", "crude falls", "opec increase", "oil glut"],
     "BEARISH", "MIXED", 60),

    # ── China / الصين ────────────────────────────────────────
    (["china slowdown", "china crisis", "china default", "evergrande",
      "china tension", "taiwan strait", "sino-us"],
     "BULLISH", "MIXED", 80),

    (["china stimulus", "china recovery", "china growth"],
     "BEARISH", "MIXED", 65),

    # ── Safe Haven / ملاذ آمن ────────────────────────────────
    (["safe haven", "gold demand", "gold rally", "gold surges",
      "buy gold", "flight to safety"],
     "BULLISH", "BEARISH", 85),

    (["risk on", "stock rally", "risk appetite", "equity surge"],
     "BEARISH", "BULLISH", 65),

    # ── Jobs / Employment / سوق العمل ────────────────────────
    (["jobs report", "nonfarm payroll", "strong jobs", "low unemployment",
      "labor market tight"],
     "BEARISH", "BULLISH", 75),

    (["job losses", "layoffs", "unemployment rises", "weak jobs"],
     "BULLISH", "BEARISH", 75),

    # ── Breaking / عاجل ──────────────────────────────────────
    (["breaking", "urgent", "flash", "alert", "emergency"],
     "MIXED", "MIXED", 70),
]

IMPACT_LABELS = {
    "BULLISH": "🟢 BULLISH صاعد",
    "BEARISH": "🔴 BEARISH هابط",
    "MIXED":   "🟡 MIXED متذبذب",
    "NEUTRAL": "⚪ NEUTRAL محايد",
}

def score_news(title: str, description: str = "") -> dict | None:
    """
    تحليل الخبر وحساب نقاط الأهمية وتحديد الأثر على الذهب والدولار.
    يُعيد None إذا كانت الأهمية منخفضة جداً.
    """
    text = (title + " " + description).lower()
    best_score  = 0
    gold_impact = "NEUTRAL"
    usd_impact  = "NEUTRAL"
    matched_rule = None

    for keywords, gold, usd, base_score in NEWS_RULES:
        hits = sum(1 for kw in keywords if kw in text)
        if hits == 0:
            continue
        # زيادة النقاط إذا تطابقت كلمات متعددة
        score = base_score + (hits - 1) * 5
        if score > best_score:
            best_score   = score
            gold_impact  = gold
            usd_impact   = usd
            matched_rule = keywords[0]

    if best_score < 55:
        return None  # تجاهل الأخبار منخفضة الأهمية

    if best_score >= 85:
        level = "🔴 HIGH IMPACT | عالي التأثير"
    elif best_score >= 70:
        level = "🟡 MEDIUM IMPACT | متوسط التأثير"
    else:
        level = "🔵 LOW-MEDIUM | منخفض-متوسط"

    return {
        "score":       best_score,
        "level":       level,
        "gold_impact": gold_impact,
        "usd_impact":  usd_impact,
        "matched":     matched_rule,
    }


# ════════════════════════════════════════════════════════════════
#  DEDUPLICATION
# ════════════════════════════════════════════════════════════════

def news_hash(title: str) -> str:
    """Hash فريد للخبر بناءً على الكلمات الأساسية."""
    clean = re.sub(r"\W+", " ", title.lower()).strip()
    words = clean.split()[:8]  # أول 8 كلمات تكفي
    return hashlib.md5(" ".join(words).encode()).hexdigest()[:12]

def load_sent_hashes() -> set[str]:
    """تحميل الهاشات المرسلة من ملف محلي (للاستخدام في GitHub Actions Cache)."""
    path = "/tmp/forex_bot_sent.json"
    try:
        with open(path) as f:
            return set(json.load(f))
    except:
        return set()

def save_sent_hashes(hashes: set[str]) -> None:
    """حفظ الهاشات لتجنب التكرار في نفس الـ run."""
    path = "/tmp/forex_bot_sent.json"
    try:
        with open(path, "w") as f:
            json.dump(list(hashes), f)
    except:
        pass

def is_duplicate(title: str, sent: set[str]) -> bool:
    h = news_hash(title)
    if h in sent:
        return True
    sent.add(h)
    return False


# ════════════════════════════════════════════════════════════════
#  UTILS
# ════════════════════════════════════════════════════════════════

def safe_float(val) -> float | None:
    try:
        if val in (None, "", "N/A"): return None
        return float(re.sub(r"[%KMBkb,\s]", "", str(val)).strip())
    except:
        return None

def fmtv(val, unit="") -> str:
    if val is None: return "—"
    s = f"{val:,.1f}" if abs(val)>=1000 else (f"{val:.2f}" if abs(val)>=10 else f"{val:.3f}")
    return s + unit

def fmtt(dt: datetime) -> str:
    return dt.astimezone(NY_TZ).strftime("%I:%M %p ET")


# ════════════════════════════════════════════════════════════════
#  MODULE 1: fetch_news() — جلب الأخبار
# ════════════════════════════════════════════════════════════════

def fetch_rss(url: str, source_name: str) -> list[dict]:
    """جلب أخبار من RSS Feed وتحويلها لتنسيق موحّد."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        root = ET.fromstring(r.content)

        items = root.findall(".//item")
        if not items:
            items = root.findall(".//{http://www.w3.org/2005/Atom}entry")

        news = []
        for item in items[:20]:  # آخر 20 خبر فقط
            def g(tag):
                el = item.find(tag)
                return el.text.strip() if el is not None and el.text else ""

            title = g("title") or g("{http://www.w3.org/2005/Atom}title")
            desc  = g("description") or g("{http://www.w3.org/2005/Atom}summary")
            link  = g("link") or g("{http://www.w3.org/2005/Atom}link")
            pub   = g("pubDate") or g("published") or g("{http://www.w3.org/2005/Atom}published")

            if not title:
                continue

            # تحليل التاريخ
            try:
                from email.utils import parsedate_to_datetime
                dt = parsedate_to_datetime(pub).astimezone(timezone.utc)
            except:
                dt = datetime.now(timezone.utc)

            news.append({
                "title":       title,
                "description": re.sub(r"<[^>]+>", "", desc)[:300],
                "link":        link,
                "source":      source_name,
                "time_utc":    dt,
            })

        log.info("📰 %s: %d أخبار", source_name, len(news))
        return news

    except Exception as e:
        log.warning("⚠️ RSS [%s] error: %s", source_name, e)
        return []


def fetch_newsapi(query: str = "gold USD federal reserve economy") -> list[dict]:
    """
    NewsAPI.org — 100 طلب/يوم مجانياً.
    يتطلب NEWS_API_KEY في Secrets.
    """
    if not NEWS_API_KEY:
        log.info("ℹ️ NewsAPI: لا يوجد NEWS_API_KEY - تخطي")
        return []
    try:
        r = requests.get(
            "https://newsapi.org/v2/everything",
            params={
                "q":          query,
                "language":   "en",
                "sortBy":     "publishedAt",
                "pageSize":   20,
                "apiKey":     NEWS_API_KEY,
            },
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        news = []
        for a in data.get("articles", []):
            try:
                dt = datetime.fromisoformat(
                    a["publishedAt"].replace("Z", "+00:00")
                ).astimezone(timezone.utc)
            except:
                dt = datetime.now(timezone.utc)
            news.append({
                "title":       a.get("title", ""),
                "description": (a.get("description") or "")[:300],
                "link":        a.get("url", ""),
                "source":      a.get("source", {}).get("name", "NewsAPI"),
                "time_utc":    dt,
            })
        log.info("📰 NewsAPI: %d أخبار", len(news))
        return news
    except Exception as e:
        log.warning("⚠️ NewsAPI error: %s", e)
        return []


def fetch_gnews(query: str = "gold USD economy federal reserve") -> list[dict]:
    """
    GNews.io — بديل مجاني لـ NewsAPI (100 طلب/يوم).
    لا يحتاج مفتاح للطلبات المحدودة.
    """
    try:
        r = requests.get(
            "https://gnews.io/api/v4/search",
            params={
                "q":        query,
                "lang":     "en",
                "max":      10,
                "sortby":   "publishedAt",
                "token":    os.getenv("GNEWS_API_KEY", ""),
            },
            headers=HEADERS,
            timeout=15,
        )
        if r.status_code == 403:
            log.info("ℹ️ GNews: يحتاج GNEWS_API_KEY - تخطي")
            return []
        r.raise_for_status()
        data = r.json()
        news = []
        for a in data.get("articles", []):
            try:
                dt = datetime.fromisoformat(
                    a["publishedAt"].replace("Z", "+00:00")
                ).astimezone(timezone.utc)
            except:
                dt = datetime.now(timezone.utc)
            news.append({
                "title":       a.get("title", ""),
                "description": (a.get("description") or "")[:300],
                "link":        a.get("url", ""),
                "source":      a.get("source", {}).get("name", "GNews"),
                "time_utc":    dt,
            })
        log.info("📰 GNews: %d أخبار", len(news))
        return news
    except Exception as e:
        log.warning("⚠️ GNews error: %s", e)
        return []


# RSS Feeds الموثوقة
RSS_SOURCES = [
    ("https://feeds.reuters.com/reuters/businessNews",           "Reuters Business"),
    ("https://feeds.reuters.com/news/wealth",                    "Reuters Wealth"),
    ("https://www.marketwatch.com/rss/topstories",               "MarketWatch"),
    ("https://feeds.content.dowjones.io/public/rss/mw_realtimeheadlines", "MarketWatch RT"),
    ("https://www.investing.com/rss/news_25.rss",                "Investing.com Gold"),
    ("https://www.investing.com/rss/news_1.rss",                 "Investing.com Forex"),
    ("https://www.forexlive.com/feed/news",                      "ForexLive"),
    ("https://www.fxstreet.com/rss",                             "FXStreet"),
    ("https://www.dailyfx.com/feeds/all",                        "DailyFX News"),
    ("https://feeds.bbci.co.uk/news/business/rss.xml",          "BBC Business"),
    ("https://www.cnbc.com/id/10000664/device/rss/rss.html",    "CNBC Economy"),
]

def fetch_all_news() -> list[dict]:
    """جلب الأخبار من جميع المصادر."""
    all_news = []

    # RSS Feeds
    for rss_url, name in RSS_SOURCES:
        all_news.extend(fetch_rss(rss_url, name))

    # APIs
    all_news.extend(fetch_newsapi())
    all_news.extend(fetch_gnews())

    log.info("📦 إجمالي الأخبار قبل الفلترة: %d", len(all_news))
    return all_news


# ════════════════════════════════════════════════════════════════
#  MODULE 2: filter_news() — فلترة الأخبار
# ════════════════════════════════════════════════════════════════

# كلمات تدل على أن الخبر مالي/اقتصادي
RELEVANCE_KEYWORDS = [
    "gold", "xauusd", "usd", "dollar", "fed", "federal reserve", "fomc",
    "inflation", "interest rate", "gdp", "recession", "economy", "economic",
    "treasury", "bond", "yield", "war", "conflict", "sanction", "tariff",
    "trade", "oil", "crude", "opec", "china", "geopolit", "safe haven",
    "jobs", "payroll", "unemployment", "cpi", "ppi", "pce", "debt",
    "bank", "crisis", "market", "forex", "currency", "powell", "yellen",
    "imf", "world bank", "g7", "g20", "nato", "iran", "russia", "ukraine",
]

def filter_news(news_list: list[dict]) -> list[dict]:
    """
    فلترة الأخبار:
    1. آخر 4 ساعات فقط
    2. يجب أن يحتوي على كلمة ذات صلة مالية
    """
    now_utc = datetime.now(timezone.utc)
    cutoff  = now_utc - timedelta(hours=4)
    filtered = []

    for item in news_list:
        # ── فلتر الوقت
        if item["time_utc"] < cutoff:
            continue

        # ── فلتر الصلة
        text = (item["title"] + " " + item["description"]).lower()
        if not any(kw in text for kw in RELEVANCE_KEYWORDS):
            continue

        filtered.append(item)

    log.info("🔍 بعد الفلترة: %d خبر مؤهل", len(filtered))
    return filtered


# ════════════════════════════════════════════════════════════════
#  MODULE 3: score_news() — تقييم الأهمية (defined above)
#  MODULE 4: analyze_impact() — تحليل الأثر
# ════════════════════════════════════════════════════════════════

def analyze_impact(news_list: list[dict], sent_hashes: set[str]) -> list[dict]:
    """
    تحليل كل خبر وتصفية:
    - الأخبار منخفضة التأثير
    - الأخبار المكررة
    """
    scored = []
    for item in news_list:
        # تجنب التكرار
        if is_duplicate(item["title"], sent_hashes):
            continue

        analysis = score_news(item["title"], item["description"])
        if analysis is None:
            continue

        item["analysis"] = analysis
        scored.append(item)

    # ترتيب حسب النقاط
    scored.sort(key=lambda x: x["analysis"]["score"], reverse=True)
    log.info("📊 أخبار مؤثرة بعد التحليل: %d", len(scored))
    return scored


# ════════════════════════════════════════════════════════════════
#  ECONOMIC CALENDAR (من النسخ السابقة)
# ════════════════════════════════════════════════════════════════

def fetch_ff_calendar() -> list[dict]:
    events = []
    for url in [
        "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
        "https://nfs.faireconomy.media/ff_calendar_nextweek.json",
    ]:
        try:
            r = requests.get(url, headers=HEADERS, timeout=20)
            r.raise_for_status()
            for item in r.json():
                if (item.get("country") or "").upper() != TARGET_CURRENCY: continue
                if (item.get("impact") or "").lower() != "high": continue
                try:
                    dt = datetime.fromisoformat(item["date"]).astimezone(timezone.utc)
                except: continue
                events.append({
                    "name":     item.get("title", "Unknown"),
                    "time_utc": dt,
                    "actual":   safe_float(item.get("actual")),
                    "forecast": safe_float(item.get("forecast")),
                    "previous": safe_float(item.get("previous")),
                    "unit":     "",
                    "source":   "ForexFactory",
                })
        except Exception as e:
            log.warning("⚠️ FF Calendar error: %s", e)
    log.info("📅 التقويم: %d أحداث USD عالية التأثير", len(events))
    return events

def fetch_fmp_calendar() -> list[dict]:
    if not FMP_API_KEY: return []
    now = datetime.now(timezone.utc)
    try:
        r = requests.get(
            "https://financialmodelingprep.com/api/v3/economic_calendar",
            params={
                "from":   now.strftime("%Y-%m-%d"),
                "to":     (now + timedelta(days=1)).strftime("%Y-%m-%d"),
                "apikey": FMP_API_KEY,
            },
            timeout=15,
        )
        r.raise_for_status()
        events = []
        for item in (r.json() if isinstance(r.json(), list) else []):
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
        return events
    except Exception as e:
        log.warning("⚠️ FMP error: %s", e)
        return []

def classify_calendar(ev: dict) -> str:
    a, f = ev.get("actual"), ev.get("forecast")
    if a is None or f is None: return "NEUTRAL"
    return "BULLISH" if a > f else ("BEARISH" if a < f else "NEUTRAL")


# ════════════════════════════════════════════════════════════════
#  MODULE 5: send_telegram() — بناء وإرسال الرسائل
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
            log.error("❌ Telegram error: %s", data)
            return False
        log.info("✅ تم الإرسال! id=%s", data["result"]["message_id"])
        return True
    except Exception as e:
        log.error("❌ Telegram failed: %s", e)
        return False


def build_news_message(item: dict) -> str:
    """بناء رسالة خبر عاجل."""
    a   = item["analysis"]
    now = datetime.now(NY_TZ).strftime("%I:%M %p ET")

    gold_label = IMPACT_LABELS.get(a["gold_impact"], a["gold_impact"])
    usd_label  = IMPACT_LABELS.get(a["usd_impact"],  a["usd_impact"])

    # تحديد إيموجي الخطورة
    if a["score"] >= 85:
        header_emoji = "🚨"
    elif a["score"] >= 70:
        header_emoji = "⚡"
    else:
        header_emoji = "📢"

    lines = [
        f"{header_emoji} *BREAKING NEWS | خبر عاجل*",
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"📡 *{item['source']}*  |  🕐 {now}",
        "",
        f"📰 {item['title']}",
        "",
        f"*{a['level']}*",
        f"Score: `{a['score']}/100`",
        "",
        f"*Expected Impact | التأثير المتوقع:*",
        f"🥇 Gold  | الذهب:   {gold_label}",
        f"💵 USD   | الدولار: {usd_label}",
        "",
        f"⚠️ *Watch XAUUSD volatility | راقب تذبذب الذهب*",
    ]

    if item.get("link"):
        lines.append(f"\n🔗 [Read more | اقرأ أكثر]({item['link']})")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("🤖 _Forex Alert Bot_")

    return "\n".join(lines)


def build_calendar_message(released: list[dict], upcoming: list[dict]) -> str:
    """بناء رسالة التقويم الاقتصادي."""
    now_ny  = datetime.now(NY_TZ)
    days_ar = ["الإثنين","الثلاثاء","الأربعاء","الخميس","الجمعة","السبت","الأحد"]
    day_ar  = days_ar[now_ny.weekday()]

    lines = [
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "📊 *FOREX HIGH IMPACT NEWS*",
        "📊 *أخبار الفوركس عالية التأثير*",
        f"🗓 {now_ny.strftime('%A, %B %d %Y')}  |  {day_ar}",
        f"🕐 {now_ny.strftime('%I:%M %p ET')}",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "",
        "✅ *RELEASED | الأحداث الصادرة*",
        "─────────────────────────",
    ]

    if released:
        for ev in released:
            s  = classify_calendar(ev)
            u  = ev.get("unit", "")
            ar = translate(ev["name"])
            lines += [
                f"📌 *{ev['name']}*",
                f"   📝 _{ar}_",
                f"   🕐 {fmtt(ev['time_utc'])}",
                f"   ┌ Actual:    `{fmtv(ev['actual'],u)}`",
                f"   ├ Forecast:  `{fmtv(ev['forecast'],u)}`",
                f"   └ Previous:  `{fmtv(ev['previous'],u)}`",
                (
                    "   💡 *USD BULLISH 🟢* | الذهب قد ينخفض 🔴" if s=="BULLISH" else
                    "   💡 *USD BEARISH 🔴* | الذهب قد يرتفع 🟢" if s=="BEARISH" else
                    "   💡 *NEUTRAL ⚪* | لا توجه واضح"
                ),
                "",
            ]
    else:
        lines += ["   _لا أحداث صادرة بعد | No released events yet_", ""]

    lines += [
        f"⏳ *UPCOMING | القادمة* _(next {UPCOMING_WINDOW_H}h)_",
        "─────────────────────────",
    ]

    if upcoming:
        now_utc = datetime.now(timezone.utc)
        for ev in upcoming:
            dm  = int((ev["time_utc"] - now_utc).total_seconds() / 60)
            eta = f"{dm}m" if dm < 60 else f"{dm//60}h {dm%60}m"
            u   = ev.get("unit","")
            lines += [
                f"⚡ *{ev['name']}*",
                f"   📝 _{translate(ev['name'])}_",
                f"   🕐 {fmtt(ev['time_utc'])}  _(in {eta})_",
                f"   Forecast: `{fmtv(ev['forecast'],u)}`  |  Prev: `{fmtv(ev['previous'],u)}`",
                "",
            ]
    else:
        lines += [f"   _لا أحداث خلال {UPCOMING_WINDOW_H} ساعات_", ""]

    lines += [
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "🤖 _Forex Alert Bot v4_",
        "📡 _Sources: ForexFactory | FMP | DailyFX_",
    ]
    return "\n".join(lines)


# ════════════════════════════════════════════════════════════════
#  MAIN ORCHESTRATION
# ════════════════════════════════════════════════════════════════

def run() -> None:
    now_utc = datetime.now(timezone.utc)
    log.info("⚡ Forex Alert Bot v4 — %s UTC", now_utc.strftime("%Y-%m-%d %H:%M"))

    # ── Validate secrets ──────────────────────────────────────
    missing = [s for s, v in [
        ("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN),
        ("CHAT_ID", CHAT_ID),
    ] if not v]
    if missing:
        log.error("❌ Secrets مفقودة: %s", ", ".join(missing))
        sys.exit(1)

    # ── Load sent hashes (deduplication) ─────────────────────
    sent_hashes = load_sent_hashes()
    log.info("🔑 Hashes محملة: %d", len(sent_hashes))

    messages_sent = 0

    # ════ PART 1: Breaking News ═══════════════════════════════
    log.info("━━━ PART 1: Breaking News ━━━")
    raw_news      = fetch_all_news()
    filtered_news = filter_news(raw_news)
    scored_news   = analyze_impact(filtered_news, sent_hashes)

    # إرسال أهم 3 أخبار فقط في كل run (لتجنب Spam)
    top_news = [n for n in scored_news if n["analysis"]["score"] >= 65][:3]

    for item in top_news:
        msg = build_news_message(item)
        if send_telegram(msg):
            messages_sent += 1
        import time; time.sleep(2)  # تأخير بسيط بين الرسائل

    # ════ PART 2: Economic Calendar ════════════════════════════
    log.info("━━━ PART 2: Economic Calendar ━━━")
    ff_events  = fetch_ff_calendar()
    fmp_events = fetch_fmp_calendar()

    # دمج وإزالة التكرار
    seen_cal = {}
    all_events = []
    for ev in ff_events + fmp_events:
        key = ev["name"][:15].lower() + ev["time_utc"].strftime("%Y%m%d%H")
        if key not in seen_cal:
            seen_cal[key] = True
            all_events.append(ev)

    cutoff   = now_utc + timedelta(hours=UPCOMING_WINDOW_H)
    released = sorted(
        [e for e in all_events if e["time_utc"] <= now_utc and e.get("actual") is not None],
        key=lambda e: e["time_utc"],
    )
    upcoming = sorted(
        [e for e in all_events if now_utc < e["time_utc"] <= cutoff],
        key=lambda e: e["time_utc"],
    )

    log.info("📅 صادر: %d | قادم: %d", len(released), len(upcoming))

    # إرسال التقويم دائماً
    cal_msg = build_calendar_message(released, upcoming)
    if send_telegram(cal_msg):
        messages_sent += 1

    # ── Save hashes ───────────────────────────────────────────
    save_sent_hashes(sent_hashes)
    log.info("✅ انتهى — %d رسائل تم إرسالها", messages_sent)


if __name__ == "__main__":
    run()
