# Nordic Startup Funding Agent

A daily email digest of Swedish and Danish startup funding news, targeting companies likely to hire Data Scientists, ML Engineers, Data Engineers, and Quantitative Analysts.

## What it does

Scrapes English-language startup news sources every morning, filters for Swedish and Danish companies that have received funding, and sends a formatted HTML email with two tables — Sweden first, Denmark second.

Each row shows the company name, funding round and amount, domain tags, headline, source, and date. A LinkedIn search link is included per company for finding hiring managers.

The email is fully mobile-responsive: on desktop it renders as a compact 6-column table; on mobile it switches to a readable card layout, one card per company.

## Sources

EU-Startups, ArcticStartup, Silicon Canals, Tech.eu, Sifted, and Google News (Sweden + Denmark queries).

## How it works

- Keyword pre-filtering removes obviously irrelevant articles (wrong geography, no funding signal)
- Gemini 2.0 Flash judges whether each remaining headline is a genuine new funding round
- Gemini 2.0 Flash extracts the company name from the headline
- Each article is tagged as `sweden`, `denmark`, or `both` based on keyword matching
- Duplicate coverage of the same company is clustered into one row
- Results are split into two sorted lists (up to 30 companies each) and emailed via Gmail SMTP

Regex fallbacks handle all Gemini tasks if the API is unavailable.

## Email layout

**Desktop** — 6-column table (Company · Round/Amount · Domain · Headline · Source · Date)
**Mobile** — stacked cards, one per company, with company name, funding, tags, linked headline, and source/date on separate lines

The responsive behaviour uses CSS `@media` queries combined with inline styles, which is compatible with Gmail mobile app, Apple Mail, Outlook.com, and Outlook desktop.

## Schedule

Runs daily at 07:00 UTC (08:00 CET / 09:00 CEST) via GitHub Actions.

## Configuration

Three GitHub Actions secrets are required:

| Secret | Description |
|---|---|
| `GMAIL_ADDRESS` | Gmail address to send and receive the digest |
| `GMAIL_APP_PASSWORD` | Gmail App Password (not your account password) |
| `GEMINI_API_KEY` | Google AI Studio API key for Gemini 2.0 Flash |

To adjust the age window for articles, edit `MAX_AGE_DAYS` at the top of `agent.py` (currently 90 days).

## Changelog

### v7
- Added Denmark as a second country section (Sweden first, Denmark second)
- Fully mobile-responsive email using CSS media queries and a card layout
- Country tagging: articles classified as `sweden`, `denmark`, or `both`
- SEK and DKK currency support added to the amount parser
- Updated subject line to show both country counts
- Denmark-specific Google News queries added

### v6
- Gemini 2.0 Flash replaces BAD_TITLE_PATTERNS regex for relevance filtering
- Gemini 2.0 Flash replaces regex chain for company name extraction
- Both have full regex fallbacks if Gemini is unavailable or errors
- GEMINI_API_KEY loaded from environment (GitHub Secret)
