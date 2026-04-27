"""
╔══════════════════════════════════════════════════════════════════╗
║         FOREX HIGH-IMPACT NEWS ALERT BOT — bot.py               ║
║  Data: Financial Modeling Prep (primary) + ForexFactory scraper  ║
║  Delivery: Telegram Bot API via requests                         ║
╚══════════════════════════════════════════════════════════════════╝
"""

import os
import re
import sys
import logging
import requests
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo  # Python 3.9+; use pytz if on older Python

# ─────────────────────────── Logging ────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

# ─────────────────────────── Config ─────────────────────────────
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID:            str = os.getenv("CHAT_ID", "")
FMP_API_KEY:        str = os.getenv("API_KEY", "")      # Financial Modeling Prep
UPCOMING_WINDOW_H:  int = 4                              # Hours ahead to fetch upcoming events
HIGH_IMPACT_LABEL:  str = "High"                        # FMP impact label for high-impact events
TARGET_CURRENCY:    str = "USD"                         # Currency to monitor
NY_TZ              = ZoneInfo("America/New_York")       # Eastern Time (NYSE/FED timezone)


# ════════════════════════════════════════════════════════════════
#  1.  DATA LAYER — Financial Modeling Prep Economic Calendar API
# ════════════════════════════════════════════════════════════════

def fetch_calendar_fmp(date_from: str, date_to: str) -> list[dict]:
    """
    Call FMP /economic_calendar endpoint.
    Free tier: 250 requests/day, calendar data is free.
    Returns a list of event dicts (may be empty on error).
    """
    if not FMP_API_KEY:
        log.warning("API_KEY not set — skipping FMP fetch.")
        return []

    url = (
        f"https://financialmodelingprep.com/api/v3/economic_calendar"
        f"?from={date_from}&to={date_to}&apikey={FMP_API_KEY}"
    )
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict) and "Error Message" in data:
            log.error("FMP API error: %s", data["Error Message"])
            return []
        return data if isinstance(data, list) else []
    except requests.RequestException as exc:
        log.error("FMP request failed: %s", exc)
        return []


def parse_fmp_events(raw: list[dict]) -> list[dict]:
    """
    Normalise FMP events into a standard internal schema:
      {name, currency, time_utc, actual, forecast, previous, impact}
    Only keeps High-impact USD events.
    """
    events = []
    for item in raw:
        currency = (item.get("currency") or "").upper()
        impact   = (item.get("impact")   or "").strip()

        if currency != TARGET_CURRENCY:
            continue
        if impact.lower() != HIGH_IMPACT_LABEL.lower():
            continue

        # Parse the event datetime (FMP returns ISO-8601 in UTC)
        raw_date = item.get("date") or ""
        try:
            # FMP format: "2024-05-01 08:30:00" or "2024-05-01T08:30:00"
            raw_date_clean = raw_date.replace(" ", "T")
            if not raw_date_clean.endswith("Z") and "+" not in raw_date_clean:
                raw_date_clean += "+00:00"
            dt_utc = datetime.fromisoformat(raw_date_clean).replace(tzinfo=timezone.utc)
        except ValueError:
            log.debug("Could not parse date: %s", raw_date)
            continue

        def safe_float(val) -> float | None:
            try:
                return float(val) if val not in (None, "", "N/A") else None
            except (TypeError, ValueError):
                return None

        events.append({
            "name":     item.get("event", "Unknown Event"),
            "currency": currency,
            "time_utc": dt_utc,
            "actual":   safe_float(item.get("actual")),
            "forecast": safe_float(item.get("estimate")),
            "previous": safe_float(item.get("previous")),
            "impact":   impact,
            "unit":     item.get("unit", ""),
        })

    return events


# ════════════════════════════════════════════════════════════════
#  2.  FALLBACK — ForexFactory scraper (no API key required)
# ════════════════════════════════════════════════════════════════

_FF_IMPACT_MAP = {"red": "High", "ora": "Medium", "yel": "Low", "gra": "Holiday"}


