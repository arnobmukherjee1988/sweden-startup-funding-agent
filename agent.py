"""
Nordic Startup Funding Agent  — v9
-----------------------------------
Daily digest of Swedish and Danish startup funding news for job seekers targeting
Data Scientist, ML Engineer, Data Engineer, and Quant Analyst roles.

v9 changes over v8:
- Switched model from gemini-2.0-flash (0 free-tier quota) to gemini-3.1-flash-lite-preview
  (15 RPM / 500 RPD on free tier — sufficient for 50 articles/day)
- Updated log/email label to reflect new model name
"""

import os
import re
import json
import time
import imaplib
import smtplib
import requests
import feedparser
import google.genai as genai
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

MAX_AGE_DAYS       = 90
FRESH_DAYS         = 3
CLEANUP_DAYS       = 10   # delete digest emails older than this many days
MAX_GEMINI_ARTICLES = 50  # hard cap on articles sent to Gemini — keeps runtime ~5 min
FEED_TIMEOUT        = 15  # seconds before giving up on a slow RSS/Google News feed

# ── Gemini setup ──────────────────────────────────────────────────────────────
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

if GEMINI_API_KEY:
    _gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    print("✅ Gemini 3.1 Flash Lite initialised (google.genai SDK)")
else:
    _gemini_client = None
    print("⚠️  GEMINI_API_KEY not set — falling back to regex for all decisions")

# ── Keyword filters ───────────────────────────────────────────────────────────

FUNDING_KEYWORDS = [
    "raises", "raised", "funding", "investment", "series a", "series b",
    "series c", "series d", "seed", "pre-seed", "venture", "capital",
    "million", "secures", "secured", "closes", "closed", "backed",
    "lands", "receives", "grant", "valuation", "round", "emerges from stealth",
    "powers up with", "bags", "attracts", "nets",
]

SWEDEN_KEYWORDS = [
    "sweden", "swedish", "stockholm", "gothenburg", "goteborg", "malmo",
    "sverige", "svensk", "linkoping", "uppsala", "vasteras", "orebro",
    "helsingborg", "lund", "umea", "solleftea", "scandinavia",
]

DENMARK_KEYWORDS = [
    "denmark", "danish", "copenhagen", "københavn", "kobenhavn",
    "aarhus", "odense", "aalborg", "frederiksberg", "esbjerg",
    "randers", "vejle", "kolding", "horsens", "dansk",
]

EXCLUDE_CONTENT_KEYWORDS = [
    "plumbing", "carpentry", "dental clinic", "dentist", "restaurant chain",
    "hair salon", "barbershop", "physical therapy", "massage", "catering company",
    "colocation", "hyperscaler", "megawatt", " mw ", "data center campus",
    "construction permit", "grid connection",
    # Non-startup noise that slips through on regex-fallback runs
    "cdc awards", "centers for disease control", "hepatitis", "vaccine study",
    "fda official", "researchers with ties",
]

# ── Regex fallbacks (used when Gemini is unavailable) ─────────────────────────

BAD_TITLE_PATTERNS = [
    r"^top\s+\d+",
    r"\d+\s+(?:startups?|companies)\s+(?:to|you|that)",
    r"venture capital firms",
    r"\bvc\s+firms\b",
    r"slashes?\s+valuation",
    r"cuts?\s+valuation",
    r"writes?\s+down",
    r"triples?\s+valuation",
    r"doubles?\s+valuation",
    r"quadruples?\s+valuation",
    r"reaches?\s+valuation\s+of",
    r"hits?\s+valuation\s+of",
    r"valued\s+at\s+\$[\d]",
    r"(?:micro\s+)?fund\s+to\s+back",
    r"launches?\s+.{0,30}\bfund\b",
    r"raises?\s+.{0,20}\b(?:third|second|fourth|new)\s+fund\b",
    r"\binvestor\b.{0,30}\braises?\b",
    r"new\s+(?:micro\s+)?fund",
    r"nordic[\-\s]focused\s+fund",
    r"(?:annual|weekly|monthly)\s+(?:roundup|digest|report)",
    r"startups?\s+(?:to\s+watch|you\s+should\s+know)",
    r"^(?:swedish|nordic|danish)\s+(?:ai[\-\s])?native\s+startups?",
    # Listicles and aggregator pages (e.g. Tracxn "latest funding rounds, trends")
    r"latest\s+funding\s+rounds?,?\s+trends?",
    r"funding\s+rounds?\s+and\s+news",
    r"startups?\s+in\s+(?:sweden|denmark|nordic).{0,30}(?:tracxn|crunchbase|dealroom)",
    # Cohort / accelerator batch announcements (multiple companies, not one raise)
    r"(?:\d+|five|six|seven|eight|nine|ten)\s+startups?\s+(?:enter|join|selected|graduate)",
    r"\bcohort\b",
    r"accelerator\s+(?:batch|cohort|program)",
]

