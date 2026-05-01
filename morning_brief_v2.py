#!/usr/bin/env python3
"""
Morning Brief — Final Edition (transcript-based CS153)
- youtube-transcript-api → fetch CS153 transcript (no video processing)
- Gemini 1.5 Flash 8B   → CS153 digest from transcript text
- DeepSeek V3           → Tech / Politics / Business news
- Gmail SMTP            → email delivery
"""

import os, json, time, smtplib, datetime, urllib.request, urllib.error
import xml.etree.ElementTree as ET
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# ── CONFIG ────────────────────────────────────────────────────────────────────
GEMINI_API_KEY   = os.environ["GEMINI_API_KEY"]
DEEPSEEK_API_KEY = os.environ["DEEPSEEK_API_KEY"]
GMAIL_ADDRESS    = os.environ["GMAIL_ADDRESS"]
GMAIL_APP_PASS   = os.environ["GMAIL_APP_PASS"]
TO_EMAIL         = os.environ.get("TO_EMAIL", GMAIL_ADDRESS)
CS153_CHANNEL_ID = "UC0YBJCRIt4kA2jZ7siTGMyQ"
MAX_TRANSCRIPT_CHARS = 12000
# ─────────────────────────────────────────────────────────────────────────────


def api_call_with_retry(req, timeout=60, retries=4, backoff=20):
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries - 1:
                wait = backoff * (attempt + 1)
                print(f"    429 rate limit. Waiting {wait}s (attempt {attempt+1}/{retries})...")
                time.sleep(wait)
                req = urllib.request.Request(
                    req.full_url, data=req.data,
                    headers=dict(req.headers), method=req.get_method()
                )
            else:
                raise
    raise RuntimeError("All retries exhausted")


def call_gemini(prompt: str) -> str:
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 1200}
    }
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"gemini-2.5-flash-lite:generateContent?key={GEMINI_API_KEY}")
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST"
    )
    result = api_call_with_retry(req, timeout=60)
    return result["candidates"][0]["content"]["parts"][0]["text"].strip()


def call_deepseek(system: str, user: str) -> str:
    payload = {
        "model": "deepseek-chat", "max_tokens": 600, "temperature": 0.2,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}]
    }
    req = urllib.request.Request(
        "https://api.deepseek.com/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {DEEPSEEK_API_KEY}"},
        method="POST"
    )
    result = api_call_with_retry(req, timeout=30)
    return result["choices"][0]["message"]["content"].strip()


# ── CS153 ─────────────────────────────────────────────────────────────────────

