import pytz

# ─────────────────────────── CONFIGURATION ───────────────────────────
TIMEZONE = pytz.timezone("America/New_York")
MIN_IMPACT_SCORE = 75  # Minimum AI score to trigger an alert (0-100)
MAX_ALERTS_PER_HOUR = 5

# Market Data symbols for context confirmation (yfinance)
MARKET_SYMBOLS = {
    "DXY": "DX-Y.NYB",
    "US10Y": "^TNX",
    "GOLD": "GC=F"
}

# Free RSS Feeds
RSS_FEEDS = {
    "Investing_Gold": "https://www.investing.com/rss/news_11.rss",
    "MarketWatch": "https://www.marketwatch.com/rss/topstories",
    "Yahoo_Finance": "https://finance.yahoo.com/news/rssindex",
    "ForexLive": "https://www.forexlive.com/feed/news"
}

# Hybrid Logic Keywords (Pre-filtering to save AI API calls)
HIGH_IMPACT_KEYWORDS = [
    "gold", "xau", "usd", "fed", "powell", "inflation", "cpi", "pce", 
    "nfp", "war", "missile", "strike", "ceasefire", "geopolitical", "rate cut", "rate hike"
]

IGNORE_KEYWORDS = [
    "crypto", "bitcoin", "earnings", "netflix", "apple", "tesla", "sport", "summary", "weekly recap"
]
