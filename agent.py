"""
Sweden Startup Funding Agent  — v2
------------------------------------
Daily digest of Swedish startup funding news, filtered to companies
likely to hire Data Scientists, ML Engineers, Data Engineers, and
Quantitative Analysts. Deduplicates stories, clusters by company,
and links directly to LinkedIn company search.
"""

import os
import re
import smtplib
import feedparser
from bs4 import BeautifulSoup
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timezone, timedelta
from urllib.parse import quote
from collections import defaultdict

# ── Configuration ─────────────────────────────────────────────────────────────
GMAIL_ADDRESS      = os.environ["GMAIL_ADDRESS"]
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]
RECIPIENT_EMAIL    = GMAIL_ADDRESS

# Articles must be published within this many days to appear in the digest.
MAX_AGE_DAYS = 180   # hard cutoff — nothing older than 6 months
FRESH_DAYS   = 3     # articles within 3 days get a NEW badge

# ── Domain filters ────────────────────────────────────────────────────────────
# An article MUST contain at least one TECH keyword to be included.
TECH_KEYWORDS = [
    "ai", "artificial intelligence", "machine learning", "deep learning",
    "ml", "nlp", "natural language", "computer vision", "llm",
    "data", "analytics", "data science", "big data", "data platform",
    "fintech", "financial technology", "trading", "quantitative", "quant",
    "algorithmic", "insurtech", "wealthtech", "regtech",
    "saas", "software", "platform", "cloud", "api", "developer",
    "cybersecurity", "security", "infosec",
    "robotics", "automation", "iot", "internet of things",
    "biotech", "medtech", "healthtech", "digital health",
    "edtech", "legaltech", "proptech", "cleantech", "climatetech",
    "blockchain", "crypto", "web3", "semiconductor", "hardware",
    "e-commerce", "marketplace", "logistics tech", "supply chain tech",
    "tech", "technology", "digital", "software-as-a-service",
]

# An article is excluded if it matches ANY of these.
EXCLUDE_KEYWORDS = [
    "plumbing", "carpentry", "carpenter", "pet grooming", "veterinary clinic",
    "dental clinic", "dentist", "restaurant chain", "hair salon", "barbershop",
    "construction firm", "real estate agency", "accounting firm", "law firm",
    "physical therapy", "massage", "catering company",
]

# Funding signal — article must contain at least one of these.
FUNDING_KEYWORDS = [
    "raises", "raised", "funding", "investment", "series a", "series b",
    "series c", "series d", "seed", "pre-seed", "venture", "capital",
    "million", "miljon", "miljard", "finansiering", "investering",
    "runda", "round", "backed", "secures", "secured", "closes", "closed",
    "lands", "receives", "grant", "led by", "valuation",
]

SWEDEN_KEYWORDS = [
    "sweden", "swedish", "stockholm", "gothenburg", "goteborg", "malmo",
    "nordic", "scandinavia", "sverige", "svensk", "linkoping", "uppsala",
    "vasteras", "orebro", "helsingborg",
]

# Domain tags shown in the email table
DOMAIN_TAGS = {
    "AI/ML":       ["ai", "machine learning", "deep learning", "llm", "nlp", "computer vision"],
    "Data":        ["data science", "data platform", "analytics", "big data", "data engineer"],
    "Fintech":     ["fintech", "trading", "quantitative", "quant", "insurtech", "wealthtech"],
    "SaaS/Cloud":  ["saas", "cloud", "software", "platform", "api"],
    "Cybersec":    ["cybersecurity", "security", "infosec"],
    "HealthTech":  ["healthtech", "medtech", "digital health", "biotech"],
    "CleanTech":   ["cleantech", "climatetech", "sustainability"],
    "Robotics":    ["robotics", "automation", "iot"],
}