# ── Domain tags (informational only) ─────────────────────────────────────────
DOMAIN_TAGS = {
    "AI/ML":        ["artificial intelligence", " ai ", "machine learning",
                     "deep learning", "llm", "nlp", "computer vision",
                     "generative ai", "ai agent", "ai-powered", "ai-native"],
    "Data":         ["data science", "data platform", "analytics", "big data",
                     "data engineer", "data infrastructure", "revenue insights",
                     "elasticsearch", "search engine", "business intelligence"],
    "Fintech":      ["fintech", "financial technology", "trading", "quantitative",
                     "insurtech", "wealthtech", "regtech", "neobank", "payments",
                     "compliance", "accounts receivable", "financial planning"],
    "SaaS/Cloud":   ["saas", " cloud ", "software platform", " api ",
                     "developer tools", "subscription", "b2b software"],
    "Cybersec":     ["cybersecurity", "cyber security", "infosec"],
    "HealthTech":   ["healthtech", "medtech", "digital health", "biotech",
                     "life science", "longevity", "microscopy", "dna",
                     "health platform", "preventive health", "bioscience"],
    "CleanTech":    ["cleantech", "climatetech", "clean energy", "renewable",
                     "sustainability", "nuclear", "small reactor", "smr"],
    "Robotics":     ["robotics", "autonomous", " iot ", "internet of things",
                     "hardware", "microled", "photonics", "semiconductor"],
    "DeepTech":     ["deeptech", "quantum", "chip", "deep tech",
                     "volumetric", "etching", "advanced materials"],
    "Gaming":       ["gaming", "esports", "game studio", "betting", "igaming"],
    "Logistics":    ["logistics", "supply chain", "last mile", "fleet"],
    "Retail/Food":  ["e-commerce", "marketplace", "grocery", "food tech",
                     "retail tech", "designer fats", "beauty"],
    "Energy":       ["energy tech", "home energy", "ev charging", "battery",
                     "power grid", "energy costs", "energy platform",
                     "energy storage", "electricity"],
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
    "Energy":       ("#ecfdf5", "#0f766e"),
}

# ── Sources ───────────────────────────────────────────────────────────────────

