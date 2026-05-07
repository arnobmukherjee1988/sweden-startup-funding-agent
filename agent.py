def _gemini_call(prompt: str) -> str | None:
    """
    Single Gemini API call with automatic rate-limit backoff.

    Free-tier limits for Gemini 3.1 Flash Lite: 15 RPM / 500 RPD.

    The sleep(4.1) is in a `finally` block so it ALWAYS runs — whether the
    call succeeds, fails, or is retried. This is the fix for the 16 RPM spike:
    the original code placed the sleep inside the `try` block, meaning a failed
    first attempt fired the retry immediately with no delay.

    Daily quota exhaustion (PerDay limit) causes an immediate fallback to regex
    for ALL remaining articles — no 60 s waits.

    RPM rate limits get one 60 s retry before giving up.
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
            return response.text.strip()

        except Exception as exc:
            err_str = str(exc)
            is_daily_quota = (
                "PerDay"    in err_str or
                "per_day"   in err_str.lower() or
                "limit: 0"  in err_str
            )
            is_rate_limit = (
                "429"       in err_str or
                "quota"     in err_str.lower() or
                "rate"      in err_str.lower()
            )

            if is_daily_quota:
                print(
                    "[Gemini] Daily quota exhausted — switching to regex "
                    "fallback for all remaining articles (no further retries)"
                )
                _gemini_quota_exhausted = True
                return None

            elif is_rate_limit and attempt == 0:
                print("[Gemini] RPM rate limit — waiting 60 s before retry …")
                time.sleep(60)
                # The finally block below adds another 4.1 s after the retry too.

            else:
                print(f"[Gemini] API error: {exc}")
                return None

        finally:
            # Always sleep after every attempt — success OR failure OR retry.
            # 60 s / 15 RPM = 4.0 s minimum; 4.1 s gives a small safety margin.
            # This is the critical fix: the original sleep was inside `try`,
            # so a failed first attempt never slept before firing the retry.
            time.sleep(4.1)

    return None
