"""
Sweden Startup Funding Agent  — v4
------------------------------------
Daily digest of Swedish company funding news for job seekers targeting
Data Scientist, ML Engineer, Data Engineer, and Quant Analyst roles.

v4 changes:
- Dropped Swedish-language sources (Breakit, DI Digital)
- Added English specialist sources: EU-Startups, ArcticStartup, Silicon Canals, Tech.eu
- Removed mandatory TECH_KEYWORDS filter — any funded Swedish company is relevant
- Added Funding Amount + Round Type extraction (new email column)
- Domain tags kept as informational labels only, not filters
- Expanded infrastructure exclusions
"""

import os
import re
import smtplib
import feedparser
from bs4 import BeautifulSoup
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timezone
from urllib.parse import quote
from collections import defaultdict

# ── Configuration ─────────────────────────────────────────────────────────────
GMAIL_ADDRESS      = os.environ["GMAIL_ADDRESS"]
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]
RECIPIENT_EMAIL    = GMAIL_ADDRESS

MAX_AGE_DAYS = 90
FRESH_DAYS   = 3

# ── Keyword filters ───────────────────────────────────────────────────────────

FUNDING_KEYWORDS = [
    "raises", "raised", "funding", "investment", "series a", "series b",
    "series c", "series d", "seed", "pre-seed", "venture", "capital",
    "million", "secures", "secured", "closes", "closed", "backed",
    "lands", "receives", "grant", "valuation", "round",
]

# Geographic — must contain at least one (strict, no "nordic" alone)
SWEDEN_KEYWORDS = [
    "sweden", "swedish", "stockholm", "gothenburg", "goteborg", "malmo",
    "sverige", "svensk", "linkoping", "uppsala", "vasteras", "orebro",
    "helsingborg", "lund", "umea", "solleftea", "scandinavia",
]

# Physical infrastructure — not relevant for hiring data roles
EXCLUDE_CONTENT_KEYWORDS = [
    "plumbing", "carpentry", "dental clinic", "dentist", "restaurant chain",
    "hair salon", "barbershop", "physical therapy", "massage", "catering company",
    "colocation", "hyperscaler", "megawatt", " mw ", "data center campus",
    "construction permit", "grid connection",
]

# ── Article quality filters ───────────────────────────────────────────────────

BAD_TITLE_PATTERNS = [
    r"^top\s+\d+",
    r"\d+\s+(?:startups?|companies)\s+(?:to|you|that)",
    r"venture capital firms",
    r"\bvc\s+firms\b",
    r"slashes?\s+valuation",
    r"cuts?\s+valuation",
    r"writes?\s+down",
    r"(?:micro\s+)?fund\s+to\s+back",
    r"launches?\s+.{0,30}\bfund\b",
    r"raises?\s+.{0,20}\b(?:third|second|fourth|new)\s+fund\b",
    r"\binvestor\b.{0,30}\braises?\b",
    r"new\s+(?:micro\s+)?fund",
    r"nordic[\-\s]focused\s+fund",
    r"(?:annual|weekly|monthly)\s+(?:roundup|digest|report)",
    r"startups?\s+(?:to\s+watch|you\s+should\s+know)",
    r"^(?:swedish|nordic)\s+(?:ai[\-\s])?native\s+startups?",
]

# ── Domain tags (informational only — not used as filters) ────────────────────
DOMAIN_TAGS = {
    "AI/ML":        ["artificial intelligence", " ai ", "machine learning",
                     "deep learning", "llm", "nlp", "computer vision", "generative ai"],
    "Data":         ["data science", "data platform", "analytics", "big data",
                     "data engineer", "data infrastructure"],
    "Fintech":      ["fintech", "financial technology", "trading", "quantitative",
                     "insurtech", "wealthtech", "regtech", "neobank", "payments"],
    "SaaS/Cloud":   ["saas", " cloud ", "software platform", " api ", "developer tools"],
    "Cybersec":     ["cybersecurity", "cyber security", "infosec"],
    "HealthTech":   ["healthtech", "medtech", "digital health", "biotech", "life science"],
    "CleanTech":    ["cleantech", "climatetech", "clean energy", "renewable", "sustainability"],
    "Robotics":     ["robotics", "autonomous", " iot ", "internet of things"],
    "DeepTech":     ["deeptech", "quantum", "semiconductor", "photonics", "chip"],
    "Gaming":       ["gaming", "esports", "game studio", "betting", "igaming"],
    "Logistics":    ["logistics", "supply chain", "last mile", "fleet"],
    "Retail/Food":  ["e-commerce", "marketplace", "grocery", "food tech", "retail tech"],
    "Energy":       ["energy tech", "grid", "power", "ev charging", "battery"],
}

