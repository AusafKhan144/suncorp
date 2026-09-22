"""Requests must not contradict the browser actually driving them.

The scrapers build a Chrome header set (UA string, Client Hints) but the
session runs on camoufox, i.e. Firefox. `user-agent` was always stripped, so
requests went out as real Firefox while `sec-ch-ua` still announced Chromium —
the header/UA contradiction Akamai scored, which is why the most-protected
endpoint 403'd while cheaper calls in the same session succeeded.

    python NRMA/home/test_header_coherence.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from NRMA.home.nrma_browser import browser_safe_headers

# Verified live against camoufox 2026-09-08: Firefox 135 sends no sec-ch-*
# header at all, and accept-language "en-US,en;q=0.5" (Chrome uses q=0.9).
CHROMIUM_ONLY = (
    "sec-ch-ua",
    "sec-ch-ua-mobile",
    "sec-ch-ua-platform",
    "sec-ch-ua-arch",
    "sec-ch-ua-full-version-list",
    "sec-ch-ua-platform-version",
)


def test_no_chromium_hints_escape():
    out = browser_safe_headers({h: "x" for h in CHROMIUM_ONLY})
    assert out == {}, f"Client Hints leaked to a Firefox session: {sorted(out)}"


def test_contradicting_identity_headers_dropped():
    out = browser_safe_headers({
        "user-agent": "Mozilla/5.0 ... Chrome/138.0.0.0 Safari/537.36",
        "accept-language": "en-US,en;q=0.9",
    })
    assert out == {}, f"identity headers must come from the browser: {sorted(out)}"


def test_real_app_headers_survive():
    # sec-fetch-* is NOT Chromium-only -- Firefox sends it, so it must pass.
    payload = {
        "accept": "application/json, text/plain, */*",
        "content-type": "application/json",
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "x-iag-consumer-system": "RETAIL_WEB",
        "x-iag-session-id": "abc123",
        "cache-control": "no-cache",
    }
    assert browser_safe_headers(payload) == payload


def test_case_insensitive_and_empty():
    assert browser_safe_headers({"Sec-CH-UA": "x", "User-Agent": "y"}) == {}
    assert browser_safe_headers(None) == {}


def test_live_scrapers_still_declare_the_chrome_set():
    """Guard the guard: if a caller stops sending these, this test is stale.

    The fix is deliberately at the chokepoint rather than in six caller files,
    so the callers still build Chrome headers. That is fine *because* they are
    filtered -- but if someone cleans the callers up, drop this test with them.
    """
    import NRMA.home.building as b
    src = open(b.__file__, encoding="utf-8").read()
    assert "sec-ch-ua" in src, "caller no longer sends Client Hints -- retire this test"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ok  {name}")
    print("header coherence: all checks passed")
