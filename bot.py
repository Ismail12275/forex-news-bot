"""
bot.py — Forex News Alert Bot (v2 - Fixed)
Data: ForexFactory JSON API (free, no auth, no block)
"""

import os, sys, re, logging, requests
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID            = os.getenv("CHAT_ID", "")
FMP_API_KEY        = os.getenv("API_KEY", "")
UPCOMING_WINDOW_H  = 4
TARGET_CURRENCY    = "USD"
NY_TZ              = ZoneInfo("America/New_York")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, */*",
    "Referer": "https://www.forexfactory.com/",
}

def safe_float(val):
    try:
        if val in (None, "", "N/A"): return None
        return float(re.sub(r"[%KMBkb,]", "", str(val)).strip())
    except: return None

def fetch_ff_json():
    events = []
    for url in ["https://nfs.faireconomy.media/ff_calendar_thisweek.json",
                "https://nfs.faireconomy.media/ff_calendar_nextweek.json"]:
        try:
            r = requests.get(url, headers=HEADERS, timeout=20)
            r.raise_for_status()
            data = r.json()
            log.info("FF JSON: %d events from %s", len(data), url)
            for item in data:
                if (item.get("country") or "").upper() != TARGET_CURRENCY: continue
                if (item.get("impact") or "").lower() != "high": continue
                try:
                    dt = datetime.fromisoformat(item.get("date","")).astimezone(timezone.utc)
                except: continue
                events.append({
                    "name": item.get("title","Unknown"),
                    "currency": TARGET_CURRENCY,
                    "time_utc": dt,
                    "actual":   safe_float(item.get("actual")),
                    "forecast": safe_float(item.get("forecast")),
                    "previous": safe_float(item.get("previous")),
                    "unit": "",
                })
        except Exception as e:
            log.warning("FF JSON error for %s: %s", url, e)
    log.info("Total FF high-impact USD events: %d", len(events))
    return events

def fetch_fmp():
    if not FMP_API_KEY: return []
    now = datetime.now(timezone.utc)
    url = (f"https://financialmodelingprep.com/api/v3/economic_calendar"
           f"?from={now.strftime('%Y-%m-%d')}&to={(now+timedelta(days=1)).strftime('%Y-%m-%d')}&apikey={FMP_API_KEY}")
    try:
        r = requests.get(url, timeout=15); r.raise_for_status()
        data = r.json()
        events = []
        for item in (data if isinstance(data, list) else []):
            if (item.get("currency") or "").upper() != TARGET_CURRENCY: continue
            if (item.get("impact") or "").lower() != "high": continue
            try:
                raw = (item.get("date") or "").replace(" ","T")
                if not raw.endswith("Z") and "+" not in raw: raw += "+00:00"
                dt = datetime.fromisoformat(raw).astimezone(timezone.utc)
            except: continue
            events.append({"name": item.get("event","Unknown"), "currency": TARGET_CURRENCY,
                "time_utc": dt, "actual": safe_float(item.get("actual")),
                "forecast": safe_float(item.get("estimate")), "previous": safe_float(item.get("previous")), "unit": item.get("unit","")})
        log.info("FMP returned %d events", len(events)); return events
    except Exception as e:
        log.warning("FMP error: %s", e); return []

def classify(ev):
    a, f = ev.get("actual"), ev.get("forecast")
    if a is None or f is None: return "NEUTRAL"
    return "BULLISH" if a > f else ("BEARISH" if a < f else "NEUTRAL")

def fmtv(val, unit=""):
    if val is None: return "—"
    s = f"{val:,.1f}" if abs(val)>=1000 else (f"{val:.2f}" if abs(val)>=10 else f"{val:.3f}")
    return s + unit

def fmtt(dt): return dt.astimezone(NY_TZ).strftime("%I:%M %p ET")

def build_message(released, upcoming):
    now_ny = datetime.now(NY_TZ)
    lines = [
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "📊 *FOREX HIGH IMPACT NEWS*",
        f"🗓 {now_ny.strftime('%A, %B %d %Y')}",
        f"🕐 {now_ny.strftime('%I:%M %p ET')}",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━", "",
        "✅ *RELEASED EVENTS*", "─────────────────────────",
    ]
    if released:
        for ev in released:
            s = classify(ev); u = ev.get("unit","")
            lines += [
                f"📌 *{ev['name']}*",
                f"   🕐 {fmtt(ev['time_utc'])}",
                f"   Actual: `{fmtv(ev['actual'],u)}`  |  Forecast: `{fmtv(ev['forecast'],u)}`  |  Prev: `{fmtv(ev['previous'],u)}`",
                ("   💡 USD BULLISH 🟢  →  Gold may DROP ↓ 🔴" if s=="BULLISH" else
                 "   💡 USD BEARISH 🔴  →  Gold may RISE ↑ 🟢" if s=="BEARISH" else
                 "   💡 USD NEUTRAL ⚪  →  No clear bias"), "",
            ]
    else:
        lines += ["   _No USD events released yet._", ""]

    lines += [f"⏳ *UPCOMING EVENTS* _(next {UPCOMING_WINDOW_H}h)_", "─────────────────────────"]
    if upcoming:
        now_utc = datetime.now(timezone.utc)
        for ev in upcoming:
            dm = int((ev["time_utc"]-now_utc).total_seconds()/60)
            eta = f"{dm}m" if dm<60 else f"{dm//60}h {dm%60}m"
            u = ev.get("unit","")
            lines += [
                f"⚡ *{ev['name']}*",
                f"   🕐 {fmtt(ev['time_utc'])}  _(in {eta})_",
                f"   Forecast: `{fmtv(ev['forecast'],u)}`  |  Prev: `{fmtv(ev['previous'],u)}`", "",
            ]
    else:
        lines += [f"   _No high-impact USD events in the next {UPCOMING_WINDOW_H} hours._", ""]

    lines += ["━━━━━━━━━━━━━━━━━━━━━━━━━━━", "🤖 _Powered by Forex Alert Bot_"]
    return "\n".join(lines)

def send_telegram(msg):
    if not TELEGRAM_BOT_TOKEN:
        log.error("❌ TELEGRAM_BOT_TOKEN missing! Add it in: Settings → Secrets → Actions"); return False
    if not CHAT_ID:
        log.error("❌ CHAT_ID missing! Add it in: Settings → Secrets → Actions"); return False
    try:
        r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "Markdown",
                  "disable_web_page_preview": True}, timeout=15)
        data = r.json()
        if not data.get("ok"):
            log.error("Telegram error: %s", data); return False
        log.info("✅ Sent! msg_id=%s", data["result"]["message_id"]); return True
    except Exception as e:
        log.error("Telegram failed: %s", e); return False

def run():
    now_utc = datetime.now(timezone.utc)
    log.info("⚡ Bot starting — %s UTC", now_utc.strftime("%Y-%m-%d %H:%M"))

    missing = [s for s,v in [("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN), ("CHAT_ID", CHAT_ID)] if not v]
    if missing:
        log.error("❌ Missing Secrets: %s\nGo to: Settings → Secrets → Actions → New repository secret", ", ".join(missing))
        sys.exit(1)

    log.info("Fetching ForexFactory JSON …")
    events = fetch_ff_json()
    if not events:
        log.info("FF empty — trying FMP …")
        events = fetch_fmp()

    cutoff   = now_utc + timedelta(hours=UPCOMING_WINDOW_H)
    released = sorted([e for e in events if e["time_utc"]<=now_utc and e.get("actual") is not None], key=lambda e: e["time_utc"])
    upcoming = sorted([e for e in events if now_utc < e["time_utc"] <= cutoff],                      key=lambda e: e["time_utc"])

    log.info("Released: %d | Upcoming (next %dh): %d", len(released), UPCOMING_WINDOW_H, len(upcoming))

    msg = build_message(released, upcoming) if events else (
        "📊 *FOREX HIGH IMPACT NEWS*\n\nℹ️ No high-impact USD events right now.\n_Bot is running normally_ ✅"
    )
    send_telegram(msg)

if __name__ == "__main__":
    run()
