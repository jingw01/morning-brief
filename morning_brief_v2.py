#!/usr/bin/env python3
"""
Morning Brief — Final Edition
- Gemini 1.5 Flash 8B → CS153 digest (graceful fallback if quota hit)
- DeepSeek V3         → Tech / Politics / Business news
- Gmail SMTP          → email delivery
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
# ─────────────────────────────────────────────────────────────────────────────


def api_call_with_retry(req, timeout=60, retries=4, backoff=20):
    """Retry on 429 with exponential-ish backoff: 20s, 40s, 60s."""
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries - 1:
                wait = backoff * (attempt + 1)
                print(f"    429 rate limit. Waiting {wait}s (attempt {attempt+1}/{retries})...")
                time.sleep(wait)
                # Rebuild request (urllib requests can't be reused after error)
                req = urllib.request.Request(
                    req.full_url, data=req.data,
                    headers=dict(req.headers), method=req.get_method()
                )
            else:
                raise
    raise RuntimeError("All retries exhausted")


def call_gemini(prompt: str, video_url: str = None) -> str:
    parts = []
    if video_url:
        parts.append({"fileData": {"mimeType": "video/mp4", "fileUri": video_url}})
    parts.append({"text": prompt})
    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 1200}
    }
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"gemini-1.5-flash-8b:generateContent?key={GEMINI_API_KEY}")
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


def get_latest_cs153_video() -> dict:
    try:
        feed_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={CS153_CHANNEL_ID}"
        req = urllib.request.Request(feed_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            root = ET.fromstring(resp.read())
        ns = {"atom": "http://www.w3.org/2005/Atom",
              "yt": "http://www.youtube.com/xml/schemas/2015",
              "media": "http://search.yahoo.com/mrss/"}
        entry = root.find("atom:entry", ns)
        if entry is None:
            return {}
        video_id  = entry.findtext("yt:videoId",    default="", namespaces=ns)
        title     = entry.findtext("atom:title",     default="", namespaces=ns)
        published = entry.findtext("atom:published", default="", namespaces=ns)
        desc_el   = entry.find("media:group/media:description", ns)
        desc      = desc_el.text[:2000] if desc_el is not None else ""
        return {"title": title, "url": f"https://www.youtube.com/watch?v={video_id}",
                "published": published[:10], "description": desc}
    except Exception as e:
        print(f"    Could not fetch CS153 RSS: {e}")
        return {}


def generate_cs153_section(video: dict) -> str:
    if not video:
        return "_CS153 video unavailable today._"

    prompt = f"""You are an expert AI educator. Break down this Stanford CS153 lecture for a motivated learner.

Video: {video['title']}
Published: {video['published']}
Description: {video['description']}

Produce EXACTLY these sections, no preamble:

WHAT THIS LECTURE IS ABOUT
One sentence.

KEY CONCEPTS (exactly 4)
• Concept Name: 2 sentences — what it is and why it matters.

HOW TO APPLY THIS
3 concrete actionable bullet points someone could act on today.

REFLECT ON THIS
2 thought-provoking questions to sit with.

WATCH: {video['url']}"""

    try:
        return call_gemini(prompt, video_url=video["url"])
    except urllib.error.HTTPError as e:
        if e.code == 429:
            print("    Gemini quota exhausted — falling back to description-only summary.")
            return f"""**{video['title']}**

Gemini API quota was exceeded today — here's the raw description to read yourself:

{video['description'][:800]}...

[Watch the full lecture]({video['url']})"""
        raise
    except Exception as e:
        print(f"    Gemini failed: {e}")
        return f"_CS153 digest unavailable today. [Watch directly]({video['url']})_"


def generate_news_section(topic: str, sources: str) -> str:
    today = datetime.date.today().strftime("%B %d, %Y")
    system = "Sharp, neutral news editor. Factual summaries only. Markdown, no preamble."
    user = f"""Today is {today}. Top 3 {topic} stories from the last 24 hours.
Preferred sources: {sources}.
Format: **Headline** — One sentence summary. *(Source)*
Rules: exactly 3 stories, one sentence each, last 24hrs only."""
    return call_deepseek(system, user)


def build_html(date, cs153, tech, politics, business):
    import re
    date_str = date.strftime("%A, %B %d, %Y")
    emoji = ["🌙","🌱","🌿","🍃","☀️","🌤️","🌅"][date.weekday()]

    def md(text):
        text = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', text)
        text = re.sub(r'\*(.*?)\*', r'<em>\1</em>', text)
        text = re.sub(r'^[•\-] (.*)', r'<li>\1</li>', text, flags=re.MULTILINE)
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


def main():
    today = datetime.date.today()
    print(f"[morning_brief] {today} — starting...")

    print("  → CS153: fetching latest video...")
    video = get_latest_cs153_video()

    print("  → CS153: generating Gemini digest...")
    cs153 = generate_cs153_section(video)

    time.sleep(3)

    print("  → DeepSeek: Tech news...")
    tech = generate_news_section("technology and AI", "TechCrunch, The Verge, Ars Technica")

    print("  → DeepSeek: Politics news...")
    politics = generate_news_section("US and world politics", "AP News, Politico, Reuters")

    print("  → DeepSeek: Business news...")
    business = generate_news_section("business, markets, and economy", "WSJ, Bloomberg, FT")

    print("  → Sending email...")
    subject = f"☀️ Morning Brief — {today.strftime('%A, %B %d, %Y')}"
    send_email(subject, build_html(today, cs153, tech, politics, business))
    print("  ✅ Done!")


if __name__ == "__main__":
    main()