def get_latest_cs153_video() -> dict:
    print("  [DEBUG] Fetching CS153 RSS feed...")
    try:
        feed_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={CS153_CHANNEL_ID}"
        req = urllib.request.Request(feed_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
        print(f"  [DEBUG] RSS feed fetched: {len(raw)} bytes")
        root = ET.fromstring(raw)
        ns = {"atom": "http://www.w3.org/2005/Atom",
              "yt":   "http://www.youtube.com/xml/schemas/2015",
              "media":"http://search.yahoo.com/mrss/"}

        # Log all videos found in feed for visibility
        entries = root.findall("atom:entry", ns)
        print(f"  [DEBUG] Videos found in RSS: {len(entries)}")
        for i, e in enumerate(entries[:3]):
            t = e.findtext("atom:title", default="?", namespaces=ns)
            vid = e.findtext("yt:videoId", default="?", namespaces=ns)
            pub = e.findtext("atom:published", default="?", namespaces=ns)
            print(f"  [DEBUG]   [{i}] {pub[:10]} | {vid} | {t[:60]}")

        entry = entries[0] if entries else None
        if entry is None:
            print("  [DEBUG] No entries found in RSS!")
            return {}

        video_id  = entry.findtext("yt:videoId",    default="", namespaces=ns)
        title     = entry.findtext("atom:title",     default="", namespaces=ns)
        published = entry.findtext("atom:published", default="", namespaces=ns)
        desc_el   = entry.find("media:group/media:description", ns)
        desc      = desc_el.text[:1000] if desc_el is not None else ""

        print(f"  [DEBUG] Selected video: {video_id} | {title[:60]}")
        return {"title": title, "video_id": video_id,
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "published": published[:10], "description": desc}
    except Exception as e:
        print(f"  [DEBUG] RSS fetch FAILED: {type(e).__name__}: {e}")
        return {}


def get_transcript(video_id: str) -> str:
    print(f"  [DEBUG] Fetching transcript for video_id={video_id}...")
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        from youtube_transcript_api._errors import TranscriptsDisabled, NoTranscriptFound

        # List available transcripts first
        try:
            transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
            available = [(t.language_code, t.is_generated) for t in transcript_list]
            print(f"  [DEBUG] Available transcripts: {available}")
        except Exception as e:
            print(f"  [DEBUG] Could not list transcripts: {e}")

        transcript = YouTubeTranscriptApi.get_transcript(video_id, languages=["en"])
        full_text = " ".join(chunk["text"] for chunk in transcript)
        print(f"  [DEBUG] Transcript fetched: {len(full_text):,} chars, {len(transcript)} chunks")

        if len(full_text) > MAX_TRANSCRIPT_CHARS:
            full_text = full_text[:MAX_TRANSCRIPT_CHARS] + "... [transcript trimmed]"
            print(f"  [DEBUG] Transcript trimmed to {MAX_TRANSCRIPT_CHARS:,} chars")

        return full_text

    except Exception as e:
        print(f"  [DEBUG] Transcript FAILED: {type(e).__name__}: {e}")
        return ""


def generate_cs153_section(video: dict) -> str:
    if not video:
        print("  [DEBUG] No video data — skipping CS153 section.")
        return "_CS153 video unavailable today._"

    transcript = get_transcript(video.get("video_id", ""))

    if transcript:
        content_label = "TRANSCRIPT"
        content_text  = transcript
        print("  [DEBUG] Using transcript for Gemini prompt.")
    else:
        content_label = "DESCRIPTION"
        content_text  = video.get("description", "No description available.")
        print(f"  [DEBUG] Falling back to description ({len(content_text)} chars).")

    prompt = f"""You are an expert AI educator. Break down this Stanford CS153 lecture for a motivated learner.

Video: {video['title']}
Published: {video['published']}
{content_label}:
{content_text}

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

    print(f"  [DEBUG] Sending prompt to Gemini ({len(prompt):,} chars)...")
    try:
        result = call_gemini(prompt)
        print(f"  [DEBUG] Gemini response: {len(result):,} chars")
        return result
    except Exception as e:
        print(f"  [DEBUG] Gemini FAILED: {type(e).__name__}: {e}")
        return f"_CS153 digest unavailable today ({type(e).__name__}). [Watch directly]({video['url']})_"


# ── NEWS ──────────────────────────────────────────────────────────────────────

def generate_news_section(topic: str, sources: str) -> str:
    today = datetime.date.today().strftime("%B %d, %Y")
    system = "Sharp, neutral news editor. Factual summaries only. Markdown, no preamble."
    user = f"""Today is {today}. Top 3 {topic} stories from the last 24 hours.
Preferred sources: {sources}.
Format: **Headline** — One sentence summary. *(Source)*
Rules: exactly 3 stories, one sentence each, last 24hrs only."""
    return call_deepseek(system, user)


# ── EMAIL ─────────────────────────────────────────────────────────────────────

def build_html(date, cs153, tech, politics, business):
    import re
    date_str = date.strftime("%A, %B %d, %Y")
    emoji = ["🌙","🌱","🌿","🍃","☀️","🌤️","🌅"][date.weekday()]

    def md(text):
        text = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', text)
        text = re.sub(r'\*(.*?)\*',     r'<em>\1</em>', text)
        text = re.sub(r'^[•\-] (.*)',   r'<li>\1</li>', text, flags=re.MULTILINE)
        text = text.replace('\n\n', '</p><p>').replace('\n', '<br>')
        return f"<p>{text}</p>"

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:640px;margin:0 auto;padding:24px;color:#1a1a1a}}
h1{{font-size:22px;border-bottom:2px solid #e5e5e5;padding-bottom:8px}}
h2{{font-size:17px;color:#2d2d2d;margin-top:32px;border-left:3px solid #6366f1;padding-left:10px}}
li{{margin:6px 0;line-height:1.6}}p{{line-height:1.7;margin:8px 0}}
.footer{{font-size:11px;color:#9ca3af;margin-top:40px;border-top:1px solid #e5e5e5;padding-top:12px}}
a{{color:#6366f1}}</style></head><body>
<h1>{emoji} Morning Brief — {date_str}</h1>
<h2>🤖 CS153: Frontier Systems</h2>{md(cs153)}
<h2>💻 Tech & AI</h2>{md(tech)}
<h2>🏛️ Politics</h2>{md(politics)}
<h2>📈 Business</h2>{md(business)}
<div class="footer">Generated daily at 6 AM PT · <a href="https://www.youtube.com/@CS153Team">CS153 YouTube</a></div>
</body></html>"""


def send_email(subject, html_body):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = GMAIL_ADDRESS
    msg["To"]      = TO_EMAIL
    msg.attach(MIMEText(html_body, "html"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_ADDRESS, GMAIL_APP_PASS)
        server.sendmail(GMAIL_ADDRESS, TO_EMAIL, msg.as_string())


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    today = datetime.date.today()
    print(f"[morning_brief] {today} — starting...")
    print(f"  [DEBUG] TO_EMAIL={os.environ.get('TO_EMAIL', '(not set)')}")
    print(f"  [DEBUG] GEMINI_API_KEY={'set' if GEMINI_API_KEY else 'MISSING'}")
    print(f"  [DEBUG] DEEPSEEK_API_KEY={'set' if DEEPSEEK_API_KEY else 'MISSING'}")

    print("\n  → CS153: fetching latest video...")
    video = get_latest_cs153_video()

    print("\n  → CS153: generating digest...")
    cs153 = generate_cs153_section(video)

    time.sleep(3)

    print("\n  → DeepSeek: Tech news...")
    tech = generate_news_section("technology and AI", "TechCrunch, The Verge, Ars Technica")

    print("\n  → DeepSeek: Politics news...")
    politics = generate_news_section("US and world politics", "AP News, Politico, Reuters")

    print("\n  → DeepSeek: Business news...")
    business = generate_news_section("business, markets, and economy", "WSJ, Bloomberg, FT")

    print("\n  → Sending email...")
    subject = f"☀️ Morning Brief — {today.strftime('%A, %B %d, %Y')}"
    html = build_html(today, cs153, tech, politics, business)
    print(f"  [DEBUG] Email HTML length: {len(html):,} chars")
    send_email(subject, html)
    print("  ✅ Done!")


if __name__ == "__main__":
    main()
