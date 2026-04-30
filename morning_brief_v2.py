#!/usr/bin/env python3
"""
Morning Brief — Hybrid Edition
- Gemini 2.0 Flash  → CS153 expanded learning digest
- DeepSeek V3       → Tech / Politics / Business news
- Gmail SMTP        → delivers to your inbox
"""

import os
import json
import smtplib
import datetime
import urllib.request
import xml.etree.ElementTree as ET
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# ── CONFIG (set these as GitHub Actions secrets) ───────────────────────────────
GEMINI_API_KEY   = os.environ["GEMINI_API_KEY"]
DEEPSEEK_API_KEY = os.environ["DEEPSEEK_API_KEY"]
GMAIL_ADDRESS    = os.environ["GMAIL_ADDRESS"]    # your Gmail address
GMAIL_APP_PASS   = os.environ["GMAIL_APP_PASS"]   # Gmail App Password (not your login password)
TO_EMAIL         = os.environ.get("TO_EMAIL", GMAIL_ADDRESS)

CS153_CHANNEL_ID = "UC0YBJCRIt4kA2jZ7siTGMyQ"
# ──────────────────────────────────────────────────────────────────────────────


# ── GEMINI ────────────────────────────────────────────────────────────────────

def call_gemini(prompt: str, video_url: str = None) -> str:
    """Call Gemini 2.0 Flash, optionally with a YouTube video as context."""
    parts = []
    if video_url:
        parts.append({
            "fileData": {
                "mimeType": "video/mp4",
                "fileUri": video_url   # Gemini accepts YouTube URLs directly
            }
        })
    parts.append({"text": prompt})

    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {
            "temperature": 0.3,
            "maxOutputTokens": 1200,
        }
    }

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
    )
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        result = json.loads(resp.read())

    return result["candidates"][0]["content"]["parts"][0]["text"].strip()


# ── DEEPSEEK ──────────────────────────────────────────────────────────────────

def call_deepseek(system: str, user: str) -> str:
    """Call DeepSeek V3 via its OpenAI-compatible API."""
    payload = {
        "model": "deepseek-chat",
        "max_tokens": 600,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ]
    }
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        "https://api.deepseek.com/chat/completions",
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        },
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read())
    return result["choices"][0]["message"]["content"].strip()


# ── CS153 ─────────────────────────────────────────────────────────────────────

