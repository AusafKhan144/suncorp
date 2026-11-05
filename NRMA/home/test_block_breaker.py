"""Check the Akamai-403 circuit breaker and its cooldown.

    python NRMA/home/test_block_breaker.py

Guards three things the 202609 run got wrong: a 403 must take the long backoff
rather than a 1-4s IP swap, a streak of them must halt the run, and the halt
must survive the cron relaunch that would otherwise restart the burn.
"""

import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
# building.py's logging.basicConfig writes ./scraper.log — keep the test's noise
# out of the live 40MB one.
_tmp = tempfile.mkdtemp()
os.chdir(_tmp)

import nrma_browser as nb  # noqa: E402
import building  # noqa: E402

nb.COOLDOWN_FILE = os.path.join(_tmp, ".nrma_cooldown")
building.COOLDOWN_FILE = nb.COOLDOWN_FILE

# Real reason strings, copied out of scraper.log and the output CSVs.
RAW_403 = (
    "POST https://insurance.nrma.com.au/domain/question/api/question/api/"
    "2.x.x/home/quote/QTEN0023187071 → 403: <HTML><HEAD>\n"
    "<TITLE>Access Denied</TITLE>"
)
SHORT_403 = "POST home/quote/QTEN0023183772 → 403: Akamai bot block"

# ── A 403 is a bot block, in either wording; nothing else is ────────────────
assert nb.is_bot_block(RAW_403)
assert nb.is_bot_block(SHORT_403)
assert not nb.is_bot_block("POST home/quote/X → 429: DynaCop rate limit")
assert not nb.is_bot_block("Page.evaluate: NetworkError when attempting to fetch")
assert not nb.is_bot_block("SumInsuredOutOfRange|contents|low|target=42000")
assert not nb.is_bot_block(None)


class _R:
    def __init__(self, reason):
        self.failure_reason = reason


# ── Every 403 earns the 30-75s decay wait, not a 1-4s IP swap ───────────────
# Measured 2026-09-10: making this conditional on _block_streak spent 4.3
# launches per row against 2.75 for always waiting. A fresh IP 2s later gets
# refused again; ~50s of decay does not. See Decisions.md before "optimising"
# this back.
assert building._is_rate_limited(_R("POST x → 429: DynaCop"))
assert building._is_rate_limited(_R(RAW_403))
assert building._is_rate_limited(_R(SHORT_403))
assert not building._is_rate_limited(_R("BrowserSessionFailed: NS_ERROR_NET"))
assert not building._is_rate_limited(_R("SumInsuredOutOfRange|contents|low"))
# ...while still counting as retryable at all.
assert building._should_retry_on_new_session(_R(RAW_403))
assert not building._should_retry_on_new_session(_R("SumInsuredOutOfRange|x"))

# ── Cooldown: absent, garbage and expired all read as "not resting" ─────────
assert nb.cooldown_hours_left() == 0.0
open(nb.COOLDOWN_FILE, "w").write("not a number")
assert nb.cooldown_hours_left() == 0.0
open(nb.COOLDOWN_FILE, "w").write(str(time.time() - 60))
assert nb.cooldown_hours_left() == 0.0
assert nb.start_cooldown(6) == 6
assert 5.9 < nb.cooldown_hours_left() <= 6.0, nb.cooldown_hours_left()
os.remove(nb.COOLDOWN_FILE)
assert nb.cooldown_hours_left() == 0.0

# ── The streak trips at the limit, and only on an unbroken run of blocks ────
building.BLOCK_STREAK_LIMIT = 5
building._block_streak = 0
building._halt.clear()

for i in range(4):
    building._note_outcome(_R(RAW_403))
    assert not building._halt.is_set(), f"halted early at {i + 1}"

# A quote getting through must reset it — a wall is consecutive or it is noise.
building._note_outcome(_R(None))
assert building._block_streak == 0
# So must a terminal answer about the property: that is a working quote too.
building._note_outcome(_R(RAW_403))
building._note_outcome(_R("SumInsuredOutOfRange|contents|low|target=42000"))
assert building._block_streak == 0
assert not os.path.exists(nb.COOLDOWN_FILE)

# The regression that made the first live run of this breaker useless: 20 of 24
# give-ups were the 403, but a handful of dead residential exits scattered among
# them reset the counter every time, so the sweep never stopped. Transport noise
# must be NEUTRAL — it is evidence about the proxy, not about the block.
for noise in (
    "BrowserSessionFailed: Page.goto: NS_ERROR_NET_TIMEOUT",
    "BrowserSessionFailed: Page.goto: NS_ERROR_PROXY_CONNECTION_REFUSED",
    "Page.evaluate: NetworkError when attempting to fetch resource.",
):
    building._block_streak = 3
    building._note_outcome(_R(noise))
    assert building._block_streak == 3, f"{noise[:40]} reset the streak"
    assert not building._halt.is_set()

