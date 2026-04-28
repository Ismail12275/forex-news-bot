"""
╔══════════════════════════════════════════════════════════════════╗
║     FOREX HIGH-IMPACT NEWS ALERT BOT v3 — bot.py                ║
║  مصادر: ForexFactory JSON + FMP + DailyFX                       ║
║  لغة: عربي + إنجليزي                                            ║
╚══════════════════════════════════════════════════════════════════╝
"""

import os, sys, re, logging, requests
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
UPCOMING_WINDOW_H  = 4
TARGET_CURRENCY    = "USD"
NY_TZ              = ZoneInfo("America/New_York")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, */*",
    "Referer": "https://www.forexfactory.com/",
}

# ════════════════════════════════════════════════════════════════
#  ترجمة أسماء الأحداث الشائعة | Event Name Translations
# ════════════════════════════════════════════════════════════════

TRANSLATIONS = {
    "Non-Farm Employment Change":    "التغير في الوظائف غير الزراعية",
    "Non-Farm Payrolls":             "الرواتب غير الزراعية",
    "Unemployment Rate":             "معدل البطالة",
    "CPI m/m":                       "مؤشر أسعار المستهلك (شهري)",
    "Core CPI m/m":                  "مؤشر أسعار المستهلك الأساسي (شهري)",
    "GDP q/q":                       "الناتج المحلي الإجمالي (ربع سنوي)",
    "Federal Funds Rate":            "سعر الفائدة الفيدرالي",
    "FOMC Statement":                "بيان لجنة السوق الفيدرالية",
    "FOMC Press Conference":         "مؤتمر الاحتياطي الفيدرالي الصحفي",
    "ISM Manufacturing PMI":         "مؤشر PMI التصنيعي",
    "ISM Services PMI":              "مؤشر PMI للخدمات",
    "Retail Sales m/m":              "مبيعات التجزئة (شهري)",
    "Core Retail Sales m/m":         "مبيعات التجزئة الأساسية (شهري)",
    "PPI m/m":                       "مؤشر أسعار المنتجين (شهري)",
    "Core PPI m/m":                  "مؤشر أسعار المنتجين الأساسي",
    "Trade Balance":                 "الميزان التجاري",
    "Consumer Confidence":           "ثقة المستهلك",
    "Building Permits":              "تصاريح البناء",
    "Housing Starts":                "مبدأ البناء",
    "Existing Home Sales":           "مبيعات المنازل القائمة",
    "New Home Sales":                "مبيعات المنازل الجديدة",
    "Durable Goods Orders m/m":      "طلبيات السلع المعمرة (شهري)",
    "ADP Non-Farm Employment Change":"تقرير ADP للوظائف غير الزراعية",
    "Initial Jobless Claims":        "طلبات إعانة البطالة الأولية",
    "JOLTs Job Openings":            "فرص العمل JOLTs",
    "PCE Price Index m/m":           "مؤشر أسعار PCE (شهري)",
    "Core PCE Price Index m/m":      "مؤشر PCE الأساسي (شهري)",
}

def translate(name: str) -> str:
    """Returns Arabic translation if available, else original name."""
    for en, ar in TRANSLATIONS.items():
        if en.lower() in name.lower():
            return ar
    return name


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

def fmtt_ar(dt: datetime) -> str:
    """وقت بتوقيت نيويورك بالعربي"""
    return dt.astimezone(NY_TZ).strftime("%H:%M بتوقيت نيويورك")


# ════════════════════════════════════════════════════════════════
#  1. المصدر الأول: ForexFactory JSON API
# ════════════════════════════════════════════════════════════════

def fetch_ff_json() -> list[dict]:
    """
    ForexFactory المصدر الرسمي - مجاني بدون مفتاح.
    يجلب بيانات هذا الأسبوع والأسبوع القادم.
    """
    events = []
    urls = [
        "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
        "https://nfs.faireconomy.media/ff_calendar_nextweek.json",
    ]
    for url in urls:
        try:
            r = requests.get(url, headers=HEADERS, timeout=20)
            r.raise_for_status()
            data = r.json()
            log.info("✅ ForexFactory: %d أحداث من %s", len(data), url.split("/")[-1])

            for item in data:
                if (item.get("country") or "").upper() != TARGET_CURRENCY: continue
                if (item.get("impact") or "").lower() != "high": continue
                try:
                    dt = datetime.fromisoformat(item["date"]).astimezone(timezone.utc)
                except:
                    continue
                events.append({
                    "name":     item.get("title", "Unknown"),
                    "currency": TARGET_CURRENCY,
                    "time_utc": dt,
                    "actual":   safe_float(item.get("actual")),
                    "forecast": safe_float(item.get("forecast")),
                    "previous": safe_float(item.get("previous")),
                    "unit":     "",
                    "source":   "ForexFactory",
                })
        except Exception as e:
            log.warning("⚠️ ForexFactory error [%s]: %s", url.split("/")[-1], e)

    log.info("📊 ForexFactory: %d أحداث USD عالية التأثير", len(events))
    return events


# ════════════════════════════════════════════════════════════════
#  2. المصدر الثاني: Financial Modeling Prep (يحتاج API_KEY)
# ════════════════════════════════════════════════════════════════

def fetch_fmp() -> list[dict]:
    """
    Financial Modeling Prep - مجاني 250 طلب/يوم.
    يتطلب API_KEY في Secrets.
    """
    if not FMP_API_KEY:
        log.info("ℹ️ FMP: لا يوجد API_KEY - تخطي هذا المصدر")
        return []

    now      = datetime.now(timezone.utc)
    date_to  = (now + timedelta(days=1)).strftime("%Y-%m-%d")
    url = (
        f"https://financialmodelingprep.com/api/v3/economic_calendar"
        f"?from={now.strftime('%Y-%m-%d')}&to={date_to}&apikey={FMP_API_KEY}"
    )
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        data = r.json()
        events = []
        for item in (data if isinstance(data, list) else []):
            if (item.get("currency") or "").upper() != TARGET_CURRENCY: continue
            if (item.get("impact") or "").lower() != "high": continue
            try:
                raw = (item.get("date") or "").replace(" ", "T")
                if not raw.endswith("Z") and "+" not in raw: raw += "+00:00"
                dt = datetime.fromisoformat(raw).astimezone(timezone.utc)
            except:
                continue
            events.append({
                "name":     item.get("event", "Unknown"),
                "currency": TARGET_CURRENCY,
                "time_utc": dt,
                "actual":   safe_float(item.get("actual")),
                "forecast": safe_float(item.get("estimate")),
                "previous": safe_float(item.get("previous")),
                "unit":     item.get("unit", ""),
                "source":   "FMP",
            })
        log.info("✅ FMP: %d أحداث USD عالية التأثير", len(events))
        return events
    except Exception as e:
        log.warning("⚠️ FMP error: %s", e)
        return []


# ════════════════════════════════════════════════════════════════
#  3. المصدر الثالث: DailyFX Economic Calendar (Scraping)
# ════════════════════════════════════════════════════════════════

def fetch_dailyfx() -> list[dict]:
    """
    DailyFX - مصدر بديل مجاني بدون مفتاح.
    يُستخدم كمصدر احتياطي إضافي.
    """
    url = "https://www.dailyfx.com/economic-calendar/api/events"
    params = {
        "timezone": "0",
        "currencies": "USD",
    }
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=20)
        r.raise_for_status()
        data = r.json()

        items = data if isinstance(data, list) else data.get("data", data.get("events", []))
        events = []

        for item in items:
            currency = (item.get("currency") or item.get("unit") or "").upper()
            if TARGET_CURRENCY not in currency: continue

            impact = (item.get("impact") or item.get("importance") or "").lower()
            if "high" not in impact and "3" not in impact: continue

            raw_date = item.get("date") or item.get("datetime") or item.get("time") or ""
            try:
                raw_date = raw_date.replace(" ", "T")
                if not raw_date.endswith("Z") and "+" not in raw_date: raw_date += "+00:00"
                dt = datetime.fromisoformat(raw_date).astimezone(timezone.utc)
            except:
                continue

            events.append({
                "name":     item.get("event") or item.get("title") or item.get("name") or "Unknown",
                "currency": TARGET_CURRENCY,
                "time_utc": dt,
                "actual":   safe_float(item.get("actual")),
                "forecast": safe_float(item.get("forecast") or item.get("consensus")),
                "previous": safe_float(item.get("previous") or item.get("revised")),
                "unit":     "",
                "source":   "DailyFX",
            })
        log.info("✅ DailyFX: %d أحداث USD عالية التأثير", len(events))
        return events
    except Exception as e:
        log.warning("⚠️ DailyFX error: %s", e)
        return []


# ════════════════════════════════════════════════════════════════
#  دمج وإزالة التكرار من المصادر المتعددة
# ════════════════════════════════════════════════════════════════

def merge_events(sources: list[list[dict]]) -> list[dict]:
    """
    يدمج الأحداث من مصادر متعددة ويُزيل التكرار.
    يعتمد على (اسم الحدث + وقته) كمعرّف فريد.
    يُفضّل الحدث الذي يحتوي على actual إذا وُجد تكرار.
    """
    seen   = {}
    merged = []

    for source_events in sources:
        for ev in source_events:
            # مفتاح فريد: أول 20 حرف من الاسم + التاريخ + الساعة
            key = (
                ev["name"][:20].lower().strip() + "_" +
                ev["time_utc"].strftime("%Y-%m-%d-%H")
            )
            if key not in seen:
                seen[key] = len(merged)
                merged.append(ev)
            else:
                # استبدل إذا كانت النسخة الجديدة تحتوي على actual
                existing = merged[seen[key]]
                if ev.get("actual") is not None and existing.get("actual") is None:
                    merged[seen[key]] = ev

    log.info("📦 إجمالي الأحداث بعد الدمج: %d", len(merged))
    return merged


# ════════════════════════════════════════════════════════════════
#  منطق التحليل | Analysis Logic
# ════════════════════════════════════════════════════════════════

def classify(ev: dict) -> str:
    a, f = ev.get("actual"), ev.get("forecast")
    if a is None or f is None: return "NEUTRAL"
    return "BULLISH" if a > f else ("BEARISH" if a < f else "NEUTRAL")


# ════════════════════════════════════════════════════════════════
#  بناء الرسالة الثنائية اللغة | Bilingual Message Builder
# ════════════════════════════════════════════════════════════════

def build_message(released: list[dict], upcoming: list[dict]) -> str:
    now_ny   = datetime.now(NY_TZ)
    date_en  = now_ny.strftime("%A, %B %d %Y")
    time_str = now_ny.strftime("%I:%M %p ET")

    # تحديد يوم الأسبوع بالعربي
    days_ar = ["الإثنين","الثلاثاء","الأربعاء","الخميس","الجمعة","السبت","الأحد"]
    day_ar  = days_ar[now_ny.weekday()]
    date_ar = now_ny.strftime(f"{day_ar} %d/%m/%Y")

    lines = [
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "📊 *FOREX HIGH IMPACT NEWS*",
        "📊 *أخبار الفوركس عالية التأثير*",
        "",
        f"🗓 {date_en}  |  {date_ar}",
        f"🕐 {time_str}",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "",
    ]

    # ── الأحداث المُصدرة | Released Events ───────────────────────
    lines += [
        "✅ *RELEASED EVENTS | الأحداث الصادرة*",
        "─────────────────────────",
    ]

    if released:
        for ev in released:
            s  = classify(ev)
            u  = ev.get("unit", "")
            ar = translate(ev["name"])

            lines += [
                f"📌 *{ev['name']}*",
                f"   📝 _{ar}_",
                f"   🕐 {fmtt(ev['time_utc'])}",
                f"   ┌ Actual | الفعلي:    `{fmtv(ev['actual'], u)}`",
                f"   ├ Forecast | التوقع:  `{fmtv(ev['forecast'], u)}`",
                f"   └ Previous | السابق:  `{fmtv(ev['previous'], u)}`",
            ]

            if s == "BULLISH":
                lines += [
                    "   💡 *USD BULLISH 🟢* | *الدولار صاعد*",
                    "   🔴 *Gold may DROP ↓* | *الذهب قد ينخفض*",
                ]
            elif s == "BEARISH":
                lines += [
                    "   💡 *USD BEARISH 🔴* | *الدولار هابط*",
                    "   🟢 *Gold may RISE ↑* | *الذهب قد يرتفع*",
                ]
            else:
                lines += [
                    "   💡 *NEUTRAL ⚪* | *محايد — لا توجه واضح*",
                ]

            src = ev.get("source", "")
            if src:
                lines.append(f"   📡 _Source: {src}_")
            lines.append("")
    else:
        lines += ["   _No released USD events yet | لا توجد أحداث صادرة بعد_", ""]

    # ── الأحداث القادمة | Upcoming Events ────────────────────────
    lines += [
        f"⏳ *UPCOMING EVENTS | الأحداث القادمة* _(next {UPCOMING_WINDOW_H}h)_",
        "─────────────────────────",
    ]

    if upcoming:
        now_utc = datetime.now(timezone.utc)
        for ev in upcoming:
            dm  = int((ev["time_utc"] - now_utc).total_seconds() / 60)
            eta = f"{dm}m" if dm < 60 else f"{dm//60}h {dm%60}m"
            u   = ev.get("unit", "")
            ar  = translate(ev["name"])

            lines += [
                f"⚡ *{ev['name']}*",
                f"   📝 _{ar}_",
                f"   🕐 {fmtt(ev['time_utc'])}  _(in {eta} | خلال {eta})_",
                f"   ├ Forecast | التوقع:  `{fmtv(ev['forecast'], u)}`",
                f"   └ Previous | السابق:  `{fmtv(ev['previous'], u)}`",
                "",
            ]
    else:
        lines += [
            f"   _No upcoming events in {UPCOMING_WINDOW_H}h | لا أحداث خلال {UPCOMING_WINDOW_H} ساعات_",
            "",
        ]

    lines += [
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "🤖 _Powered by Forex Alert Bot | بوت تنبيهات الفوركس_",
        "📡 _Sources: ForexFactory | FMP | DailyFX_",
    ]

    return "\n".join(lines)


# ════════════════════════════════════════════════════════════════
#  إرسال تيليغرام | Telegram Sender
# ════════════════════════════════════════════════════════════════

def send_telegram(msg: str) -> bool:
    if not TELEGRAM_BOT_TOKEN:
        log.error("❌ TELEGRAM_BOT_TOKEN مفقود! أضفه في: Settings → Secrets → Actions")
        return False
    if not CHAT_ID:
        log.error("❌ CHAT_ID مفقود! أضفه في: Settings → Secrets → Actions")
        return False

    url     = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id":                  CHAT_ID,
        "text":                     msg,
        "parse_mode":               "Markdown",
        "disable_web_page_preview": True,
    }
    try:
        r    = requests.post(url, json=payload, timeout=15)
        data = r.json()
        if not data.get("ok"):
            log.error("❌ Telegram API error: %s", data)
            return False
        log.info("✅ تم الإرسال! msg_id=%s", data["result"]["message_id"])
        return True
    except Exception as e:
        log.error("❌ Telegram failed: %s", e)
        return False


# ════════════════════════════════════════════════════════════════
#  الدالة الرئيسية | Main
# ════════════════════════════════════════════════════════════════

def run() -> None:
    now_utc = datetime.now(timezone.utc)
    log.info("⚡ Bot v3 بدء التشغيل — %s UTC", now_utc.strftime("%Y-%m-%d %H:%M"))

    # ── التحقق من الـ Secrets ──────────────────────────────────
    missing = [s for s, v in [
        ("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN),
        ("CHAT_ID", CHAT_ID),
    ] if not v]
    if missing:
        log.error("❌ Secrets مفقودة: %s", ", ".join(missing))
        sys.exit(1)

    # ── جلب البيانات من المصادر الثلاثة ──────────────────────
    log.info("🔍 جلب البيانات من المصادر...")

    ff_events     = fetch_ff_json()
    fmp_events    = fetch_fmp()
    dailyfx_events = fetch_dailyfx()

    # ── دمج الأحداث وإزالة التكرار ──────────────────────────
    all_events = merge_events([ff_events, fmp_events, dailyfx_events])

    # ── تصنيف الأحداث ────────────────────────────────────────
    cutoff   = now_utc + timedelta(hours=UPCOMING_WINDOW_H)

    released = sorted(
        [e for e in all_events if e["time_utc"] <= now_utc and e.get("actual") is not None],
        key=lambda e: e["time_utc"],
    )
    upcoming = sorted(
        [e for e in all_events if now_utc < e["time_utc"] <= cutoff],
        key=lambda e: e["time_utc"],
    )

    log.info("📊 صادر: %d | قادم (خلال %dh): %d",
             len(released), UPCOMING_WINDOW_H, len(upcoming))

    # ── بناء وإرسال الرسالة ──────────────────────────────────
    if not all_events:
        msg = (
            "📊 *FOREX HIGH IMPACT NEWS | أخبار الفوركس*\n\n"
            "ℹ️ لا توجد أحداث USD عالية التأثير الآن.\n"
            "No high-impact USD events found right now.\n\n"
            "_✅ البوت يعمل بشكل طبيعي | Bot is running normally_"
        )
    else:
        msg = build_message(released, upcoming)

    send_telegram(msg)


if __name__ == "__main__":
    run()