TAG_COLOURS = {
    "AI/ML":        ("#dbeafe", "#1d4ed8"),
    "Data":         ("#dcfce7", "#15803d"),
    "Fintech":      ("#fef9c3", "#854d0e"),
    "SaaS/Cloud":   ("#f3e8ff", "#7e22ce"),
    "Cybersec":     ("#fee2e2", "#991b1b"),
    "HealthTech":   ("#ffedd5", "#c2410c"),
    "CleanTech":    ("#d1fae5", "#065f46"),
    "Robotics":     ("#e0f2fe", "#0369a1"),
    "DeepTech":     ("#fce7f3", "#9d174d"),
    "Gaming":       ("#fef3c7", "#92400e"),
    "Logistics":    ("#f0fdf4", "#166534"),
    "Retail/Food":  ("#fff7ed", "#9a3412"),
    "Energy":       ("#ecfdf5", "#065f46"),
}

# ── Sources ───────────────────────────────────────────────────────────────────

RSS_SOURCES = [
    # Specialist Nordic / European startup outlets
    ("https://www.eu-startups.com/feed/",    "EU-Startups"),
    ("https://arcticstartup.com/feed/",      "ArcticStartup"),
    ("https://siliconcanals.com/feed/",      "Silicon Canals"),
    ("https://tech.eu/feed/",                "Tech.eu"),
    ("https://sifted.eu/feed",               "Sifted"),
]

GOOGLE_NEWS_QUERIES = [
    "Sweden+startup+funding",
    "Swedish+startup+raises+million",
    "Stockholm+startup+investment+round",
    "Swedish+company+series+A+B+C+funding",
    "Sweden+startup+seed+investment",
    "Swedish+startup+secures+funding",
    "Nordic+startup+Sweden+raises",
]

SOURCE_PRIORITY = {
    "EU-Startups":    0,
    "ArcticStartup":  1,
    "Silicon Canals": 2,
    "Sifted":         3,
    "Tech.eu":        4,
    "TechCrunch":     5,
}


# ── Scrapers ──────────────────────────────────────────────────────────────────

def fetch_google_news(query: str) -> list[dict]:
    url = f"https://news.google.com/rss/search?q={query}&hl=en-SE&gl=SE&ceid=SE:en"
    try:
        feed = feedparser.parse(url)
        results = []
        for entry in feed.entries[:20]:
            summary = BeautifulSoup(entry.get("summary", ""), "html.parser").get_text()
            results.append({
                "title":     entry.get("title", "").strip(),
                "link":      entry.get("link", "#"),
                "published": entry.get("published_parsed", None),
                "source":    entry.get("source", {}).get("title", "Google News"),
                "summary":   summary[:600],
            })
        return results
    except Exception as exc:
        print(f"[Google News] '{query}': {exc}")
        return []


def fetch_rss(url: str, source_name: str) -> list[dict]:
    try:
        feed = feedparser.parse(url)
        results = []
        for entry in feed.entries[:30]:
            summary = BeautifulSoup(entry.get("summary", ""), "html.parser").get_text()
            results.append({
                "title":     entry.get("title", "").strip(),
                "link":      entry.get("link", "#"),
                "published": entry.get("published_parsed", None),
                "source":    source_name,
                "summary":   summary[:600],
            })
        return results
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
    return (datetime.now(timezone.utc) - pub).total_seconds() / 86400


def format_date(pub: datetime | None) -> str:
    if pub is None:
        return "Unknown"
    return pub.strftime("%-d %b %Y")


# ── Filters ───────────────────────────────────────────────────────────────────

def is_bad_title(title: str) -> bool:
    tl = title.lower()
    return any(re.search(p, tl) for p in BAD_TITLE_PATTERNS)


def is_norway_only(article: dict) -> bool:
    text = (article["title"] + " " + article["summary"]).lower()
    norway = ["oslo-based", "oslo based", "norwegian startup", "norway-based"]
    return any(s in text for s in norway) and not any(k in text for k in SWEDEN_KEYWORDS)


