#!/usr/bin/env python3
import os, json, time, smtplib, datetime, urllib.request, urllib.error
import xml.etree.ElementTree as ET
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

GEMINI_API_KEY   = os.environ["GEMINI_API_KEY"]
DEEPSEEK_API_KEY = os.environ["DEEPSEEK_API_KEY"]
GMAIL_ADDRESS    = os.environ["GMAIL_ADDRESS"]
GMAIL_APP_PASS   = os.environ["GMAIL_APP_PASS"]
TO_EMAIL         = os.environ.get("TO_EMAIL", GMAIL_ADDRESS)
CS153_CHANNEL_ID = "UC0YBJCRIt4kA2jZ7siTGMyQ"
SKIP_KEYWORDS    = ["office hours", "q&a", "panel", "discussion"]
MAX_TRANSCRIPT   = 12000

# Market cap priority order for ranking multiple earnings
TICKER_PRIORITY = ["AAPL","MSFT","NVDA","GOOGL","AMZN","META","TSLA","NFLX",
                   "AMD","ORCL","CRM","INTC","QCOM","ADBE","NOW","SNOW","PLTR"]

print("=== MORNING BRIEF STARTING ===")
print(f"GEMINI_API_KEY set: {bool(GEMINI_API_KEY)}")
print(f"TO_EMAIL: {TO_EMAIL}")

# ── RETRY ─────────────────────────────────────────────────────────────────────
def api_post(url, payload, headers, timeout=60):
    for attempt in range(4):
        try:
            req = urllib.request.Request(
                url, data=json.dumps(payload).encode(),
                headers=headers, method="POST"
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            print(f"  HTTP {e.code} on attempt {attempt+1}: {e.reason}")
            if e.code == 429 and attempt < 3:
                wait = 20 * (attempt + 1)
                print(f"  Waiting {wait}s...")
                time.sleep(wait)
            else:
                raise

# ── GEMINI ────────────────────────────────────────────────────────────────────
def call_gemini(prompt, max_tokens=1200):
    print("  Calling Gemini...")
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"gemini-2.5-flash-lite:generateContent?key={GEMINI_API_KEY}")
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": max_tokens}
    }
    result = api_post(url, payload, {"Content-Type": "application/json"})
    text = result["candidates"][0]["content"]["parts"][0]["text"].strip()
    print(f"  Gemini OK: {len(text)} chars")
    return text

# ── DEEPSEEK ──────────────────────────────────────────────────────────────────
def call_deepseek(system, user, max_tokens=600):
    payload = {
        "model": "deepseek-chat", "max_tokens": max_tokens, "temperature": 0.2,
        "messages": [{"role": "system", "content": system},
                     {"role": "user",   "content": user}]
    }
    headers = {"Content-Type": "application/json",
               "Authorization": f"Bearer {DEEPSEEK_API_KEY}"}
    result = api_post("https://api.deepseek.com/chat/completions", payload, headers, timeout=30)
    return result["choices"][0]["message"]["content"].strip()

def parse_json_response(raw):
    """Safely parse JSON from LLM response, stripping markdown fences."""
    raw = raw.strip().strip("```json").strip("```").strip()
    return json.loads(raw)