TAG_COLOURS = {
    "AI/ML":      ("#dbeafe", "#1d4ed8"),
    "Data":       ("#dcfce7", "#15803d"),
    "Fintech":    ("#fef9c3", "#854d0e"),
    "SaaS/Cloud": ("#f3e8ff", "#7e22ce"),
    "Cybersec":   ("#fee2e2", "#991b1b"),
    "HealthTech": ("#ffedd5", "#c2410c"),
    "CleanTech":  ("#d1fae5", "#065f46"),
    "Robotics":   ("#e0f2fe", "#0369a1"),
}


# ── Scrapers ──────────────────────────────────────────────────────────────────

def fetch_google_news(query: str) -> list[dict]:
    url = f"https://news.google.com/rss/search?q={query}&hl=en-SE&gl=SE&ceid=SE:en"
    try:
        feed = feedparser.parse(url)
        articles = []
        for entry in feed.entries[:20]:
            summary = BeautifulSoup(entry.get("summary", ""), "html.parser").get_text()
            articles.append({
                "title":     entry.get("title", "").strip(),
                "link":      entry.get("link", "#"),
                "published": entry.get("published_parsed", None),
                "source":    entry.get("source", {}).get("title", "Google News"),
                "summary":   summary[:500],
            })
        return articles
    except Exception as exc:
        print(f"[Google News] '{query}': {exc}")
        return []


def fetch_rss(url: str, source_name: str) -> list[dict]:
    try:
        feed = feedparser.parse(url)
        articles = []
        for entry in feed.entries[:25]:
            summary = BeautifulSoup(entry.get("summary", ""), "html.parser").get_text()
            articles.append({
                "title":     entry.get("title", "").strip(),
                "link":      entry.get("link", "#"),
                "published": entry.get("published_parsed", None),
                "source":    source_name,
                "summary":   summary[:500],
            })
        return articles
    except Exception as exc:
        print(f"[{source_name}] {exc}")
        return []


# ── Date helpers ──────────────────────────────────────────────────────────────

def to_datetime(parsed_time) -> datetime | None:
    if parsed_time is None:
        return None
    try:
        return datetime(*parsed_time[:6], tzinfo=timezone.utc)
    except Exception:
        return None


def age_days(pub: datetime | None) -> float:
    if pub is None:
        return 999
    now = datetime.now(timezone.utc)
    return (now - pub).total_seconds() / 86400


def format_date(pub: datetime | None) -> str:
    if pub is None:
        return "Unknown date"
    return pub.strftime("%-d %b %Y")


# ── Filters ───────────────────────────────────────────────────────────────────

def passes_filters(article: dict) -> bool:
    text = (article["title"] + " " + article["summary"]).lower()
    pub  = to_datetime(article["published"])

    if age_days(pub) > MAX_AGE_DAYS:
        return False
    if not any(kw in text for kw in SWEDEN_KEYWORDS):
        return False
    if not any(kw in text for kw in FUNDING_KEYWORDS):
        return False
    if not any(kw in text for kw in TECH_KEYWORDS):
        return False
    if any(kw in text for kw in EXCLUDE_KEYWORDS):
        return False
    return True


def get_domain_tags(article: dict) -> list[str]:
    text = (article["title"] + " " + article["summary"]).lower()
    return [tag for tag, keywords in DOMAIN_TAGS.items()
            if any(k in text for k in keywords)]


# ── Company name extraction & LinkedIn URL ────────────────────────────────────

def extract_company_name(title: str) -> str:
    match = re.search(
        r"^(.*?)\s+(?:raises?|secures?|gets?|receives?|closes?|lands?|announces?|backs?|backed)",
        title,
        re.IGNORECASE,
    )
    if match:
        candidate = match.group(1).strip()
        candidate = re.sub(
            r"^(Stockholm[\-\s]based|Sweden[\-\s]based|Swedish|Nordic|Gothenburg[\-\s]based"
            r"|AI\s+startup|Tech\s+startup|Fintech\s+startup|SaaS\s+startup"
            r"|startup|company|firm)\s+",
            "",
            candidate,
            flags=re.IGNORECASE,
        ).strip()
        if candidate and len(candidate) < 60:
            return candidate

    tokens = title.split()
    name_parts = []
    for tok in tokens[:4]:
        clean = re.sub(r"[^\w\s\-&\.]", "", tok)
        if clean and clean[0].isupper():
            name_parts.append(clean)
        else:
            break
    return " ".join(name_parts) if name_parts else title[:40]