def passes_filters(article: dict) -> bool:
    text = (article["title"] + " " + article["summary"]).lower()
    pub  = to_datetime(article["published"])

    if age_days(pub) > MAX_AGE_DAYS:
        return False
    if not any(kw in text for kw in SWEDEN_KEYWORDS):
        return False
    if not any(kw in text for kw in FUNDING_KEYWORDS):
        return False
    if any(kw in text for kw in EXCLUDE_CONTENT_KEYWORDS):
        return False
    if is_bad_title(article["title"]):
        return False
    if is_norway_only(article):
        return False
    return True


# ── Funding extraction ────────────────────────────────────────────────────────

_AMOUNT_RE = re.compile(
    r"([€£\$])\s*([\d]+(?:[.,]\d+)?)\s*(k|m|mn|million|bn|billion)?"
    r"|"
    r"([\d]+(?:[.,]\d+)?)\s*(million|billion|m\b|bn\b|k\b)\s*(?:euro[s]?|dollar[s]?|usd|sek|kr)?",
    re.IGNORECASE,
)

_ROUND_RE = re.compile(
    r"\b(pre[\-\s]?seed|seed|series\s+[a-e]|growth\s+round|bridge\s+round|ipo|crowdfunding)\b",
    re.IGNORECASE,
)


def extract_funding_info(title: str, summary: str) -> tuple[str, str]:
    """Return (amount_str, round_str) e.g. ('€5M', 'Series A')."""
    text = title + " " + summary

    # Round type
    round_str = ""
    rm = _ROUND_RE.search(text)
    if rm:
        raw = rm.group(0).strip()
        # Normalise capitalisation
        raw = re.sub(r"series\s+([a-e])", lambda m: f"Series {m.group(1).upper()}", raw, flags=re.IGNORECASE)
        raw = re.sub(r"pre[\-\s]?seed", "Pre-Seed", raw, flags=re.IGNORECASE)
        round_str = raw.title() if raw.lower() not in ("ipo",) else "IPO"

    # Amount
    amount_str = ""
    am = _AMOUNT_RE.search(text)
    if am:
        try:
            if am.group(1):                          # symbol-first: €5M
                sym    = am.group(1)
                num    = float(am.group(2).replace(",", "."))
                unit   = (am.group(3) or "").lower()
            else:                                    # number-first: 5 million euros
                sym    = "€"
                num    = float(am.group(4).replace(",", "."))
                unit   = (am.group(5) or "").lower()

            if unit in ("bn", "billion"):
                amount_str = f"{sym}{num:g}B"
            elif unit in ("m", "mn", "million"):
                amount_str = f"{sym}{num:g}M"
            elif unit == "k":
                amount_str = f"{sym}{int(num)}K"
            else:
                amount_str = f"{sym}{num:g}"
        except (ValueError, IndexError):
            pass

    return amount_str, round_str


# ── Domain tags ───────────────────────────────────────────────────────────────

def get_domain_tags(article: dict) -> list[str]:
    text = " " + (article["title"] + " " + article["summary"]).lower() + " "
    return [tag for tag, kws in DOMAIN_TAGS.items() if any(k in text for k in kws)]


# ── Company name extraction ───────────────────────────────────────────────────

_PREFIX_RE = re.compile(
    r"""^(?:
        sweden'?s?\s+             |
        swedish\s+                |
        stockholm[\-\s]based\s+   |
        gothenburg[\-\s]based\s+  |
        nordic\s+                 |
        (?:ai|ml|data|tech|saas|fintech|deeptech|biotech|medtech|
           cleantech|healthtech|quantum|crypto|gaming|energy)\s+
        (?:startup|company|firm|scaleup|unicorn|platform)\s+  |
        (?:startup|company|firm|scaleup)\s+                   |
        [\w\-]+[\-\s](?:based|native|first)\s+
    )+""",
    re.IGNORECASE | re.VERBOSE,
)

_DESC_RE = re.compile(
    r"^(?:(?:ai|ml|data|b2b|b2c|saas|tech|green|digital|smart|autonomous|"
    r"cloud|api|deep|advanced|innovative|leading|open[\-\s]source|next[\-\s]gen)\s+)*",
    re.IGNORECASE,
)


