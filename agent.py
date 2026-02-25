"""
Sweden Startup Funding Agent
-----------------------------
Scrapes Swedish startup funding news daily and sends a formatted
HTML digest to your Gmail. Designed to help identify funded startups
that may be hiring for Data Science, ML Engineering, and Quant roles.
"""

import os
import smtplib
import feedparser
import requests
from bs4 import BeautifulSoup
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime

# ── Configuration ────────────────────────────────────────────────────────────
GMAIL_ADDRESS      = os.environ["GMAIL_ADDRESS"]
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]
RECIPIENT_EMAIL    = GMAIL_ADDRESS  # sending the digest to yourself

# Keywords used to score article relevance
FUNDING_KEYWORDS = [
    "funding", "raises", "raised", "investment", "series a", "series b",
    "series c", "seed", "venture", "capital", "million", "miljon",
    "miljard", "finansiering", "investering", "runda", "round",
]
DOMAIN_KEYWORDS = [
    "ai", "machine learning", "ml", "data", "fintech", "saas", "tech",
    "software", "analytics", "deep learning", "nlp", "platform",
    "automation", "quantitative", "algorithm",
]
SWEDEN_KEYWORDS = [
    "sweden", "swedish", "stockholm", "gothenburg", "göteborg",
    "malmö", "nordic", "scandinavia", "sverige", "svensk",
]


# ── Scrapers ─────────────────────────────────────────────────────────────────

def fetch_google_news(query: str) -> list[dict]:
    """Fetch articles from Google News RSS for a given query."""
    url = f"https://news.google.com/rss/search?q={query}&hl=en-SE&gl=SE&ceid=SE:en"
    try:
        feed = feedparser.parse(url)
        articles = []
        for entry in feed.entries[:15]:
            summary_raw = entry.get("summary", "")
            summary_text = BeautifulSoup(summary_raw, "html.parser").get_text()
            articles.append({
                "title":     entry.get("title", "No title"),
                "link":      entry.get("link", "#"),
                "published": entry.get("published", "Unknown date"),
                "source":    entry.get("source", {}).get("title", "Google News"),
                "summary":   summary_text[:400],
            })
        return articles
    except Exception as exc:
        print(f"[Google News] Error for query '{query}': {exc}")
        return []


def fetch_breakit() -> list[dict]:
    """Fetch funding-related articles from Breakit (Sweden's top tech news)."""
    try:
        feed = feedparser.parse("https://www.breakit.se/feed/articles")
        articles = []
        for entry in feed.entries[:20]:
            title   = entry.get("title", "")
            summary = BeautifulSoup(entry.get("summary", ""), "html.parser").get_text()
            combined = (title + " " + summary).lower()
            if any(kw in combined for kw in FUNDING_KEYWORDS):
                articles.append({
                    "title":     title,
                    "link":      entry.get("link", "#"),
                    "published": entry.get("published", "Unknown date"),
                    "source":    "Breakit",
                    "summary":   summary[:400],
                })
        return articles
    except Exception as exc:
        print(f"[Breakit] Error: {exc}")
        return []


def fetch_di_digital() -> list[dict]:
    """Fetch from Dagens industri digital (Swedish business news)."""
    try:
        feed = feedparser.parse("https://digital.di.se/rss")
        articles = []
        for entry in feed.entries[:20]:
            title   = entry.get("title", "")
            summary = BeautifulSoup(entry.get("summary", ""), "html.parser").get_text()
            combined = (title + " " + summary).lower()
            if any(kw in combined for kw in FUNDING_KEYWORDS):
                articles.append({
                    "title":     title,
                    "link":      entry.get("link", "#"),
                    "published": entry.get("published", "Unknown date"),
                    "source":    "Dagens industri Digital",
                    "summary":   summary[:400],
                })
        return articles
    except Exception as exc:
        print(f"[DI Digital] Error: {exc}")
        return []


# ── Scoring & Deduplication ───────────────────────────────────────────────────

def score_article(article: dict) -> int:
    """
    Score an article for relevance.
    Higher score = more likely to be a funded Swedish startup
    in a domain relevant to Data Science / ML / Quant.
    """
    text = (article["title"] + " " + article["summary"]).lower()
    score = 0
    score += sum(2 for kw in FUNDING_KEYWORDS if kw in text)
    score += sum(3 for kw in DOMAIN_KEYWORDS  if kw in text)
    score += sum(1 for kw in SWEDEN_KEYWORDS  if kw in text)
    return score