RSS_SOURCES = [
    # ── Free sources (preferred) ───────────────────────────────────────────────
    ("https://arcticstartup.com/feed/",          "ArcticStartup"),       # Nordic-focused, free
    ("https://nordicstartupnews.com/feed/",       "Nordic Startup News"), # Nordic-focused, free, non-profit
    ("https://siliconcanals.com/feed/",           "Silicon Canals"),      # European, free
    ("https://tech.eu/feed/",                     "Tech.eu"),             # European, free
    ("https://techfundingnews.com/feed/",         "Tech Funding News"),   # Global, free
    ("https://techcrunch.com/feed/",              "TechCrunch"),          # Global, free
    ("https://www.finsmes.com/feed",              "FinSMEs"),             # Global VC/funding, free
    ("https://sifted.eu/feed",                    "Sifted"),              # European, free
    # ── Paywalled (kept for data coverage; link de-prioritised) ───────────────
    ("https://www.eu-startups.com/feed/",         "EU-Startups"),         # Paywalled — fallback only
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

DENMARK_GOOGLE_NEWS_QUERIES = [
    "Denmark+startup+funding",
    "Danish+startup+raises+million",
    "Copenhagen+startup+investment+round",
    "Danish+company+series+A+B+C+funding",
    "Denmark+startup+seed+investment",
    "Danish+startup+secures+funding",
]

SOURCE_PRIORITY = {
    # Lower number = preferred link shown in the email.
    # When multiple sources cover the same company, the lowest-numbered one wins.
    # EU-Startups is paywalled so it sits last; all other sources are free.
    "ArcticStartup":       0,   # Nordic-specific, free
    "Nordic Startup News": 1,   # Nordic-specific, free, non-profit
    "Silicon Canals":      2,   # European, free
    "Tech.eu":             3,   # European, free
    "Tech Funding News":   4,   # Global, free
    "TechCrunch":          5,   # Global, free
    "FinSMEs":             6,   # Global VC/funding, free
    "Sifted":              7,   # European, free
    "EU-Startups":         8,   # Paywalled — fallback only
}

# ── Scrapers ──────────────────────────────────────────────────────────────────

def fetch_google_news(query: str) -> list[dict]:
    url = f"https://news.google.com/rss/search?q={query}&hl=en-SE&gl=SE&ceid=SE:en"
    try:
        resp = requests.get(url, timeout=FEED_TIMEOUT,
                            headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
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
        resp = requests.get(url, timeout=FEED_TIMEOUT,
                            headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
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

# ── Gemini helpers ────────────────────────────────────────────────────────────

# Set to True if we detect daily quota exhaustion — skips all further Gemini calls
_gemini_quota_exhausted = False


def _gemini_call(prompt: str) -> str | None:
    """
    Single Gemini API call with automatic rate-limit backoff.

    Free-tier limits for Gemini 3.1 Flash Lite: 15 RPM / 1,500 RPD.

    Daily quota exhaustion (limit: 0 / PerDay quota ID) is detected and causes
    an immediate fallback to regex for ALL remaining articles — no 60 s waits.

    RPM rate limits (too many per minute) get one 60 s retry before giving up.
    """
    global _gemini_quota_exhausted

    if _gemini_client is None or _gemini_quota_exhausted:
        return None

    for attempt in range(2):          # 2 attempts: initial + one retry
        try:
            response = _gemini_client.models.generate_content(
                model="gemini-3.1-flash-lite-preview",
                contents=prompt,
            )
            time.sleep(4.1)           # respect 15 RPM free-tier limit (60 s / 15 = 4 s)
            return response.text.strip()
        except Exception as exc:
            err_str = str(exc)
            is_rate_limit = ("429" in err_str or
                             "quota" in err_str.lower() or
                             "rate" in err_str.lower())
            is_daily_quota = ("PerDay" in err_str or
                              "per_day" in err_str.lower() or
                              "limit: 0" in err_str)

            if is_daily_quota:
                print("[Gemini] Daily quota exhausted — switching to regex "
                      "fallback for all remaining articles (no further retries)")
                _gemini_quota_exhausted = True
                return None
            elif is_rate_limit and attempt == 0:
                print("[Gemini] RPM rate limit — waiting 60 s before retry …")
                time.sleep(60)
            else:
                print(f"[Gemini] API error: {exc}")
                time.sleep(1)
                return None
    return None


def is_relevant_article_llm(title: str) -> bool:
    """
    Returns True if the headline reports a new funding round / investment
    for a specific named company.

    Uses Gemini 3.1 Flash Lite when available; falls back to BAD_TITLE_PATTERNS
    regex when Gemini is unavailable.
    """
    if _gemini_client is None:
        return not is_bad_title(title)

    prompt = (
        "Does this headline report a NEW funding round, investment, or fundraise "
        "completed by a specific named company?\n"
        "Answer ONLY 'Yes' or 'No'. No explanation.\n\n"
        f"Headline: {title}"
    )
    answer = _gemini_call(prompt)
    if answer is None:
        return not is_bad_title(title)   # fallback
    return answer.lower().startswith("yes")


def extract_company_name_llm(title: str) -> str:
    """
    Extracts the company name receiving funding from the headline.

    Uses Gemini 3.1 Flash Lite when available; falls back to regex chain
    when Gemini is unavailable or returns an implausible result.
    """
    if _gemini_client is None:
        return extract_company_name(title)

    prompt = (
        "What is the exact name of the company that received funding in this headline?\n"
        "Return ONLY the company name — no punctuation, no explanation, "
        "no descriptors like 'startup' or 'Swedish'.\n\n"
        f"Headline: {title}"
    )
    answer = _gemini_call(prompt)

    # Sanity checks: non-empty, single line, not absurdly long
    if answer and "\n" not in answer and 1 < len(answer) < 60:
        return answer

    # Fallback if Gemini returned something implausible
    print(f"[Gemini name] Implausible result '{answer}' — using regex fallback")
    return extract_company_name(title)

def can_hire_data_roles_llm(company: str, title: str, summary: str) -> bool:
    """
    Returns True if this company would plausibly hire Data Scientists,
    ML / AI Engineers, or Data Engineers as it scales.

    The prompt is intentionally permissive: most funded tech startups
    eventually need these roles. We only return False for companies that
    are clearly non-technical (physical services, food production, events).

    Falls back to True (include) when Gemini is unavailable or errors,
    so no company is silently dropped due to an API failure.
    """
    if _gemini_client is None:
        return True     # no Gemini — include everything

    prompt = (
        "A funded startup is described below.\n"
        "As it grows, would it plausibly hire Data Scientists, ML Engineers, "
        "AI Engineers, or Data Engineers?\n"
        "Most software, platform, and tech startups eventually need these roles "
        "for analytics, ML features, data infrastructure, or AI products.\n"
        "Answer 'No' ONLY if the company is clearly non-technical — e.g. a "
        "restaurant, catering company, events organiser, hair salon, or a "
        "business whose core product is entirely physical with no software.\n"
        "Answer ONLY 'Yes' or 'No'. No explanation.\n\n"
        f"Company: {company}\n"
        f"Description: {title}. {summary[:300]}"
    )
    answer = _gemini_call(prompt)
    if answer is None:
        return True     # on error, include the company
    return not answer.strip().lower().startswith("no")


def analyse_article_llm(title: str, summary: str) -> dict | None:
    """
    Single Gemini call replacing the three separate calls for:
      1. relevance check (is_relevant_article_llm)
      2. company name extraction (extract_company_name_llm)
      3. data-role hiring assessment (can_hire_data_roles_llm)

    Returns a dict with keys:
      relevant   (bool)  — does this report a new funding round for a named company?
      company    (str)   — exact company name, or "" if not relevant
      data_roles (bool)  — would this company plausibly hire data/ML/AI engineers?

    Returns None if Gemini is unavailable or the response cannot be parsed,
    so callers can fall back to regex logic.
    """
    if _gemini_client is None:
        return None

    prompt = (
        "Analyse this startup news headline and answer three questions.\n"
        "Return ONLY valid JSON — no explanation, no markdown, no code fences.\n\n"
        "JSON keys required:\n"
        '  "relevant":   true if the headline reports a NEW funding round, investment,\n'
        '                or fundraise completed by a specific named company; else false.\n'
        '  "company":    the exact company name that received funding (no descriptors\n'
        '                like "Swedish" or "startup"); empty string "" if not relevant.\n'
        '  "data_roles": true if, as this company scales, it would plausibly hire\n'
        '                Data Scientists, ML Engineers, AI Engineers, or Data Engineers.\n'
        '                Answer false ONLY for clearly non-technical businesses\n'
        '                (restaurants, catering, events, hair salons, purely physical\n'
        '                businesses with no software product).\n\n'
        f"Headline: {title}\n"
        f"Description: {summary[:250]}"
    )
    answer = _gemini_call(prompt)
    if answer is None:
        return None

    # Strip accidental markdown code fences
    clean = re.sub(r"^```[a-z]*\n?", "", answer.strip())
    clean = re.sub(r"\n?```$", "", clean)
    try:
        result = json.loads(clean)
        return {
            "relevant":   bool(result.get("relevant",   False)),
            "company":    str(result.get("company",     "")).strip(),
            "data_roles": bool(result.get("data_roles", True)),
        }
    except (json.JSONDecodeError, KeyError, TypeError):
        print(f"[Gemini] JSON parse error — raw response: {answer[:120]}")
        return None


# ── Filters ───────────────────────────────────────────────────────────────────

def is_bad_title(title: str) -> bool:
    """Regex-based fallback relevance filter (used when Gemini is unavailable)."""
    tl = title.lower()
    return any(re.search(p, tl) for p in BAD_TITLE_PATTERNS)


def is_norway_only(article: dict) -> bool:
    text = (article["title"] + " " + article["summary"]).lower()
    norway = ["oslo-based", "oslo based", "norwegian startup", "norway-based"]
    nordic = SWEDEN_KEYWORDS + DENMARK_KEYWORDS
    return any(s in text for s in norway) and not any(k in text for k in nordic)


def passes_basic_filters(article: dict) -> bool:
    """
    Fast keyword-only pre-filter — no API calls.
    Articles that fail here are dropped immediately.
    Now accepts both Swedish and Danish articles.
    """
    text = (article["title"] + " " + article["summary"]).lower()
    pub  = to_datetime(article["published"])
    if age_days(pub) > MAX_AGE_DAYS:
        return False
    is_se = any(kw in text for kw in SWEDEN_KEYWORDS)
    is_dk = any(kw in text for kw in DENMARK_KEYWORDS)
    if not (is_se or is_dk):
        return False
    if not any(kw in text for kw in FUNDING_KEYWORDS):
        return False
    if any(kw in text for kw in EXCLUDE_CONTENT_KEYWORDS):
        return False
    if is_norway_only(article):
        return False
    return True


def get_article_country(article: dict) -> str:
    """Returns 'sweden', 'denmark', or 'both' based on keyword matching."""
    text = (article["title"] + " " + article["summary"]).lower()
    is_se = any(kw in text for kw in SWEDEN_KEYWORDS)
    is_dk = any(kw in text for kw in DENMARK_KEYWORDS)
    if is_se and is_dk:
        return "both"
    if is_dk:
        return "denmark"
    return "sweden"  # default to Sweden (original behaviour)

# ── Funding extraction ────────────────────────────────────────────────────────

# Extended to recognise SEK, DKK, MSEK, MDKK, Mkr, mio. kr
# IMPORTANT: longer tokens (million, billion, msek …) must come before bare
# single-letter variants (m, k) so the alternation doesn't consume "m" from
# "million" and leave the rest unmatched.
_AMOUNT_RE = re.compile(
    # Pattern 1: symbol-first  e.g.  €10M  $6.6B  £500K
    # 'billion' before 'b\b' and 'million' before 'm\b' so longer tokens win.
    # milli?o?n? handles "million", "millon" (missing i), "milion" (single l)
    r"([€£\$])\s*([\d]+(?:[.,]\d+)?)\s*"
    r"(billion|milli?o?n?|mn|bn\b|b\b|k\b|m\b)?"
    r"|"
    # Pattern 2: number-first  e.g.  200 MSEK  50 million DKK  1.2 billion kr
    r"([\d]+(?:[.,]\d+)?)\s*"
    r"(billion|milli?o?n?|mio\.?\s*kr|msek|mdkk|mkr|bn\b|b\b|k\b|m\b)\s*"
    r"(?:sek|dkk|euro[s]?|dollar[s]?|usd|kr)?",
    re.IGNORECASE,
)

_ROUND_RE = re.compile(
    r"\b(pre[\-\s]?seed|seed|series\s+[a-e]|growth\s+round|bridge\s+round"
    r"|ipo|crowdfunding)\b",
    re.IGNORECASE,
)


def extract_funding_info(title: str, summary: str) -> tuple[str, str]:
    text = title + " " + summary

    round_str = ""
    rm = _ROUND_RE.search(text)
    if rm:
        raw = rm.group(0).strip()
        raw = re.sub(r"series\s+([a-e])",
                     lambda m: f"Series {m.group(1).upper()}", raw, flags=re.IGNORECASE)
        raw = re.sub(r"pre[\-\s]?seed", "Pre-Seed", raw, flags=re.IGNORECASE)
        round_str = raw.title() if raw.lower() != "ipo" else "IPO"

    amount_str = ""
    am = _AMOUNT_RE.search(text)
    if am:
        try:
            if am.group(1):
                sym  = am.group(1)
                num  = float(am.group(2).replace(",", "."))
                unit = (am.group(3) or "").lower()
            else:
                sym  = ""   # will be set by currency unit below
                num  = float(am.group(4).replace(",", "."))
                unit = (am.group(5) or "").lower()
                # Detect Scandinavian currency symbols
                full_match = am.group(0).lower()
                if "sek" in full_match or "kr" in full_match:
                    sym = "SEK "
                elif "dkk" in full_match:
                    sym = "DKK "
                else:
                    sym = "€"

            if unit in ("bn", "b", "billion"):
                amount_str = f"{sym}{num:g}B"
            elif unit in ("m", "mn", "million", "milion", "millon",
                          "msek", "mdkk", "mkr"):
                amount_str = f"{sym}{num:g}M"
            elif unit in ("k",):
                amount_str = f"{sym}{int(num)}K"
            elif "mio" in unit:
                amount_str = f"{sym}{num:g}M"
            else:
                amount_str = f"{sym}{num:g}"
        except (ValueError, IndexError):
            pass

    return amount_str, round_str

# ── Domain tags ───────────────────────────────────────────────────────────────

def get_domain_tags(article: dict) -> list[str]:
    text = " " + (article["title"] + " " + article["summary"]).lower() + " "
    return [tag for tag, kws in DOMAIN_TAGS.items() if any(k in text for k in kws)]

# ── Company name extraction — regex chain (fallback) ─────────────────────────

def _normalise_apostrophes(s: str) -> str:
    return s.replace("\u2019", "'").replace("\u2018", "'").replace("\u02bc", "'")


_PREFIX_RE = re.compile(
    r"""^(?:
        (?:sweden|denmark)'?s?\s+       |
        (?:swedish|danish)\s+           |
        stockholm[\-\s]based\s+         |
        gothenburg[\-\s]based\s+        |
        copenhagen[\-\s]based\s+        |
        nordic\s+                       |
        (?:ai|ml|data|tech|saas|fintech|deeptech|biotech|bio|medtech|nuclear|
           cleantech|healthtech|quantum|crypto|gaming|energy|insurtech|pet|
           micro|nano)\s+
        (?:startup|company|firm|scaleup|unicorn|platform)\s+  |
        (?:startup|company|firm|scaleup)\s+                   |
        [\w\-]+[\-\s](?:based|native|first)\s+
    )+""",
    re.IGNORECASE | re.VERBOSE,
)

_DESC_RE = re.compile(
    r"^(?:(?:ai|ml|data|b2b|b2c|saas|tech|green|digital|smart|autonomous|"
    r"cloud|api|deep|advanced|innovative|leading|open[\-\s]source|next[\-\s]gen|"
    r"micro|nano|pet|bio|nuclear|prevention[\-\s]first|"
    r"insurtech|healthtech|fintech|proptech|edtech|legaltech)\s+)*",
    re.IGNORECASE,
)

_FUNDING_VERB_RE = re.compile(
    r"\s+(?:raises?|secures?|gets?|receives?|closes?|lands?|fetches?|"
    r"announces?|backs?|backed|completes?|confirms?|bags?|attracts?|"
    r"nets?|powers?\s+up\s+with|emerges?\s+from\s+stealth\s+with|"
    r"extends?\s+(?:funding|its\s+funding|round)|wraps?\s+up)",
    re.IGNORECASE,
)


def extract_company_name(title: str) -> str:
    """Regex-based company name extraction (used as Gemini fallback)."""
    title_n = _normalise_apostrophes(title)

    match = _FUNDING_VERB_RE.search(title_n)
    candidate = title_n[:match.start()].strip() if match else title_n[:60]

    candidate = _PREFIX_RE.sub("", candidate).strip()
    candidate = _DESC_RE.sub("", candidate).strip()

    words = candidate.split()
    if len(words) > 2:
        cap = [w for w in words if w and w[0].isupper()]
        candidate = " ".join(cap[-2:]) if cap else " ".join(words[-2:])

    candidate = candidate.strip(" -–,.")
    return candidate if candidate and len(candidate) > 1 else title[:40]


def normalize_for_cluster(name: str) -> str:
    n = _normalise_apostrophes(name)
    n = _PREFIX_RE.sub("", n).strip()
    n = _DESC_RE.sub("", n).strip()
    n = re.sub(r"'s$", "", n).strip(" -–,.'\"")
    return n.lower()


def linkedin_url(company_name: str) -> str:
    return (
        f"https://www.linkedin.com/search/results/companies/"
        f"?keywords={quote(company_name)}"
    )

# ── Invalid company name guard ────────────────────────────────────────────────
# When Gemini is unavailable the regex sometimes extracts a funding-round term
# ("Series A", "Seed", "Pre-Seed") as the company name.  These articles are
# useless in the digest and must be dropped before clustering.

_INVALID_COMPANY_RE = re.compile(
    r"^(?:pre[\-\s]?seed|seed|series\s+[a-e]|bridge\s+round|growth\s+round"
    r"|ipo|crowdfunding|round|funding\s+round)$",
    re.IGNORECASE,
)


def is_invalid_company_name(name: str) -> bool:
    return bool(_INVALID_COMPANY_RE.match(name.strip()))


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

# ── Email HTML builder ────────────────────────────────────────────────────────

def _build_country_section(articles: list[dict], flag: str, name: str,
                            header_bg: str) -> str:
    """
    Returns the HTML for one country section.
    Produces BOTH a desktop table and mobile cards.

    Desktop:  6-column table (shown by default; hidden on mobile via @media)
    Mobile:   stacked cards  (hidden by default; shown on mobile via @media)

    The inline style="display:none" on .mobile-cards means they are hidden
    everywhere UNLESS a @media query overrides it — which Gmail mobile app,
    Apple Mail, and Outlook.com all do. Desktop Gmail strips <style> blocks,
    so the inline style keeps the cards hidden there, and the table shows
    without interference.
    """
    count = len(articles)
    desktop_rows = ""
    mobile_cards = ""

    for a in articles:
        pub      = to_datetime(a["published"])
        days_old = age_days(pub)
        date_str = format_date(pub)

        fresh_badge = (
            '<span style="background:#22c55e;color:#fff;font-size:10px;'
            'padding:1px 6px;border-radius:8px;font-weight:bold;'
            'vertical-align:middle;margin-left:4px;">NEW</span>'
            if days_old <= FRESH_DAYS else ""
        )
        coverage_note = (
            f'<span style="color:#9ca3af;font-size:11px;">({a["coverage"]} sources)</span>'
            if a.get("coverage", 1) > 1 else ""
        )

        amount, rnd = a.get("amount", ""), a.get("round", "")
        funding_parts = []
        if rnd:
            funding_parts.append(
                f'<span style="font-weight:600;color:#374151;">{rnd}</span>')
        if amount:
            funding_parts.append(
                f'<span style="color:#059669;font-weight:700;">{amount}</span>')
        funding_html = (
            ' <span style="color:#d1d5db;">·</span> '.join(funding_parts)
            if funding_parts else
            '<span style="color:#d1d5db;font-size:11px;">—</span>'
        )

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

        # ── Desktop table row ──────────────────────────────────────────────────
        desktop_rows += f"""
        <tr style="border-bottom:1px solid #f3f4f6;">
          <td style="padding:11px 14px;vertical-align:top;min-width:130px;">
            <span style="font-weight:700;color:#111827;">{a['company']}</span>
            {fresh_badge} {coverage_note}
            <br>
            <a href="{a.get('linkedin_url','#')}" target="_blank"
               style="font-size:11px;color:#6b7280;text-decoration:none;">
              🔗 LinkedIn search
            </a>
          </td>
          <td style="padding:11px 14px;vertical-align:top;
                     min-width:110px;white-space:nowrap;">{funding_html}</td>
          <td style="padding:11px 14px;vertical-align:top;min-width:110px;">{tags_html}</td>
          <td style="padding:11px 14px;vertical-align:top;font-size:13px;">
            <a href="{a['link']}" target="_blank"
               style="color:#374151;text-decoration:none;">{a['title']}</a>
          </td>
          <td style="padding:11px 14px;vertical-align:top;font-size:12px;
                     color:#6b7280;white-space:nowrap;">{a['source']}</td>
          <td style="padding:11px 14px;vertical-align:top;font-size:12px;
                     color:#6b7280;white-space:nowrap;">{date_str}</td>
        </tr>"""

        # ── Mobile card ────────────────────────────────────────────────────────
        mobile_cards += f"""
        <div style="background:#fff;border:1px solid #e5e7eb;border-radius:10px;
                    margin-bottom:10px;padding:14px 16px;">
          <div style="font-size:15px;font-weight:700;color:#111827;
                      margin-bottom:2px;">
            {a['company']} {fresh_badge}
          </div>
          {('<div style="font-size:11px;color:#9ca3af;margin-bottom:6px;">'
            + str(a['coverage']) + ' sources</div>') if a.get('coverage', 1) > 1 else ''}
          <div style="margin-bottom:6px;">{funding_html}</div>
          <div style="margin-bottom:8px;">{tags_html}</div>
          <div style="font-size:13px;line-height:1.5;margin-bottom:8px;">
            <a href="{a['link']}" target="_blank"
               style="color:#1d4ed8;text-decoration:none;">{a['title']}</a>
          </div>
          <div style="font-size:11px;color:#6b7280;">
            <a href="{a.get('linkedin_url','#')}" target="_blank"
               style="color:#6b7280;text-decoration:none;">🔗 LinkedIn</a>
            &nbsp;·&nbsp; {a['source']} &nbsp;·&nbsp; {date_str}
          </div>
        </div>"""

    no_results_row = (
        f'<tr><td colspan="6" style="padding:32px;text-align:center;'
        f'color:#9ca3af;font-size:14px;">No {name} funding news found today '
        f'— check back tomorrow!</td></tr>'
        if not articles else ""
    )
    no_results_card = (
        f'<p style="text-align:center;color:#9ca3af;padding:24px 16px;">'
        f'No {name} funding news found today.</p>'
        if not articles else ""
    )

    return f"""
  <!-- ════ {name} section ════ -->
  <div style="background:{header_bg};color:#fff;padding:16px 32px;">
    <span style="font-size:16px;font-weight:700;letter-spacing:-.2px;">
      {flag}&nbsp; {name}
      <span style="font-weight:400;opacity:.75;font-size:13px;">
        &nbsp;— {count} compan{'ies' if count != 1 else 'y'}
      </span>
    </span>
  </div>

  <!-- Desktop table (hidden on mobile via CSS) -->
  <div class="desktop-table">
    <div style="overflow-x:auto;">
      <table style="width:100%;border-collapse:collapse;font-size:14px;">
        <thead>
          <tr style="background:#f9fafb;border-bottom:2px solid #e5e7eb;">
            <th style="padding:10px 14px;text-align:left;color:#6b7280;font-size:11px;
                       text-transform:uppercase;letter-spacing:.06em;">Company</th>
            <th style="padding:10px 14px;text-align:left;color:#6b7280;font-size:11px;
                       text-transform:uppercase;letter-spacing:.06em;white-space:nowrap;">
                       Round · Amount</th>
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
        <tbody>{desktop_rows or no_results_row}</tbody>
      </table>
    </div>
  </div>

  <!-- Mobile cards (hidden by default; revealed on mobile via @media) -->
  <div class="mobile-cards" style="display:none;padding:12px 10px;">
    {mobile_cards or no_results_card}
  </div>"""


def build_html(sweden_articles: list[dict], denmark_articles: list[dict]) -> str:
    today    = datetime.now().strftime("%A, %d %B %Y")
    se_count = len(sweden_articles)
    dk_count = len(denmark_articles)
    mode     = "Gemini 3.1 Flash Lite" if _gemini_client else "regex fallback"

    sweden_html  = _build_country_section(
        sweden_articles,  "🇸🇪", "Sweden",  "#005B99")
    denmark_html = _build_country_section(
        denmark_articles, "🇩🇰", "Denmark", "#AE0523")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <style>
    /*
      Mobile-responsive email strategy:
      - .desktop-table: shown by default (no inline display set); hidden on mobile via @media
      - .mobile-cards:  hidden by default (inline style="display:none"); shown on mobile via @media

      This works because:
      - Desktop Gmail strips <style> blocks → inline display:none on cards hides them ✓
      - Mobile Gmail app preserves <style> and fires @media → table hidden, cards shown ✓
      - Apple Mail / Outlook.com: full @media support ✓
      - Outlook desktop: ignores @media, falls back to inline styles (table shows) ✓
    */
    @media only screen and (max-width: 600px) {{
      .email-outer  {{ padding: 4px !important; }}
      .email-header {{ padding: 20px 16px !important; }}
      .email-header h1 {{ font-size: 18px !important; }}
      .email-tip    {{ padding: 8px 14px !important; font-size: 11px !important; }}
      .desktop-table {{ display: none !important; }}
      .mobile-cards  {{ display: block !important; }}
    }}
  </style>
</head>
<body class="email-outer"
      style="margin:0;padding:20px;background:#f3f4f6;font-family:Arial,sans-serif;">

<div style="max-width:1060px;margin:auto;background:#fff;border-radius:12px;
            overflow:hidden;box-shadow:0 2px 16px rgba(0,0,0,.08);">

  <!-- ── Master header ── -->
  <div class="email-header"
       style="background:#0d1b2a;color:#fff;padding:28px 32px;">
    <h1 style="margin:0 0 6px;font-size:22px;letter-spacing:-.3px;">
      🇸🇪🇩🇰 Nordic Startup Funding Digest
    </h1>
    <p style="margin:0;opacity:.65;font-size:13px;">
      {today}
      &nbsp;·&nbsp; {se_count} Swedish · {dk_count} Danish compan{'ies' if (se_count + dk_count) != 1 else 'y'}
      &nbsp;·&nbsp; Data · ML · Engineering · Quant
    </p>
  </div>

  <!-- ── Tip bar ── -->
  <div class="email-tip"
       style="background:#eff6ff;border-bottom:1px solid #dbeafe;
              padding:10px 32px;font-size:12px;color:#1d4ed8;">
    💡 Click <strong>LinkedIn search</strong> under a company name to find
    founders and hiring managers directly &nbsp;·&nbsp;
    🔬 Companies screened by Gemini for <strong>Data · ML · AI · Engineering</strong> role potential
  </div>

  {sweden_html}

  <!-- ── Divider between sections ── -->
  <div style="height:10px;background:#f3f4f6;"></div>

  {denmark_html}

  <!-- ── Footer ── -->
  <div style="background:#f9fafb;padding:16px 32px;font-size:12px;
              color:#9ca3af;border-top:1px solid #f3f4f6;">
    🤖 Nordic Startup Funding Agent v8 ({mode}) &nbsp;·&nbsp;
    Sources: ArcticStartup · Nordic Startup News · Silicon Canals · Tech.eu ·
    Tech Funding News · TechCrunch · FinSMEs · Sifted · EU-Startups · Google News
  </div>

</div>
</body></html>"""

# ── Email sender ──────────────────────────────────────────────────────────────

def send_email(html: str, se_count: int, dk_count: int) -> None:
    subject = (
        f"🇸🇪 {se_count} Swedish · 🇩🇰 {dk_count} Danish Startups"
        f" | {datetime.now().strftime('%d %b %Y')}"
    )
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = GMAIL_ADDRESS
    msg["To"]      = RECIPIENT_EMAIL
    msg.attach(MIMEText(html, "html"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_ADDRESS, RECIPIENT_EMAIL, msg.as_string())
    print(
        f"✅ Email sent — {se_count} Swedish + {dk_count} Danish companies"
        f" — {datetime.now().strftime('%H:%M UTC')}"
    )

# ── Email cleanup ─────────────────────────────────────────────────────────────

def cleanup_old_emails() -> None:
    """
    Delete digest emails older than CLEANUP_DAYS from Inbox and Sent Mail.
    Uses IMAP with the same Gmail App Password — no additional credentials needed.
    Covers both old subject format ("Startup Digest") and current ("Startups").
    """
    cutoff     = datetime.now() - timedelta(days=CLEANUP_DAYS)
    cutoff_str = cutoff.strftime("%d-%b-%Y")   # IMAP format: e.g. 23-Feb-2026
    folders    = ["INBOX", "[Gmail]/Sent Mail"]
    criteria   = f'(FROM "{GMAIL_ADDRESS}" BEFORE {cutoff_str} SUBJECT "Danish Startups")'

    try:
        with imaplib.IMAP4_SSL("imap.gmail.com", 993) as mail:
            mail.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
            total = 0
            for folder in folders:
                status, _ = mail.select(folder)
                if status != "OK":
                    print(f"[Cleanup] Skipping {folder} — not accessible")
                    continue
                status, data = mail.search(None, criteria)
                if status != "OK" or not data[0]:
                    print(f"[Cleanup] {folder}: nothing to delete")
                    continue
                msg_ids = data[0].split()
                for mid in msg_ids:
                    mail.store(mid, "+FLAGS", "\\Deleted")
                mail.expunge()
                total += len(msg_ids)
                print(f"[Cleanup] {folder}: deleted {len(msg_ids)} old digest email(s)")
            print(f"[Cleanup] Done — {total} email(s) removed (older than {CLEANUP_DAYS} days)")
    except Exception as exc:
        print(f"[Cleanup] Error: {exc}")

# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    print(f"🚀 Agent v8 — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")

    raw: list[dict] = []

    for url, name in RSS_SOURCES:
        articles = fetch_rss(url, name)
        print(f"  [{name}] {len(articles)} articles")
        raw.extend(articles)

    all_queries = GOOGLE_NEWS_QUERIES + DENMARK_GOOGLE_NEWS_QUERIES
    for query in all_queries:
        raw.extend(fetch_google_news(query))

    print(f"📥 {len(raw)} raw articles")

    # Step 1: fast keyword pre-filter (no API calls) — now accepts SE + DK
    pre_filtered = [a for a in raw if passes_basic_filters(a)]
    print(f"🔍 {len(pre_filtered)} after basic keyword filters")

    # Step 2: deduplicate by URL before Gemini calls (saves API quota)
    seen_urls: set[str] = set()
    unique = []
    for a in pre_filtered:
        if a["link"] not in seen_urls:
            seen_urls.add(a["link"])
            unique.append(a)
    print(f"🔗 {len(unique)} after URL deduplication")

    # Sort by recency and cap before Gemini — keeps runtime predictable.
    # Most recent articles are most valuable; older ones were likely processed
    # in a previous day's digest anyway.
    unique.sort(key=lambda a: age_days(to_datetime(a["published"])))
    if len(unique) > MAX_GEMINI_ARTICLES:
        print(f"⚠️  Capping to {MAX_GEMINI_ARTICLES} most recent articles "
              f"(dropped {len(unique) - MAX_GEMINI_ARTICLES} older ones)")
        unique = unique[:MAX_GEMINI_ARTICLES]

    # Steps 3+4+7 combined: single Gemini call per article
    # Each call returns relevance, company name, and data-role fit in one JSON response.
    # This replaces three separate API calls per article, cutting Gemini usage by ~3×.
    # Inter-call sleep is 4.1 s to respect the 15 RPM free-tier limit and avoid
    # 60-second rate-limit penalty waits.
    print(f"🤖 Gemini analysis — 1 call per article ({len(unique)} articles) …")
    enriched = []
    for a in unique:
        analysis = analyse_article_llm(a["title"], a.get("summary", ""))

        if analysis is None:
            # Gemini unavailable or JSON unparseable — use regex fallbacks
            if is_bad_title(a["title"]):
                print(f"  ✗ Dropped (regex): {a['title'][:80]}")
                continue
            a["company"] = extract_company_name(a["title"])
        else:
            if not analysis["relevant"]:
                print(f"  ✗ Not relevant: {a['title'][:80]}")
                continue
            if not analysis["data_roles"]:
                print(f"  ✗ No data roles: {analysis['company'] or a['title'][:60]}")
                continue
            # Use Gemini-extracted name, fall back to regex if empty
            a["company"] = analysis["company"] or extract_company_name(a["title"])

        a["linkedin_url"]        = linkedin_url(a["company"])
        a["tags"]                = get_domain_tags(a)
        a["amount"], a["round"]  = extract_funding_info(a["title"], a["summary"])
        a["country"]             = get_article_country(a)

        if is_invalid_company_name(a["company"]):
            print(f"  ✗ Bad company name {a['company']!r} — dropping: {a['title'][:60]}")
            continue

        enriched.append(a)
        print(f"  → {a['company']!r:30s} [{a['country']:6s}]  {a['title'][:50]}")

    print(f"✅ {len(enriched)} articles after Gemini filter")

    # Step 5: cluster duplicates
    clustered = cluster_by_company(enriched)

    # Step 6: split by country
    sweden_list  = [a for a in clustered if a.get("country") in ("sweden",  "both")]
    denmark_list = [a for a in clustered if a.get("country") in ("denmark", "both")]

    sweden_list.sort( key=lambda x: age_days(to_datetime(x["published"])))
    denmark_list.sort(key=lambda x: age_days(to_datetime(x["published"])))

    sweden_final  = sweden_list[:30]
    denmark_final = denmark_list[:30]

    print(
        f"📰 {len(sweden_final)} Swedish + {len(denmark_final)} Danish"
        f" companies in digest"
    )

    html = build_html(sweden_final, denmark_final)
    send_email(html, len(sweden_final), len(denmark_final))
    cleanup_old_emails()


if __name__ == "__main__":
    main()