def linkedin_url(company_name: str) -> str:
    return f"https://www.linkedin.com/search/results/companies/?keywords={quote(company_name)}"


# ── Clustering ────────────────────────────────────────────────────────────────

def cluster_by_company(articles: list[dict]) -> list[dict]:
    SOURCE_PRIORITY = {"Breakit": 0, "Dagens industri Digital": 1, "TechCrunch": 2}
    clusters: dict[str, list[dict]] = defaultdict(list)
    for a in articles:
        key = a["company"].lower().strip()
        clusters[key].append(a)

    result = []
    for key, group in clusters.items():
        group.sort(key=lambda x: SOURCE_PRIORITY.get(x["source"], 99))
        best = group[0].copy()
        best["coverage"] = len(group)
        result.append(best)
    return result


# ── Email builder ─────────────────────────────────────────────────────────────

def build_html(articles: list[dict]) -> str:
    today    = datetime.now().strftime("%A, %d %B %Y")
    count    = len(articles)

    rows = ""
    for a in articles:
        pub      = to_datetime(a["published"])
        days_old = age_days(pub)

        fresh_badge = (
            ' <span style="background:#22c55e;color:#fff;font-size:10px;'
            'padding:1px 6px;border-radius:8px;vertical-align:middle;'
            'font-weight:bold;">NEW</span>'
            if days_old <= FRESH_DAYS else ""
        )

        coverage_note = (
            f' <span style="color:#9ca3af;font-size:11px;">'
            f'({a["coverage"]} sources)</span>'
            if a.get("coverage", 1) > 1 else ""
        )

        tags_html = ""
        for t in a.get("tags", []):
            bg, fg = TAG_COLOURS.get(t, ("#f3f4f6", "#374151"))
            tags_html += (
                f'<span style="background:{bg};color:{fg};font-size:10px;'
                f'padding:2px 7px;border-radius:8px;white-space:nowrap;'
                f'margin-right:3px;font-weight:600;">{t}</span>'
            )
        if not tags_html:
            tags_html = '<span style="color:#d1d5db;font-size:11px;">—</span>'

        rows += f"""
        <tr style="border-bottom:1px solid #f3f4f6;">
          <td style="padding:11px 14px;vertical-align:top;min-width:130px;">
            <a href="{a['linkedin_url']}"
               target="_blank"
               title="Search {a['company']} on LinkedIn"
               style="color:#1d4ed8;font-weight:700;text-decoration:none;
                      border-bottom:1px solid #bfdbfe;">{a['company']}</a>
            {fresh_badge}{coverage_note}
          </td>
          <td style="padding:11px 14px;vertical-align:top;min-width:110px;">{tags_html}</td>
          <td style="padding:11px 14px;vertical-align:top;font-size:13px;">
            <a href="{a['link']}" target="_blank"
               style="color:#374151;text-decoration:none;">{a['title']}</a>
          </td>
          <td style="padding:11px 14px;vertical-align:top;font-size:12px;
                     color:#6b7280;white-space:nowrap;">{a['source']}</td>
          <td style="padding:11px 14px;vertical-align:top;font-size:12px;
                     color:#6b7280;white-space:nowrap;">{format_date(pub)}</td>
        </tr>"""

    no_results = """<tr><td colspan="5"
        style="padding:32px;text-align:center;color:#9ca3af;font-size:14px;">
        No new funding news matching your criteria today — check back tomorrow!
        </td></tr>""" if not articles else ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