def deduplicate(articles: list[dict]) -> list[dict]:
    """Remove near-duplicate articles by comparing title prefixes."""
    seen, unique = set(), []
    for a in articles:
        key = a["title"].lower()[:60]
        if key not in seen:
            seen.add(key)
            unique.append(a)
    return unique


# ── Email Builder ─────────────────────────────────────────────────────────────

def build_html(articles: list[dict]) -> str:
    date_str = datetime.now().strftime("%A, %d %B %Y")
    count    = len(articles)

    rows = ""
    for a in articles:
        rows += f"""
        <div class="card">
          <h3><a href="{a['link']}">{a['title']}</a></h3>
          <p class="meta">📰 {a['source']} &nbsp;·&nbsp; 📅 {a['published']}</p>
          <p class="summary">{a['summary']}{'…' if len(a['summary']) >= 400 else ''}</p>
        </div>"""

    no_results = """
        <div class="card">
          <p>No new funding news found today — check back tomorrow!</p>
        </div>""" if not articles else ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<style>
  body      {{ font-family: Arial, sans-serif; background:#f4f4f4; margin:0; padding:20px; color:#333; }}
  .wrap     {{ max-width:680px; margin:auto; background:#fff; border-radius:8px; overflow:hidden;
               box-shadow:0 2px 8px rgba(0,0,0,.1); }}
  .header   {{ background:#0d1b2a; color:#fff; padding:28px 32px; }}
  .header h1{{ margin:0 0 6px; font-size:22px; }}
  .header p {{ margin:0; opacity:.75; font-size:14px; }}
  .body     {{ padding:24px 32px; }}
  .card     {{ background:#f9f9f9; border-left:4px solid #e63946; border-radius:4px;
               padding:16px 18px; margin-bottom:18px; }}
  .card h3  {{ margin:0 0 6px; font-size:16px; }}
  .card h3 a{{ color:#0d1b2a; text-decoration:none; }}
  .card h3 a:hover {{ color:#e63946; }}
  .meta     {{ color:#888; font-size:12px; margin:0 0 8px; }}
  .summary  {{ font-size:14px; margin:0; line-height:1.5; }}
  .footer   {{ background:#f0f0f0; padding:16px 32px; font-size:12px; color:#999; }}
  .tag      {{ display:inline-block; background:#e63946; color:#fff; font-size:11px;
               padding:2px 8px; border-radius:10px; margin-right:4px; }}
</style>
</head>
<body>
<div class="wrap">
  <div class="header">
    <h1>🇸🇪 Sweden Startup Funding Digest</h1>
    <p>{date_str} &nbsp;·&nbsp; {count} article{'s' if count != 1 else ''} found</p>
  </div>
  <div class="body">
    <p>Your daily roundup of Swedish startup funding news — potential employers
       actively building teams after raising capital.</p>
    {rows or no_results}
  </div>
  <div class="footer">
    <p>🤖 Generated by your Sweden Startup Funding Agent</p>
    <p>Target roles:
      <span class="tag">Data Scientist</span>
      <span class="tag">ML Engineer</span>
      <span class="tag">Quant Analyst</span>
    </p>
  </div>
</div>
</body>
</html>"""


# ── Email Sender ──────────────────────────────────────────────────────────────

def send_email(html: str, count: int) -> None:
    subject = (
        f"🇸🇪 Sweden Startup Digest – {count} article{'s' if count != 1 else ''} "
        f"| {datetime.now().strftime('%d %b %Y')}"
    )
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = GMAIL_ADDRESS
    msg["To"]      = RECIPIENT_EMAIL
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_ADDRESS, RECIPIENT_EMAIL, msg.as_string())

    print(f"✅ Email sent — {count} articles — {datetime.now().strftime('%H:%M:%S')}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    print(f"🚀 Agent starting at {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}")

    raw: list[dict] = []

    # Google News — multiple targeted queries
    for query in [
        "Sweden+startup+funding",
        "Swedish+startup+raises+million",
        "Stockholm+startup+investment+round",
        "Sverige+startup+finansiering",
        "Nordic+startup+funding",
    ]:
        raw.extend(fetch_google_news(query))

    # Swedish-specific feeds
    raw.extend(fetch_breakit())
    raw.extend(fetch_di_digital())

    # Deduplicate, score, and sort
    articles = deduplicate(raw)
    articles = [a for a in articles if score_article(a) >= 3]   # filter low-relevance
    articles.sort(key=score_article, reverse=True)              # best first
    articles = articles[:25]                                    # cap at 25 per digest

    print(f"📰 {len(articles)} relevant articles after filtering")

    html = build_html(articles)
    send_email(html, len(articles))


if __name__ == "__main__":
    main()
