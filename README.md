# Sweden Startup Funding Agent

A daily email digest of Swedish startup funding news, targeting companies likely to hire Data Scientists, ML Engineers, Data Engineers, and Quantitative Analysts.

## What it does

Scrapes English-language startup news sources every morning, filters for Swedish companies that have received funding, and sends a formatted HTML email with a table of results.

Each row shows the company name, funding round and amount, domain tags, headline, source, and date. A LinkedIn search link is included per company for finding hiring managers.

## Sources

EU-Startups, ArcticStartup, Silicon Canals, Tech.eu, Sifted, and Google News.

## How it works

- Keyword pre-filtering removes obviously irrelevant articles (wrong geography, no funding signal)
- Gemini 2.0 Flash judges whether each remaining headline is a genuine new funding round
- Gemini 2.0 Flash extracts the company name from the headline
- Duplicate coverage of the same company is clustered into one row
- Results are emailed via Gmail SMTP

Regex fallbacks handle all Gemini tasks if the API is unavailable.

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