</head>
<body style="margin:0;padding:20px;background:#f3f4f6;font-family:Arial,sans-serif;">
<div style="max-width:960px;margin:auto;background:#fff;border-radius:12px;
            overflow:hidden;box-shadow:0 2px 16px rgba(0,0,0,.08);">

  <!-- Header -->
  <div style="background:#0d1b2a;color:#fff;padding:28px 32px;">
    <h1 style="margin:0 0 6px;font-size:22px;letter-spacing:-.3px;">
      🇸🇪 Sweden Startup Funding Digest
    </h1>
    <p style="margin:0;opacity:.65;font-size:13px;">
      {today} &nbsp;·&nbsp; {count} unique compan{'ies' if count != 1 else 'y'}
      &nbsp;·&nbsp; Filtered for Data · AI/ML · Fintech · SaaS · CleanTech
    </p>
  </div>

  <!-- Tip bar -->
  <div style="background:#eff6ff;border-bottom:1px solid #dbeafe;
              padding:10px 32px;font-size:12px;color:#1d4ed8;">
    💡 <strong>Click any company name</strong> to search it on LinkedIn and find
    hiring managers, founders, and open roles.
  </div>

  <!-- Table -->
  <div style="overflow-x:auto;">
    <table style="width:100%;border-collapse:collapse;font-size:14px;">
      <thead>
        <tr style="background:#f9fafb;border-bottom:2px solid #e5e7eb;">
          <th style="padding:10px 14px;text-align:left;color:#6b7280;font-size:11px;
                     text-transform:uppercase;letter-spacing:.06em;">Company</th>
          <th style="padding:10px 14px;text-align:left;color:#6b7280;font-size:11px;
                     text-transform:uppercase;letter-spacing:.06em;">Domain</th>
          <th style="padding:10px 14px;text-align:left;color:#6b7280;font-size:11px;
                     text-transform:uppercase;letter-spacing:.06em;">Headline</th>
          <th style="padding:10px 14px;text-align:left;color:#6b7280;font-size:11px;
                     text-transform:uppercase;letter-spacing:.06em;">Source</th>
          <th style="padding:10px 14px;text-align:left;color:#6b7280;font-size:11px;
                     text-transform:uppercase;letter-spacing:.06em;">Date</th>
        </tr>
      </thead>
      <tbody>
        {rows or no_results}
      </tbody>
    </table>
  </div>

  <!-- Footer -->
  <div style="background:#f9fafb;padding:16px 32px;font-size:12px;
              color:#9ca3af;border-top:1px solid #f3f4f6;">
    🤖 Sweden Startup Funding Agent v2 &nbsp;·&nbsp;
    Target roles: Data Scientist · ML Engineer · Data Engineer · Quant Analyst
  </div>

</div>
</body>
</html>"""


# ── Email sender ──────────────────────────────────────────────────────────────

def send_email(html: str, count: int) -> None:
    subject = (
        f"🇸🇪 Sweden Startup Digest — {count} compan{'ies' if count != 1 else 'y'} "
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
    print(f"✅ Email sent — {count} companies — {datetime.now().strftime('%H:%M UTC')}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    print(f"🚀 Agent v2 — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")

    raw: list[dict] = []

    for query in [
        "Sweden+startup+funding",
        "Swedish+startup+raises+million",
        "Stockholm+startup+investment+round",
        "Nordic+AI+startup+funding",
        "Sverige+tech+startup+finansiering",
        "Sweden+fintech+raises",
        "Swedish+SaaS+investment",
        "Sweden+deeptech+funding",
    ]:
        raw.extend(fetch_google_news(query))

    raw.extend(fetch_rss("https://www.breakit.se/feed/articles",  "Breakit"))
    raw.extend(fetch_rss("https://digital.di.se/rss",             "Dagens industri Digital"))

    print(f"📥 {len(raw)} raw articles fetched")

    filtered = [a for a in raw if passes_filters(a)]
    print(f"🔍 {len(filtered)} after domain/date/funding filters")

    for a in filtered:
        a["company"]      = extract_company_name(a["title"])
        a["linkedin_url"] = linkedin_url(a["company"])
        a["tags"]         = get_domain_tags(a)

    clustered = cluster_by_company(filtered)
    clustered.sort(key=lambda x: age_days(to_datetime(x["published"])))
    final = clustered[:30]

    print(f"📰 {len(final)} unique companies after clustering")

    html = build_html(final)
    send_email(html, len(final))


if __name__ == "__main__":
    main()