def extract_company_name(title: str) -> str:
    match = re.search(
        r"^(.*?)\s+(?:raises?|secures?|gets?|receives?|closes?|lands?|"
        r"fetches?|announces?|backs?|backed|completes?|confirms?)",
        title, re.IGNORECASE,
    )
    candidate = match.group(1).strip() if match else title[:60]
    candidate = _PREFIX_RE.sub("", candidate).strip()
    candidate = _DESC_RE.sub("", candidate).strip()

    words = candidate.split()
    if len(words) > 4:
        cap = [w for w in words if w and w[0].isupper()]
        candidate = " ".join(cap[-2:]) if cap else " ".join(words[-2:])

    candidate = candidate.strip(" -–,.")
    return candidate if candidate and len(candidate) > 1 else title[:40]


def normalize_for_cluster(name: str) -> str:
    n = _PREFIX_RE.sub("", name).strip()
    n = _DESC_RE.sub("", n).strip()
    n = re.sub(r"'s$", "", n).strip(" -–,.'\"")
    return n.lower()


def linkedin_url(company_name: str) -> str:
    return f"https://www.linkedin.com/search/results/companies/?keywords={quote(company_name)}"


# ── Clustering ────────────────────────────────────────────────────────────────

def cluster_by_company(articles: list[dict]) -> list[dict]:
    clusters: dict[str, list[dict]] = defaultdict(list)
    for a in articles:
        clusters[normalize_for_cluster(a["company"])].append(a)

    result = []
    for group in clusters.values():
        group.sort(key=lambda x: SOURCE_PRIORITY.get(x["source"], 99))
        best = group[0].copy()
        best["coverage"] = len(group)
        result.append(best)
    return result


# ── Email builder ─────────────────────────────────────────────────────────────

def build_html(articles: list[dict]) -> str:
    today = datetime.now().strftime("%A, %d %B %Y")
    count = len(articles)

    rows = ""
    for a in articles:
        pub      = to_datetime(a["published"])
        days_old = age_days(pub)

        fresh_badge = (
            ' <span style="background:#22c55e;color:#fff;font-size:10px;'
            'padding:1px 6px;border-radius:8px;font-weight:bold;'
            'vertical-align:middle;margin-left:4px;">NEW</span>'
            if days_old <= FRESH_DAYS else ""
        )
        coverage_note = (
            f' <span style="color:#9ca3af;font-size:11px;">({a["coverage"]} sources)</span>'
            if a.get("coverage", 1) > 1 else ""
        )

        # Funding column
        amount, rnd = a.get("amount", ""), a.get("round", "")
        funding_parts = []
        if rnd:
            funding_parts.append(
                f'<span style="font-weight:600;color:#374151;">{rnd}</span>'
            )
        if amount:
            funding_parts.append(
                f'<span style="color:#059669;font-weight:700;">{amount}</span>'
            )
        funding_html = (
            ' <span style="color:#d1d5db;font-size:11px;">·</span> '.join(funding_parts)
            if funding_parts else
            '<span style="color:#d1d5db;font-size:11px;">—</span>'
        )

        # Domain tags
        tags_html = ""
        for t in a.get("tags", []):
            bg, fg = TAG_COLOURS.get(t, ("#f3f4f6", "#374151"))
            tags_html += (
                f'<span style="background:{bg};color:{fg};font-size:10px;'
                f'padding:2px 6px;border-radius:7px;white-space:nowrap;'
                f'margin-right:3px;margin-bottom:2px;font-weight:600;'
                f'display:inline-block;">{t}</span>'
            )
        if not tags_html:
            tags_html = '<span style="color:#e5e7eb;font-size:11px;">—</span>'

        li_url = a.get("linkedin_url", "#")

        rows += f"""
        <tr style="border-bottom:1px solid #f3f4f6;">
          <td style="padding:11px 14px;vertical-align:top;min-width:120px;">
            <span style="font-weight:700;color:#111827;">{a['company']}</span>
            {fresh_badge}{coverage_note}
            <br>
            <a href="{li_url}" target="_blank"
               style="font-size:11px;color:#6b7280;text-decoration:none;">
              🔗 LinkedIn search
            </a>
          </td>
          <td style="padding:11px 14px;vertical-align:top;min-width:110px;white-space:nowrap;">
            {funding_html}
          </td>
          <td style="padding:11px 14px;vertical-align:top;min-width:110px;">
            {tags_html}
          </td>
          <td style="padding:11px 14px;vertical-align:top;font-size:13px;">
            <a href="{a['link']}" target="_blank"
               style="color:#374151;text-decoration:none;">{a['title']}</a>
          </td>
          <td style="padding:11px 14px;vertical-align:top;font-size:12px;
                     color:#6b7280;white-space:nowrap;">{a['source']}</td>
          <td style="padding:11px 14px;vertical-align:top;font-size:12px;
                     color:#6b7280;white-space:nowrap;">{format_date(pub)}</td>
        </tr>"""

    no_results = """<tr><td colspan="6"
        style="padding:32px;text-align:center;color:#9ca3af;font-size:14px;">
        No new funding news matching your criteria today — check back tomorrow!
        </td></tr>""" if not articles else ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:20px;background:#f3f4f6;font-family:Arial,sans-serif;">