# ── CS153 ─────────────────────────────────────────────────────────────────────
def get_cs153_video():
    print("Fetching CS153 RSS...")
    feed_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={CS153_CHANNEL_ID}"
    req = urllib.request.Request(feed_url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        root = ET.fromstring(resp.read())
    ns = {"atom":  "http://www.w3.org/2005/Atom",
          "yt":    "http://www.youtube.com/xml/schemas/2015",
          "media": "http://search.yahoo.com/mrss/"}
    entries = root.findall("atom:entry", ns)
    print(f"Found {len(entries)} videos in RSS")
    entry = None
    for e in entries:
        title = e.findtext("atom:title", default="", namespaces=ns).lower()
        if not any(kw in title for kw in SKIP_KEYWORDS):
            entry = e
            break
    if entry is None:
        entry = entries[0]
    video_id = entry.findtext("yt:videoId",    default="", namespaces=ns)
    title    = entry.findtext("atom:title",     default="", namespaces=ns)
    pub      = entry.findtext("atom:published", default="", namespaces=ns)
    desc_el  = entry.find("media:group/media:description", ns)
    desc     = (desc_el.text or "")[:1000] if desc_el is not None else ""
    print(f"Selected: {video_id} | {title[:70]}")
    return {"title": title, "video_id": video_id,
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "published": pub[:10], "description": desc}

def get_transcript(video_id):
    print(f"Fetching transcript for {video_id}...")
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        ytt = YouTubeTranscriptApi()
        fetched = ytt.fetch(video_id, languages=["en"])
        text = " ".join(s.text for s in fetched)[:MAX_TRANSCRIPT]
        print(f"Transcript OK: {len(text)} chars")
        return text
    except Exception as e:
        print(f"Transcript failed: {type(e).__name__}: {e}")
        return ""

def cs153_section(video):
    transcript = get_transcript(video["video_id"])
    content = f"TRANSCRIPT:\n{transcript}" if transcript else f"DESCRIPTION:\n{video['description']}"
    print("Using transcript." if transcript else "Using description fallback.")
    prompt = f"""You are an expert AI educator. Break down this Stanford CS153 lecture for a motivated learner.

Video: {video['title']}
Published: {video['published']}
{content}

Produce EXACTLY these sections — no preamble:

WHAT THIS LECTURE IS ABOUT
One clear sentence.

KEY CONCEPTS (exactly 4)
• **Concept Name**: 2 sentences — what it is and why it matters in the real world.

HOW TO APPLY THIS
3 concrete actionable bullet points someone could act on today.

REFLECT ON THIS
2 thought-provoking questions to sit with after watching.

WATCH: {video['url']}"""
    try:
        return call_gemini(prompt)
    except Exception as e:
        print(f"Gemini failed: {type(e).__name__}: {e}")
        return f"_CS153 unavailable today. [Watch directly]({video['url']})_"

# ── EARNINGS ──────────────────────────────────────────────────────────────────
EARNINGS_ANALYSIS_PROMPT = """You are an institutional equity analyst applying the tech-earnings-deepdive framework.

Company: {company} ({ticker})
Quarter: {quarter}
Revenue: {revenue} — {rev_beat}
EPS: {eps} — {eps_beat}
Guidance: {guidance}
Key Metric: {key_metric}

Produce a concise earnings memo — no preamble:

⚡ KEY FORCE
The single most important thing this quarter reveals about {company}'s future. 2 sentences.

📊 THREE SIGNALS
• **Revenue Quality**: One sentence on the beat/miss and what's driving it.
• **Forward Guidance**: One sentence on what guidance signals for next quarter.
• **Key Metric**: One sentence on the most important non-GAAP metric and its trend.

🔭 VARIANT VIEW
What is the market likely missing or overreacting to? 2 sentences.

🧭 INVESTOR TAKE (Buffett/Munger lens)
Add, hold, or trim — and why? 2 sentences.

⚠️ WATCH
One key risk to monitor next quarter. One sentence."""

DEEP_DIVE_PROMPT = """You are an institutional equity analyst applying the tech-earnings-deepdive framework.
Do a deep dive on {company} ({ticker}) — a rising tech company gaining momentum right now.

Produce a concise investment memo — no preamble:

⚡ KEY FORCE
The 1-2 decisive forces determining {company}'s future value. 3 sentences.

📊 BUSINESS QUALITY CHECK
• **Revenue Model**: One sentence on how they make money and its quality.
• **Competitive Moat**: One sentence on what protects their position.
• **Growth Vector**: One sentence on the primary growth driver right now.

📐 VALUATION SNAPSHOT
Current rough valuation context and whether it looks stretched, fair, or cheap vs. peers. 2 sentences.

🔭 VARIANT VIEW
What is the market missing about this company — bull or bear? 2 sentences.

🧭 INVESTOR TAKE (Buffett/Munger lens)
Quality compounder or speculative bet? Would they own it? 2 sentences.

⚠️ WATCH
The single biggest risk to the thesis. One sentence."""

def beat_label(val):
    return "✅ Beat" if val else "❌ Missed"

def find_earnings_today(today_str):
    """Ask DeepSeek to find ALL major tech earnings in last 24h, return ranked list."""
    system = "Financial data assistant. Respond in JSON only. No markdown, no explanation."
    user = f"""Today is {today_str}.
List ALL major tech companies (AAPL, MSFT, NVDA, GOOGL, AMZN, META, TSLA, AMD, NFLX, CRM, ORCL, QCOM, ADBE, NOW, SNOW, PLTR, etc.) that reported quarterly earnings in the last 24 hours.

Respond with ONLY valid JSON:
{{
  "earnings": [
    {{
      "ticker": "NVDA",
      "company": "NVIDIA",
      "quarter": "Q1 FY2026",
      "revenue": "$44.1B",
      "eps": "$0.89",
      "revenue_beat": true,
      "eps_beat": true,
      "guidance": "Q2 revenue guided at $45B, above consensus",
      "key_metric": "Data center revenue $39.1B, up 73% YoY"
    }}
  ]
}}

If no major tech earnings today, return: {{"earnings": []}}"""
    try:
        raw = call_deepseek(system, user, max_tokens=800)
        data = parse_json_response(raw)
        return data.get("earnings", [])
    except Exception as e:
        print(f"  Earnings finder failed: {e}")
        return []

def find_rising_company():
    """Ask DeepSeek to pick the most interesting rising tech company for a deep dive."""
    system = "Financial analyst. Respond in JSON only. No markdown."
    user = """Pick the single most interesting rising or high-momentum tech company right now for a deep dive — 
not one of the mega-cap stalwarts (no AAPL, MSFT, GOOGL, AMZN). 
Think: breakout growth, key narrative shift, emerging category leader, or recent catalyst.

Respond with ONLY valid JSON:
{"ticker": "PLTR", "company": "Palantir", "reason": "AIP platform driving rapid enterprise adoption with accelerating US commercial growth"}"""
    try:
        raw = call_deepseek(system, user, max_tokens=200)
        return parse_json_response(raw)
    except Exception as e:
        print(f"  Rising company finder failed: {e}")
        return None

def rank_earnings(earnings_list):
    """Sort earnings by TICKER_PRIORITY, return top 2."""
    def priority(e):
        try:
            return TICKER_PRIORITY.index(e["ticker"].upper())
        except ValueError:
            return 999
    return sorted(earnings_list, key=priority)[:2]

def analyze_single_earnings(e):
    """Run Gemini earnings analysis on one company."""
    prompt = EARNINGS_ANALYSIS_PROMPT.format(
        company=e["company"], ticker=e["ticker"], quarter=e.get("quarter",""),
        revenue=e.get("revenue","N/A"), rev_beat=beat_label(e.get("revenue_beat")),
        eps=e.get("eps","N/A"),         eps_beat=beat_label(e.get("eps_beat")),
        guidance=e.get("guidance","N/A"),
        key_metric=e.get("key_metric","N/A")
    )
    try:
        return call_gemini(prompt, max_tokens=900)
    except Exception as ex:
        print(f"  Analysis failed for {e['ticker']}: {ex}")
        return "_Analysis unavailable._"

def earnings_section():
    today_str = datetime.date.today().strftime("%B %d, %Y")
    print(f"Checking earnings for {today_str}...")

    earnings_list = find_earnings_today(today_str)

    # ── Multiple or single earnings ───────────────────────────────────────────
    if earnings_list:
        ranked = rank_earnings(earnings_list)
        print(f"  Found {len(earnings_list)} earnings report(s). Covering top {len(ranked)}.")
        results = []
        for e in ranked:
            print(f"  Analyzing {e['ticker']}...")
            analysis = analyze_single_earnings(e)
            results.append({
                "ticker": e["ticker"], "company": e["company"],
                "quarter": e.get("quarter",""), "analysis": analysis
            })
            time.sleep(2)  # small pause between Gemini calls
        return {"mode": "earnings", "reports": results}

    # ── No earnings — do a deep dive on a rising company ─────────────────────
    print("  No earnings today. Finding rising tech company for deep dive...")
    pick = find_rising_company()
    if not pick:
        return None

    ticker  = pick.get("ticker","")
    company = pick.get("company","")
    reason  = pick.get("reason","")
    print(f"  Deep dive: {company} ({ticker}) — {reason}")

    prompt = DEEP_DIVE_PROMPT.format(company=company, ticker=ticker)
    try:
        analysis = call_gemini(prompt, max_tokens=1000)
        return {"mode": "deepdive", "ticker": ticker, "company": company,
                "reason": reason, "analysis": analysis}
    except Exception as e:
        print(f"  Deep dive failed: {e}")
        return None

# ── NEWS ──────────────────────────────────────────────────────────────────────
def news_section(topic, sources):
    today = datetime.date.today().strftime("%B %d, %Y")
    system = "Sharp neutral news editor. Markdown only, no preamble."
    user   = (f"Today is {today}. Top 3 {topic} stories last 24hrs from {sources}.\n"
              f"Format: **Headline** — One sentence. *(Source)*\n"
              f"Rules: exactly 3 stories, one sentence each, last 24hrs only.")
    return call_deepseek(system, user)

# ── EMAIL ─────────────────────────────────────────────────────────────────────
def build_html(date, cs153, tech, politics, business, earnings_data=None):
    import re
    date_str = date.strftime("%A, %B %d, %Y")
    emoji    = ["🌙","🌱","🌿","🍃","☀️","🌤️","🌅"][date.weekday()]

    def md(t):
        t = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', t)
        t = re.sub(r'\*(.*?)\*',     r'<em>\1</em>', t)
        t = re.sub(r'^[•\-] (.*)',   r'<li>\1</li>', t, flags=re.MULTILINE)
        return "<p>" + t.replace('\n\n', '</p><p>').replace('\n', '<br>') + "</p>"

    earnings_html = ""
    if earnings_data:
        if earnings_data["mode"] == "earnings":
            for r in earnings_data["reports"]:
                earnings_html += f"""
<h2>📊 Earnings: {r['company']} ({r['ticker']}) — {r['quarter']}</h2>
{md(r['analysis'])}"""
        elif earnings_data["mode"] == "deepdive":
            earnings_html = f"""
<h2>🔍 Deep Dive: {earnings_data['company']} ({earnings_data['ticker']})</h2>
<p><em>{earnings_data['reason']}</em></p>
{md(earnings_data['analysis'])}"""

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:640px;margin:0 auto;padding:24px;color:#1a1a1a}}
h1{{font-size:22px;border-bottom:2px solid #e5e5e5;padding-bottom:8px}}
h2{{font-size:17px;color:#2d2d2d;margin-top:32px;border-left:3px solid #6366f1;padding-left:10px}}
li{{margin:6px 0;line-height:1.6}}p{{line-height:1.7;margin:8px 0}}
.ft{{font-size:11px;color:#9ca3af;margin-top:40px;border-top:1px solid #e5e5e5;padding-top:12px}}
a{{color:#6366f1}}</style></head><body>
<h1>{emoji} Morning Brief — {date_str}</h1>
<h2>🤖 CS153: Frontier Systems</h2>{md(cs153)}
{earnings_html}
<h2>💻 Tech & AI</h2>{md(tech)}
<h2>🏛️ Politics</h2>{md(politics)}
<h2>📈 Business</h2>{md(business)}
<div class="ft">Daily at 6 AM PT · <a href="https://www.youtube.com/@CS153Team">CS153</a> · Earnings: <a href="https://github.com/star23/Day1Global-Skills">Day1Global Skills</a></div>
</body></html>"""

def send_email(subject, html):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = GMAIL_ADDRESS
    msg["To"]      = TO_EMAIL
    msg.attach(MIMEText(html, "html"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(GMAIL_ADDRESS, GMAIL_APP_PASS)
        s.sendmail(GMAIL_ADDRESS, TO_EMAIL, msg.as_string())
    print("Email sent!")

# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    today = datetime.date.today()

    print("\n--- CS153 ---")
    video = get_cs153_video()
    cs153 = cs153_section(video)

    time.sleep(3)

    print("\n--- EARNINGS / DEEP DIVE ---")
    earnings_data = earnings_section()

    time.sleep(3)

    print("\n--- NEWS ---")
    tech     = news_section("technology and AI", "TechCrunch, The Verge, Ars Technica")
    politics = news_section("US and world politics", "AP News, Politico, Reuters")
    business = news_section("business and markets", "WSJ, Bloomberg, FT")

    print("\n--- EMAIL ---")
    send_email(
        f"☀️ Morning Brief — {today.strftime('%A, %B %d, %Y')}",
        build_html(today, cs153, tech, politics, business, earnings_data)
    )
    print("=== DONE ===")

main()