# 20 blocks with 4 flaky exits mixed in still has to trip — the live shape.
building._block_streak = 0
for i in range(5):
    building._note_outcome(_R("BrowserSessionFailed: Page.goto: NS_ERROR_NET_TIMEOUT"))
    building._note_outcome(_R(RAW_403))
assert building._halt.is_set(), "flaky exits must not defuse a real wall"
assert nb.cooldown_hours_left() > 0, "breaker must park the account, not just stop"

# A profile Akamai never validated (3 sessions x 3 bootstraps, all bot-flagged)
# IS a wall: 2026-10-07 the office IP failed every bootstrap for 46h and the
# breaker, treating it as noise, never tripped. It counts up — and still never
# resets the streak.
assert nb.is_bot_block("AkamaiNotValidated: _abck never validated after 3 bootstrap attempts")
os.remove(nb.COOLDOWN_FILE)
building._halt.clear()
building._block_streak = 0
for i in range(nb.BLOCK_STREAK_LIMIT):
    building._note_outcome(_R("AkamaiNotValidated: _abck never validated after 3 bootstrap attempts"))
assert building._halt.is_set(), "a run of never-validated sessions must trip the breaker"

# An unattended relaunch must never fall through to the office IP.
assert "direct" in nb.PROXY_POOLS and "direct" not in nb.UNATTENDED_POOLS
# An unconfigured mobile pool does not exist; a configured one is tried first.
assert ("mobile" in nb.UNATTENDED_POOLS) == bool(nb.MOBILE_PROXY_URL)
assert nb.PROXY_POOLS[0] == "mobile"
saved = nb.MOBILE_PROXY_URL, nb.PROXY_PROVIDER
nb.MOBILE_PROXY_URL = "http://customer-u-cc-au-sessid-{session}:p%40ss@mob.example:7777"
nb.PROXY_PROVIDER = "mobile"
assert nb._proxy_config("ab12") == {
    "server": "http://mob.example:7777",
    "username": "customer-u-cc-au-sessid-ab12",
    "password": "p@ss",
}
# A "-cc" pool is the same plan in another country; no suffix stays AU.
nb.PROXY_PROVIDER = "iproyal-gb"
assert "_country-gb_" in nb._proxy_config("ab12")["password"]
nb.PROXY_PROVIDER = "oxylabs-us"
assert "-cc-us-sessid-ab12" in nb._proxy_config("ab12")["username"]
nb.PROXY_PROVIDER = "iproyal"
assert f"_country-{nb.OXY_COUNTRY}_" in nb._proxy_config("ab12")["password"]
assert nb.cooldown_file("iproyal-gb") != nb.cooldown_file("iproyal")
nb.MOBILE_PROXY_URL, nb.PROXY_PROVIDER = saved

# Proxy credentials never reach the log or the CSV (2026-10-13: 80k log lines).
leak = "Failed to connect to proxy: http://customer-x-sessid-ab12:s3cr3t@pr.oxylabs.io:7777"
assert nb.redact(leak) == "Failed to connect to proxy: http://<redacted>@pr.oxylabs.io:7777"
assert "s3cr3t" not in nb.redact(leak)
assert nb.redact("POST home/quote/X → 403") == "POST home/quote/X → 403"  # untouched

# ── The parked account is what makes the halt survive a cron relaunch ───────
assert 5.9 < nb.cooldown_hours_left() <= 6.0, "default back-off is 6h"

os.remove(nb.COOLDOWN_FILE)
print("ok — 403 backoff, breaker streak, and cooldown all behave")

# The excess sweep rides out DynaCop 429s; anything else, or an endless 429,
# propagates so a toggle row with a hole in its ladder fails instead of shipping.
import asyncio
import contents
nb.SWEEP_PAUSE, nb.SWEEP_429_WAITS = (0, 0), (0, 0)
calls = []
async def _flaky():
    calls.append(1)
    if len(calls) < 3:
        raise RuntimeError('PUT x/price → 429: {"message":"SIMPLE: DynaCop blocked request"}')
    return {"ok": 1}
assert asyncio.run(nb.sweep_call(_flaky)) == {"ok": 1} and len(calls) == 3
for err in ("PUT x/price → 403: Access Denied", "PUT x/price → 429: DynaCop"):
    async def _fails(err=err):
        raise RuntimeError(err)
    try:
        asyncio.run(nb.sweep_call(_fails))
        raise AssertionError(f"must propagate: {err}")
    except RuntimeError as exc:
        assert str(exc) == err
# Contents sweeps the tiers its own response offers, not the 102-step grid.
assert contents._available_excess_tiers(
    {"conditions": {"Contents": {"BaseExcess": ["500", "300", 10000]}}}) == [300, 500, 10000]
assert contents._available_excess_tiers({}) == list(contents.DEFAULT_CONTENTS_EXCESS_TIERS)
print("sweep checks ok")