<div style="max-width:1060px;margin:auto;background:#fff;border-radius:12px;
            overflow:hidden;box-shadow:0 2px 16px rgba(0,0,0,.08);">

  <div style="background:#0d1b2a;color:#fff;padding:28px 32px;">
    <h1 style="margin:0 0 6px;font-size:22px;letter-spacing:-.3px;">
      🇸🇪 Sweden Startup Funding Digest
    </h1>
    <p style="margin:0;opacity:.65;font-size:13px;">
      {today} &nbsp;·&nbsp; {count} unique compan{'ies' if count != 1 else 'y'}
      &nbsp;·&nbsp; Any domain · All roles: Data · ML · Engineering · Quant
    </p>
  </div>

  <div style="background:#eff6ff;border-bottom:1px solid #dbeafe;
              padding:10px 32px;font-size:12px;color:#1d4ed8;">
    💡 Company name shows the extracted name &nbsp;·&nbsp;
    Click <strong>LinkedIn search</strong> below each name to find founders and hiring managers
  </div>

  <div style="overflow-x:auto;">
    <table style="width:100%;border-collapse:collapse;font-size:14px;">
      <thead>
        <tr style="background:#f9fafb;border-bottom:2px solid #e5e7eb;">
          <th style="padding:10px 14px;text-align:left;color:#6b7280;font-size:11px;
                     text-transform:uppercase;letter-spacing:.06em;white-space:nowrap;">Company</th>
          <th style="padding:10px 14px;text-align:left;color:#6b7280;font-size:11px;
                     text-transform:uppercase;letter-spacing:.06em;white-space:nowrap;">Round · Amount</th>
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
      <tbody>{rows or no_results}</tbody>
    </table>
  </div>

  <div style="background:#f9fafb;padding:16px 32px;font-size:12px;
              color:#9ca3af;border-top:1px solid #f3f4f6;">
    🤖 Sweden Startup Funding Agent v4 &nbsp;·&nbsp;
    Sources: EU-Startups · ArcticStartup · Silicon Canals · Sifted · Tech.eu · Google News
  </div>
</div>
</body></html>"""


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
    print(f"🚀 Agent v4 — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")

    raw: list[dict] = []

    # Specialist English-language RSS feeds
    for url, name in RSS_SOURCES:
        articles = fetch_rss(url, name)
        print(f"  [{name}] {len(articles)} articles")
        raw.extend(articles)

    # Google News as a broad catch-all
    for query in GOOGLE_NEWS_QUERIES:
        raw.extend(fetch_google_news(query))

    print(f"📥 {len(raw)} raw articles")

    filtered = [a for a in raw if passes_filters(a)]
    print(f"🔍 {len(filtered)} after filters")

    # Enrich
    for a in filtered:
        a["company"]      = extract_company_name(a["title"])
        a["linkedin_url"] = linkedin_url(a["company"])
        a["tags"]         = get_domain_tags(a)
        a["amount"], a["round"] = extract_funding_info(a["title"], a["summary"])

    clustered = cluster_by_company(filtered)
    clustered.sort(key=lambda x: age_days(to_datetime(x["published"])))
    final = clustered[:30]

    print(f"📰 {len(final)} unique companies in digest")

    html = build_html(final)
    send_email(html, len(final))


if __name__ == "__main__":
    main()