def get_latest_cs153_video() -> dict:
    """Fetch latest CS153 video from YouTube RSS."""
    feed_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={CS153_CHANNEL_ID}"
    req = urllib.request.Request(feed_url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        root = ET.fromstring(resp.read())

    ns = {
        "atom":  "http://www.w3.org/2005/Atom",
        "yt":    "http://www.youtube.com/xml/schemas/2015",
        "media": "http://search.yahoo.com/mrss/",
    }
    entry = root.find("atom:entry", ns)
    if entry is None:
        return {}

    video_id = entry.findtext("yt:videoId", default="", namespaces=ns)
    title    = entry.findtext("atom:title",  default="", namespaces=ns)
    published= entry.findtext("atom:published", default="", namespaces=ns)
    desc_el  = entry.find("media:group/media:description", ns)
    desc     = desc_el.text[:2000] if desc_el is not None else ""

    return {
        "title":     title,
        "video_id":  video_id,
        "url":       f"https://www.youtube.com/watch?v={video_id}",
        "published": published[:10],
        "description": desc,
    }


def generate_cs153_section(video: dict) -> str:
    """Use Gemini to produce an expanded CS153 digest, watching the video directly."""
    if not video:
        return "<p><em>Could not fetch CS153 video today.</em></p>"

    prompt = f"""
You are an expert AI educator breaking down a Stanford CS153 lecture for a motivated learner.

Video: {video['title']}
Published: {video['published']}
Description: {video['description']}

Produce a concise but rich digest with EXACTLY these sections — no preamble:

🎯 WHAT THIS LECTURE IS ABOUT
One sentence.

🧠 KEY CONCEPTS (exactly 4)
For each concept:
• **Concept Name**: 2 sentences — what it is and why it matters in the real world.

🛠️ HOW TO APPLY THIS
3 concrete, actionable bullet points. How would someone use these ideas today — in a project, job, or side experiment?

🤔 REFLECT ON THIS
2 thought-provoking questions to sit with after watching.

🔗 WATCH: {video['url']}
"""
    try:
        # Try with video file first (Gemini can watch YouTube directly)
        return call_gemini(prompt, video_url=video["url"])
    except Exception:
        # Fall back to description-only if video loading fails
        return call_gemini(prompt, video_url=None)


# ── NEWS ──────────────────────────────────────────────────────────────────────

def generate_news_section(topic: str, sources: str) -> str:
    """Use DeepSeek to summarize today's top news for a given topic."""
    today = datetime.date.today().strftime("%B %d, %Y")
    system = (
        "You are a sharp, neutral news editor. Write clean, factual summaries. "
        "No preamble, no meta-commentary. Markdown only."
    )
    user = f"""
Today is {today}. Find the top 3 {topic} news stories from the last 24 hours.
Preferred sources: {sources}.

Format each story as:
**Headline** — One sentence: what happened and why it matters. *(Source)*

Strict rules:
- Exactly 3 stories.
- Exactly one sentence per story.
- No stories older than 24 hours.
- If nothing significant: write *Nothing significant today.*
"""
    return call_deepseek(system, user)


# ── EMAIL ─────────────────────────────────────────────────────────────────────

def build_html(date: datetime.date, cs153: str, tech: str, politics: str, business: str) -> str:
    """Render the newsletter as clean HTML email."""
    date_str = date.strftime("%A, %B %d, %Y")
    weekday_emojis = ["🌙","🌱","🌿","🍃","☀️","🌤️","🌅"]
    emoji = weekday_emojis[date.weekday()]

    def md_to_simple_html(text: str) -> str:
        """Minimal markdown → HTML for email."""
        import re
        text = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', text)
        text = re.sub(r'\*(.*?)\*',     r'<em>\1</em>', text)
        text = re.sub(r'🎯 (.*)',  r'<h3>🎯 \1</h3>', text)
        text = re.sub(r'🧠 (.*)',  r'<h3>🧠 \1</h3>', text)
        text = re.sub(r'🛠️ (.*)', r'<h3>🛠️ \1</h3>', text)
        text = re.sub(r'🤔 (.*)',  r'<h3>🤔 \1</h3>', text)
        text = re.sub(r'🔗 (.*)',  r'<p>🔗 \1</p>', text)
        text = re.sub(r'^• (.*)',  r'<li>\1</li>', text, flags=re.MULTILINE)
        text = re.sub(r'^- (.*)',  r'<li>\1</li>', text, flags=re.MULTILINE)
        text = text.replace('\n\n', '</p><p>').replace('\n', '<br>')
        return f"<p>{text}</p>"

    return f"""
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
          max-width: 640px; margin: 0 auto; padding: 24px; color: #1a1a1a; }}
  h1   {{ font-size: 22px; border-bottom: 2px solid #e5e5e5; padding-bottom: 8px; }}
  h2   {{ font-size: 17px; color: #2d2d2d; margin-top: 32px; border-left: 3px solid #6366f1;
          padding-left: 10px; }}
  h3   {{ font-size: 14px; color: #4b5563; margin: 16px 0 4px; }}
  li   {{ margin: 6px 0; line-height: 1.6; }}
  p    {{ line-height: 1.7; margin: 8px 0; }}
  .footer {{ font-size: 11px; color: #9ca3af; margin-top: 40px; border-top: 1px solid #e5e5e5;
             padding-top: 12px; }}
  a    {{ color: #6366f1; }}
</style>
</head>
<body>

<h1>{emoji} Morning Brief — {date_str}</h1>

<h2>🤖 CS153: Frontier Systems</h2>
{md_to_simple_html(cs153)}

<h2>💻 Tech & AI</h2>
{md_to_simple_html(tech)}

<h2>🏛️ Politics</h2>
{md_to_simple_html(politics)}

<h2>📈 Business</h2>
{md_to_simple_html(business)}

<div class="footer">
  Generated daily at 6 AM PT ·
  <a href="https://www.youtube.com/@CS153Team">CS153 YouTube</a>
</div>

</body>
</html>
"""


def send_email(subject: str, html_body: str):
    """Send the newsletter via Gmail SMTP."""
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

    print("  → CS153: fetching latest video...")
    video = get_latest_cs153_video()

    print("  → CS153: generating Gemini digest...")
    cs153 = generate_cs153_section(video)

    print("  → DeepSeek: Tech news...")
    tech = generate_news_section("technology and AI", "TechCrunch, The Verge, Ars Technica")

    print("  → DeepSeek: Politics news...")
    politics = generate_news_section("US and world politics", "AP News, Politico, Reuters")

    print("  → DeepSeek: Business news...")
    business = generate_news_section("business, markets, and economy", "WSJ, Bloomberg, FT")

    print("  → Building email...")
    date_str = today.strftime("%A, %B %d, %Y")
    subject  = f"☀️ Morning Brief — {date_str}"
    html     = build_html(today, cs153, tech, politics, business)

    print("  → Sending email...")
    send_email(subject, html)
    print("  ✅ Done!")


if __name__ == "__main__":
    main()
