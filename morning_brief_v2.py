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
def call_gemini(prompt):
    print("  Calling Gemini...")
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"gemini-2.5-flash-lite:generateContent?key={GEMINI_API_KEY}")
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 1200}
    }
    result = api_post(url, payload, {"Content-Type": "application/json"})
    text = result["candidates"][0]["content"]["parts"][0]["text"].strip()
    print(f"  Gemini OK: {len(text)} chars")
    return text

# ── DEEPSEEK ──────────────────────────────────────────────────────────────────
def call_deepseek(system, user):
    payload = {
        "model": "deepseek-chat", "max_tokens": 600, "temperature": 0.2,
        "messages": [{"role": "system", "content": system},
                     {"role": "user",   "content": user}]
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}"
    }
    result = api_post("https://api.deepseek.com/chat/completions", payload, headers, timeout=30)
    return result["choices"][0]["message"]["content"].strip()

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

    # Pick the most recent video that isn't office hours / Q&A
    entry = None
    for e in entries:
        title = e.findtext("atom:title", default="", namespaces=ns).lower()
        if not any(kw in title for kw in SKIP_KEYWORDS):
            entry = e
            break
    if entry is None:
        entry = entries[0]  # fallback to latest if all filtered

    video_id = entry.findtext("yt:videoId",    default="", namespaces=ns)
    title    = entry.findtext("atom:title",     default="", namespaces=ns)
    pub      = entry.findtext("atom:published", default="", namespaces=ns)
    desc_el  = entry.find("media:group/media:description", ns)
    desc     = desc_el.text[:1000] if desc_el is not None else ""

    print(f"Selected video: {video_id} | {title[:70]}")
    return {
        "title":       title,
        "video_id":    video_id,
        "url":         f"https://www.youtube.com/watch?v={video_id}",
        "published":   pub[:10],
        "description": desc
    }


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
    if transcript:
        content = f"TRANSCRIPT:\n{transcript}"
        print("Using transcript for Gemini prompt.")
    else:
        content = f"DESCRIPTION:\n{video['description']}"
        print("Falling back to description.")

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


# ── NEWS ──────────────────────────────────────────────────────────────────────
def news_section(topic, sources):
    today = datetime.date.today().strftime("%B %d, %Y")
    system = "Sharp neutral news editor. Markdown only, no preamble."
    user   = (f"Today is {today}. Top 3 {topic} stories last 24hrs from {sources}.\n"
              f"Format: **Headline** — One sentence. *(Source)*\n"
              f"Rules: exactly 3 stories, one sentence each, last 24hrs only.")
    return call_deepseek(system, user)


# ── EMAIL ─────────────────────────────────────────────────────────────────────
def build_html(date, cs153, tech, politics, business):
    import re
    date_str = date.strftime("%A, %B %d, %Y")
    emoji    = ["🌙","🌱","🌿","🍃","☀️","🌤️","🌅"][date.weekday()]

    def md(t):
        t = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', t)
        t = re.sub(r'\*(.*?)\*',     r'<em>\1</em>', t)
        t = re.sub(r'^[•\-] (.*)',   r'<li>\1</li>', t, flags=re.MULTILINE)
        return "<p>" + t.replace('\n\n', '</p><p>').replace('\n', '<br>') + "</p>"

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:640px;margin:0 auto;padding:24px;color:#1a1a1a}}
h1{{font-size:22px;border-bottom:2px solid #e5e5e5;padding-bottom:8px}}
h2{{font-size:17px;color:#2d2d2d;margin-top:32px;border-left:3px solid #6366f1;padding-left:10px}}
li{{margin:6px 0;line-height:1.6}}p{{line-height:1.7;margin:8px 0}}
.ft{{font-size:11px;color:#9ca3af;margin-top:40px;border-top:1px solid #e5e5e5;padding-top:12px}}
a{{color:#6366f1}}</style></head><body>
<h1>{emoji} Morning Brief — {date_str}</h1>
<h2>🤖 CS153: Frontier Systems</h2>{md(cs153)}
<h2>💻 Tech & AI</h2>{md(tech)}
<h2>🏛️ Politics</h2>{md(politics)}
<h2>📈 Business</h2>{md(business)}
<div class="ft">Daily at 6 AM PT · <a href="https://www.youtube.com/@CS153Team">CS153</a></div>
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

    print("\n--- NEWS ---")
    tech     = news_section("technology and AI", "TechCrunch, The Verge, Ars Technica")
    politics = news_section("US and world politics", "AP News, Politico, Reuters")
    business = news_section("business and markets", "WSJ, Bloomberg, FT")

    print("\n--- EMAIL ---")
    send_email(
        f"☀️ Morning Brief — {today.strftime('%A, %B %d, %Y')}",
        build_html(today, cs153, tech, politics, business)
    )
    print("=== DONE ===")

main()
