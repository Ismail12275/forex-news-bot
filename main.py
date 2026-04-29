import os
import time
import json
import sqlite3
import hashlib
import logging
from datetime import datetime
import requests
import feedparser
import yfinance as yf
import schedule
from dotenv import load_dotenv
import config

# ─────────────────────────── INITIALIZATION ───────────────────────────
load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# Initialize SQLite for Deduplication
conn = sqlite3.connect("news_cache.db", check_same_thread=False)
cursor = conn.cursor()
cursor.execute("""
    CREATE TABLE IF NOT EXISTS sent_news (
        hash TEXT PRIMARY KEY,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
    )
""")
conn.commit()

# ─────────────────────────── MARKET CONTEXT ───────────────────────────
def get_market_context():
    """Fetches live market data to confirm AI reasoning."""
    context = {}
    try:
        for name, ticker in config.MARKET_SYMBOLS.items():
            data = yf.Ticker(ticker).history(period="5d")
            if len(data) >= 2:
                prev_close = data['Close'].iloc[-2]
                current = data['Close'].iloc[-1]
                change = ((current - prev_close) / prev_close) * 100
                trend = "Up" if change > 0 else "Down"
                context[name] = f"{trend} ({change:.2f}%)"
            else:
                context[name] = "Unknown"
        return context
    except Exception as e:
        logging.warning(f"Market context error: {e}")
        return {"DXY": "Unknown", "US10Y": "Unknown", "GOLD": "Unknown"}

# ─────────────────────────── HYBRID FILTERING ───────────────────────────
def is_relevant(title):
    """Rule-based filter: Blocks noise, allows high-probability news."""
    title_lower = title.lower()
    
    for word in config.IGNORE_KEYWORDS:
        if word in title_lower:
            return False
            
    for word in config.HIGH_IMPACT_KEYWORDS:
        if word in title_lower:
            return True
            
    return False # Ignore if no high impact keywords are found

def is_duplicate(text):
    """Checks SQLite DB to prevent duplicate alerts."""
    h = hashlib.md5(text.encode('utf-8')).hexdigest()
    cursor.execute("SELECT hash FROM sent_news WHERE hash = ?", (h,))
    if cursor.fetchone():
        return True
    return False

def mark_as_sent(text):
    h = hashlib.md5(text.encode('utf-8')).hexdigest()
    cursor.execute("INSERT INTO sent_news (hash) VALUES (?)", (h,))
    conn.commit()

# ─────────────────────────── AI ANALYSIS (GROQ) ───────────────────────────
def analyze_news(title, source, market_context):
    """Uses Groq's free Llama-3 API for intelligent, context-aware analysis."""
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    
    prompt = f"""
    You are an expert Forex & Commodities analyst. Analyze the following news headline.
    Headline: "{title}"
    Source: {source}
    Current Market Context: DXY is {market_context['DXY']}, US10Y is {market_context['US10Y']}.
    
    Determine the impact on XAUUSD (Gold) and USD. 
    Respond STRICTLY in JSON format with no markdown formatting or extra text.
    Format:
    {{
        "category": "Macroeconomics / Geopolitical / Central Bank etc.",
        "score": 0-100 (100 being massive market moving news),
        "gold_impact": "Bullish", "Bearish", or "Mixed",
        "usd_impact": "Bullish", "Bearish", or "Mixed",
        "reason": "1-2 short sentences explaining why based on market context.",
        "confidence": "High", "Medium", or "Low",
        "volatility_prob": "XX%"
    }}
    """
    
    payload = {
        "model": "llama3-8b-8192", # Extremely fast, free model
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1
    }
    
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        response.raise_for_status()
        content = response.json()['choices'][0]['message']['content']
        # Clean potential markdown from AI output
        content = content.strip().strip("`").removeprefix("json")
        return json.loads(content)
    except Exception as e:
        logging.warning(f"AI Analysis Failed: {e}")
        return None

# ─────────────────────────── TELEGRAM ───────────────────────────
def escape_html(text):
    """Prevents Telegram API parsing errors."""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def send_telegram(analysis, original_title, source, link):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    
    impact_emoji = "🔥" if analysis['score'] >= 85 else "⚡"
    gold_emoji = "🟢" if "Bullish" in analysis['gold_impact'] else "🔴" if "Bearish" in analysis['gold_impact'] else "🟡"
    usd_emoji = "🟢" if "Bullish" in analysis['usd_impact'] else "🔴" if "Bearish" in analysis['usd_impact'] else "🟡"
    
    msg = (
        f"{impact_emoji} <b>BREAKING NEWS</b>\n"
        f"<b>Source:</b> {escape_html(source)} | <b>Category:</b> {escape_html(analysis['category'])}\n\n"
        f"🚨 <b>Headline:</b> <i>{escape_html(original_title)}</i>\n\n"
        f"📊 <b>Impact Score:</b> {analysis['score']}/100\n"
        f"🟡 <b>Gold (XAUUSD):</b> {gold_emoji} {analysis['gold_impact']}\n"
        f"💵 <b>USD:</b> {usd_emoji} {analysis['usd_impact']}\n\n"
        f"🧠 <b>Reasoning:</b> {escape_html(analysis['reason'])}\n\n"
        f"🎯 <b>Confidence:</b> {analysis['confidence']} | <b>Volatility:</b> {analysis['volatility_prob']}\n"
        f"🔗 <a href='{link}'>Read Full Article</a>"
    )
    
    payload = {"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"}
    
    # Retry mechanism for Telegram
    for _ in range(3):
        try:
            r = requests.post(url, json=payload, timeout=10)
            if r.status_code == 200:
                return True
            elif r.status_code == 429:
                time.sleep(5) # Rate limit hit
        except requests.exceptions.RequestException:
            time.sleep(2)
    return False

# ─────────────────────────── MAIN LOOP ───────────────────────────
def job():
    logging.info("Starting news cycle...")
    
    # Check session mode (Fast/Quiet)
    current_hour = datetime.now(config.TIMEZONE).hour
    if 17 <= current_hour <= 23:
        logging.info("Dead hours (Asian open). Minimal volatility expected.")
        # Could adjust threshold here dynamically if desired.
    
    market_context = get_market_context()
    alerts_sent_this_cycle = 0
    
    for source_name, url in config.RSS_FEEDS.items():
        if alerts_sent_this_cycle >= config.MAX_ALERTS_PER_HOUR:
            break
            
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:5]: # Check top 5 recent news
                title = entry.title
                link = entry.link
                
                if not is_relevant(title):
                    continue
                    
                if is_duplicate(title):
                    continue
                
                # Hybrid verification passed, send to AI
                logging.info(f"Analyzing: {title}")
                analysis = analyze_news(title, source_name, market_context)
                
                if analysis and analysis.get('score', 0) >= config.MIN_IMPACT_SCORE:
                    if send_telegram(analysis, title, source_name, link):
                        mark_as_sent(title)
                        alerts_sent_this_cycle += 1
                        logging.info(f"Alert sent! Score: {analysis['score']}")
                        time.sleep(3) # Anti-spam delay
                else:
                    mark_as_sent(title) # Mark low-impact as processed so we don't re-analyze
                    
        except Exception as e:
            logging.warning(f"Feed error [{source_name}]: {e}")

if __name__ == "__main__":
    logging.info("Bot started successfully.")
    job() # Run immediately once
    
    # Schedule to run every 15 minutes
    schedule.every(15).minutes.do(job)
    
    while True:
        schedule.run_pending()
        time.sleep(1)