def fetch_calendar_forexfactory() -> list[dict]:
    """
    Scrape ForexFactory's weekly calendar page.
    Used when FMP key is unavailable or returns no data.
    NOTE: scraping is fragile — ForexFactory may block or change markup.
          Use FMP as primary whenever possible.
    """
    import html
    from html.parser import HTMLParser

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }
    url = "https://www.forexfactory.com/calendar"
    try:
        resp = requests.get(url, headers=headers, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as exc:
        log.error("ForexFactory scrape failed: %s", exc)
        return []

    # ── Minimal table parser ──────────────────────────────────────
    class FFParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.events   = []
            self._in_row  = False
            self._cells   = []
            self._cur_td  = None
            self._impact  = None
            self._cur_date = None
            self._depth   = 0

        def handle_starttag(self, tag, attrs):
            attrs_d = dict(attrs)
            cls = attrs_d.get("class", "")
            if tag == "tr" and "calendar__row" in cls:
                self._in_row = True
                self._cells  = []
                # Extract impact from <tr> class
                for key, colour in _FF_IMPACT_MAP.items():
                    if f"impact-{key}" in cls:
                        self._impact = colour
                        break
            if self._in_row and tag == "td":
                self._cur_td = attrs_d.get("class", "")
                self._depth  = 0

        def handle_endtag(self, tag):
            if tag == "tr" and self._in_row:
                self._in_row = False
                if len(self._cells) >= 7 and self._impact == "High":
                    self.events.append(self._cells[:])
                self._cells = []
            if self._in_row and tag == "td":
                self._cur_td = None

        def handle_data(self, data):
            if self._in_row and self._cur_td is not None:
                text = data.strip()
                if text:
                    self._cells.append(html.unescape(text))
                else:
                    self._cells.append("")

    parser = FFParser()
    parser.feed(resp.text)

    # ForexFactory columns: date, time, currency, impact, event, actual, forecast, previous
    events = []
    now_utc = datetime.now(timezone.utc)
    for row in parser.events:
        try:
            currency = row[2].upper() if len(row) > 2 else ""
            if currency != TARGET_CURRENCY:
                continue
            events.append({
                "name":     row[4] if len(row) > 4 else "Unknown",
                "currency": currency,
                "time_utc": now_utc,   # Time parsing from FF is complex; simplified here
                "actual":   _try_float(row[5] if len(row) > 5 else None),
                "forecast": _try_float(row[6] if len(row) > 6 else None),
                "previous": _try_float(row[7] if len(row) > 7 else None),
                "impact":   "High",
                "unit":     "",
            })
        except IndexError:
            continue
    return events


def _try_float(val) -> float | None:
    if val is None:
        return None
    cleaned = re.sub(r"[%KMBkb,]", "", str(val)).strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


# ════════════════════════════════════════════════════════════════
#  3.  BUSINESS LOGIC — Classify events & build message
# ════════════════════════════════════════════════════════════════

def classify_event(event: dict) -> str:
    """
    Returns sentiment string based on Actual vs Forecast.
    Rules apply only to USD events.
    """
    actual   = event.get("actual")
    forecast = event.get("forecast")
    if actual is None or forecast is None:
        return "NEUTRAL ⚪"
    if actual > forecast:
        return "BULLISH"
    if actual < forecast:
        return "BEARISH"
    return "NEUTRAL ⚪"


def fmt_value(val: float | None, unit: str = "") -> str:
    """Format numeric values; return '—' if None."""
    if val is None:
        return "—"
    # Smart rounding: keep meaningful digits
    if abs(val) >= 1000:
        formatted = f"{val:,.1f}"
    elif abs(val) >= 10:
        formatted = f"{val:.2f}"
    else:
        formatted = f"{val:.3f}"
    return f"{formatted}{unit}"


def fmt_time(dt_utc: datetime, tz=NY_TZ) -> str:
    """Convert UTC datetime to New-York time string for display."""
    return dt_utc.astimezone(tz).strftime("%I:%M %p ET")


def build_telegram_message(released: list[dict], upcoming: list[dict]) -> str:
    """
    Assemble the full Telegram message with emojis and sections.
    Uses MarkdownV2 escaping rules.
    """
    now_ny   = datetime.now(NY_TZ)
    date_str = now_ny.strftime("%A, %B %d %Y")
    time_str = now_ny.strftime("%I:%M %p ET")

    lines = [
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"📊 *FOREX HIGH IMPACT NEWS*",
        f"🗓 {date_str}  |  🕐 {time_str}",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "",
    ]

    # ── RELEASED EVENTS section ───────────────────────────────────
    if released:
        lines.append("✅ *RELEASED EVENTS*")
        lines.append("─────────────────────────")
        for ev in released:
            sentiment = classify_event(ev)
            unit      = ev.get("unit", "")
            actual_s  = fmt_value(ev["actual"],   unit)
            forecast_s= fmt_value(ev["forecast"], unit)
            previous_s= fmt_value(ev["previous"], unit)

            lines.append(f"📌 *{ev['name']}*")
            lines.append(f"   🕐 {fmt_time(ev['time_utc'])}")
            lines.append(
                f"   Actual: `{actual_s}`  |  "
                f"Forecast: `{forecast_s}`  |  "
                f"Prev: `{previous_s}`"
            )

            if sentiment == "BULLISH":
                lines.append(f"   💡 USD BULLISH 🟢  →  Gold may DROP ↓ 🔴")
            elif sentiment == "BEARISH":
                lines.append(f"   💡 USD BEARISH 🔴  →  Gold may RISE ↑ 🟢")
            else:
                lines.append(f"   💡 USD NEUTRAL ⚪  →  No clear bias")

            lines.append("")
    else:
        lines.append("✅ *RELEASED EVENTS*")
        lines.append("   _No high-impact USD events released yet today._")
        lines.append("")

    # ── UPCOMING EVENTS section ───────────────────────────────────
    lines.append(f"⏳ *UPCOMING EVENTS* _(next {UPCOMING_WINDOW_H}h)_")
    lines.append("─────────────────────────")
    if upcoming:
        for ev in upcoming:
            unit       = ev.get("unit", "")
            forecast_s = fmt_value(ev["forecast"], unit)
            previous_s = fmt_value(ev["previous"], unit)
            delta_m    = int(
                (ev["time_utc"] - datetime.now(timezone.utc)).total_seconds() / 60
            )
            eta = f"in {delta_m}m" if delta_m < 60 else f"in {delta_m // 60}h {delta_m % 60}m"

            lines.append(f"⚡ *{ev['name']}*")
            lines.append(f"   🕐 {fmt_time(ev['time_utc'])}  ({eta})")
            lines.append(
                f"   Forecast: `{forecast_s}`  |  Prev: `{previous_s}`"
            )
            lines.append("")
    else:
        lines.append(
            f"   _No high-impact USD events in the next {UPCOMING_WINDOW_H} hours._"
        )
        lines.append("")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("🤖 _Powered by Forex Alert Bot_")

    return "\n".join(lines)


# ════════════════════════════════════════════════════════════════
#  4.  TELEGRAM DELIVERY
# ════════════════════════════════════════════════════════════════

def send_telegram(message: str) -> bool:
    """
    POST the message to the Telegram Bot API.
    Uses parse_mode=Markdown (v1) for simpler escaping in the message builder.
    """
    if not TELEGRAM_BOT_TOKEN or not CHAT_ID:
        log.error("TELEGRAM_BOT_TOKEN or CHAT_ID is not set.")
        return False

    url     = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id":    CHAT_ID,
        "text":       message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    try:
        resp = requests.post(url, json=payload, timeout=15)
        data = resp.json()
        if not data.get("ok"):
            log.error("Telegram API error: %s", data)
            return False
        log.info("✅ Message sent to Telegram (msg_id=%s)", data["result"]["message_id"])
        return True
    except requests.RequestException as exc:
        log.error("Telegram request failed: %s", exc)
        return False


# ════════════════════════════════════════════════════════════════
#  5.  ORCHESTRATION
# ════════════════════════════════════════════════════════════════

def run() -> None:
    now_utc   = datetime.now(timezone.utc)
    today_str = now_utc.strftime("%Y-%m-%d")
    tomorrow  = (now_utc + timedelta(days=1)).strftime("%Y-%m-%d")

    log.info("⚡ Forex Alert Bot starting — %s UTC", now_utc.strftime("%Y-%m-%d %H:%M"))

    # ── Fetch events ──────────────────────────────────────────────
    raw_events: list[dict] = []

    if FMP_API_KEY:
        log.info("Fetching from Financial Modeling Prep …")
        raw = fetch_calendar_fmp(today_str, tomorrow)
        raw_events = parse_fmp_events(raw)
        log.info("FMP returned %d high-impact USD events", len(raw_events))
    
    if not raw_events:
        log.info("FMP yielded nothing — falling back to ForexFactory scraper …")
        raw_events = fetch_calendar_forexfactory()
        log.info("ForexFactory returned %d high-impact USD events", len(raw_events))

    if not raw_events:
        log.warning("No events found from any source. Sending a status ping.")
        send_telegram(
            "📊 *FOREX HIGH IMPACT NEWS*\n\n"
            "ℹ️ No high-impact USD events found at this time.\n"
            "_Bot is running normally._"
        )
        return

    # ── Split into released vs upcoming ───────────────────────────
    cutoff_upcoming = now_utc + timedelta(hours=UPCOMING_WINDOW_H)

    released = [
        ev for ev in raw_events
        if ev["time_utc"] <= now_utc and ev.get("actual") is not None
    ]
    upcoming = [
        ev for ev in raw_events
        if now_utc < ev["time_utc"] <= cutoff_upcoming
    ]

    # Sort chronologically
    released.sort(key=lambda e: e["time_utc"])
    upcoming.sort(key=lambda e: e["time_utc"])

    log.info(
        "Events → Released: %d  |  Upcoming (next %dh): %d",
        len(released), UPCOMING_WINDOW_H, len(upcoming),
    )

    # ── Build & send message ──────────────────────────────────────
    message = build_telegram_message(released, upcoming)
    log.info("Sending Telegram message …\n%s", message)
    send_telegram(message)


# ════════════════════════════════════════════════════════════════
#  Entry point
# ════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    run()
