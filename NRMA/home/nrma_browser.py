# nrma_browser.py
"""Camoufox transport for the NRMA scrapers.

Why this exists
---------------
NRMA's Akamai edge rejects any request whose JSON body carries a non-empty
object inside an array — the `Parties` block written by `step9_set_owner` —
unless the session's `_abck` cookie has been validated by the Bot Manager
sensor. The origin answers HTTP 403 `Access Denied`; routed through Zyte that
surfaces as `520 /download/website-ban`.

Zyte cannot obtain that validation. `motors/motors.py` implements the full
playbook (pinned session, geoLocation, a browser bootstrap that clicks the
interstitial, sec_cpt solving) and `motors/cron.log` ends with 23,188
website-bans and `_abck still bot-flagged (~-1~)`.

Camoufox does get validated (`_abck` second field flips from `-1` to `0`), so
the API calls are issued as same-origin `fetch()` from a real page.

`CamoufoxSession` is a drop-in for the `ZyteSession.request()` contract used by
building.py / contents.py / combined.py — same signature, same 4-tuple return —
so the scrapers' step methods, payload builders and extractors are unchanged.
"""

import base64
import html
import json
import logging
import os
import re
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

BASE_URL = "https://insurance.nrma.com.au"

# `fetch()` refuses to set these (forbidden header names); the browser sets them
# itself, and correctly — which is the entire point of routing through one.
FETCH_FORBIDDEN_HEADERS = frozenset(
    {
        "accept-encoding",
        "connection",
        "content-length",
        "cookie",
        "host",
        "origin",
        "referer",
        "user-agent",
        # Chrome's value ("en-US,en;q=0.9"); real Firefox sends q=0.5. Dropping
        # it lets the browser send its own, which is coherent by construction
        # and stays right if camoufox changes version.
        "accept-language",
    }
)

# Client Hints are Chromium-only: Firefox does not implement them and
# `navigator.userAgentData` is undefined there. The callers all build a Chrome
# header set (UA string included) as if they were driving Chromium, but the
# session runs on camoufox, i.e. Firefox. `user-agent` was already stripped
# here, so the request went out as real Firefox while `sec-ch-ua` still
# announced "Chromium";v="138" — a header/UA contradiction that Akamai Bot
# Manager scores directly, and the reason the most-protected endpoint
# (POST /2.x.x/home/quote/{id}) started returning 403 while the cheaper calls
# in the same session kept succeeding. Prefix-matched because the family is
# open-ended (-arch, -bitness, -full-version-list, -model, -platform-version…).
CLIENT_HINT_PREFIX = "sec-ch-"


def browser_safe_headers(headers: dict | None) -> dict:
    """Drop headers the page's own fetch must own, or that would contradict it."""
    return {
        k: v
        for k, v in (headers or {}).items()
        if k.lower() not in FETCH_FORBIDDEN_HEADERS
        and not k.lower().startswith(CLIENT_HINT_PREFIX)
    }

# ---------------------------------------------------------------------------
# Distributed-trace headers
# ---------------------------------------------------------------------------
# The app's New Relic browser agent instruments every XHR it makes, so a genuine
# request to these endpoints carries a coherent set of trace headers. Ours
# carried none: all 24 requests in the `.har` capture of a real journey have
# `newrelic`, `traceparent`, `tracestate` and `x-b3-traceid`, which made our
# traffic trivially separable from the app's at exactly the endpoint Bot Manager
# scores hardest (POST /2.x.x/home/quote/{id}).
#
# ac / ap / tk are NRMA's New Relic account, application and trust-key ids —
# constant, read out of that capture. Trace and span ids are per-request and must
# agree across all three headers, because a real agent derives them from one W3C
# context; mismatched ids would be their own tell.
#
# `priority: u=1, i` is in the capture too and is deliberately NOT reproduced:
# Chrome sends it, Firefox does not, and adding it would re-create precisely the
# Chrome-on-Firefox contradiction that CLIENT_HINT_PREFIX exists to remove.
NR_ACCOUNT, NR_APP, NR_TRUST_KEY = "3497760", "1134267718", "152846"


def trace_headers(b3_trace_id: str) -> dict:
    """One request's worth of New Relic / W3C / B3 trace headers.

    `b3_trace_id` is threaded in because it is per *page load*, not per request:
    the capture shows 16 distinct values across 24 requests.
    """
    trace_id = uuid.uuid4().hex
    span_id = uuid.uuid4().hex[:16]
    ts_ms = int(time.time() * 1000)
    payload = {
        "v": [0, 1],
        "d": {
            "ty": "Browser",
            "ac": NR_ACCOUNT,
            "ap": NR_APP,
            "id": span_id,
            "tr": trace_id,
            "ti": ts_ms,
            "tk": NR_TRUST_KEY,
        },
    }
    return {
        "traceparent": f"00-{trace_id}-{span_id}-01",
        "tracestate": (
            f"{NR_TRUST_KEY}@nr=0-1-{NR_ACCOUNT}-{NR_APP}-{span_id}----{ts_ms}"
        ),
        "newrelic": base64.b64encode(
            json.dumps(payload, separators=(",", ":")).encode()
        ).decode(),
        "x-b3-traceid": b3_trace_id,
    }


BOOTSTRAP_ATTEMPTS = int(os.getenv("NRMA_BOOTSTRAP_ATTEMPTS", "3"))
# A full jar on this journey is 13 cookies; 9 means the sensor stopped early.
# Reloading for a fuller one is OFF (0 = accept any validated jar) because it
# measured net negative: 21 reloads across 15 launches, each costing
# SETTLE+INTERACT (~18s) plus a page load, which roughly doubled bootstrap time
# for a claimed +45% yield per launch — about 0.73x throughput.
#
# And the evidence for the gain was only correlational: a 13-cookie jar priced
# 34.5% against 23.7% for a 9-cookie one, but a jar is plausibly thin *because*
# the session was already going badly, so forcing a reload need not fix
# anything. Set to 13 to re-enable, but measure rows/h and not just yield per
# launch. Behaviour is asserted either way in test_bootstrap_jar.py.
MIN_BOOTSTRAP_COOKIES = int(os.getenv("NRMA_MIN_BOOTSTRAP_COOKIES", "0"))
BOOTSTRAP_SETTLE_MS = int(os.getenv("NRMA_BOOTSTRAP_SETTLE_MS", "12000"))
BOOTSTRAP_INTERACT_MS = int(os.getenv("NRMA_BOOTSTRAP_INTERACT_MS", "6000"))
# Residential proxies are slow; Playwright's 30s navigation default is not enough
# to load this page and let the Akamai sensor finish posting.
NAV_TIMEOUT_MS = int(os.getenv("NRMA_NAV_TIMEOUT_MS", "120000"))

# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------
# Never inline a credential here. This repo is a PUBLIC GitHub repository, and
# a proxy password committed to it is a password published to the internet.
#
# Read from disk rather than the environment alone, because the environment is
# not available where it matters: monitor_status.py relaunches a dead scraper
# through Popen under cron, which inherits cron's stripped environment. The
# same reasoning already puts the Chat webhook in webhook.conf.
#
# secrets.conf is gitignored, lives beside this file, and is plain KEY=VALUE:
#
#     OXY_PASS=...
#     IPROYAL_PASS=...
#
# Order: a real environment variable wins (a one-off override), then the file.
_SECRETS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "secrets.conf")
_SECRETS: dict[str, str] = {}
try:
    with open(_SECRETS_FILE, encoding="utf-8") as _fh:
        for _line in _fh:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _, _v = _line.partition("=")
                _SECRETS[_k.strip()] = _v.strip()
except FileNotFoundError:
    pass


def secret(name: str, default: str = "") -> str:
    """A credential, from the environment or secrets.conf. Never from source."""
    return os.getenv(name) or _SECRETS.get(name) or default


# Oxylabs AU residential. A proxy is not optional: Akamai flags the exit IP
# after roughly one completed quote, and DynaCop rate-limits quote creation per
# true-client-ip. `sessid` gives each quote its own exit IP, which is what keeps
# both of those off us. Set NRMA_PROXY=0 to run direct (single quote only).
OXY_USER = secret("OXY_USER")
OXY_PASS = secret("OXY_PASS")
OXY_HOST = os.getenv("OXY_HOST", "pr.oxylabs.io")
OXY_PORT = int(os.getenv("OXY_PORT", "7777"))
# The AU setting is shared by every pool below, not an Oxylabs-only knob.
OXY_COUNTRY = os.getenv("OXY_COUNTRY", "au")

# Two residential pools, switchable with NRMA_PROXY_PROVIDER. Oxylabs is the
# incumbent; GoProxies came from the Youi scrapers (same WMC account) and is a
# different provider with a different address space — which is the whole point.
# Exit IPs already rotate per quote, cookies are per-browser, camoufox
# randomises the fingerprint and x-iag-session-id is a fresh UUID, so nothing
# client-side survives between quotes. By elimination, whatever accumulates
# across hundreds of them lives in the pool's own IP space, and a second pool is
# the only lever that resets it without waiting hours.
#
# Sticky sessions are NOT optional: the Akamai bootstrap ties _abck to the exit
# IP, so one quote must leave from one address. Verified live 2026-09-10 —
# Oxylabs pins on `-sessid-<id>`, GoProxies on `-sessionid-<id>` (there,
# `-session-` and `-sessid-` both fail auth outright). Both measured as genuine
# AU residential; GoProxies runs 2.3-3.1s per request against Oxylabs' ~1.4s.
# Preference order. `launch_free_pool.py` walks this and starts the scraper on
# the first pool that is not resting, so a cron tick collects on GoProxies while
# Oxylabs sits out its cooldown instead of collecting nothing.
# "direct" is a pool like any other so the breaker, the per-pool cooldown and
# launch_free_pool.py all treat it uniformly. It is LAST on purpose: it spends
# WMC's own address, which DynaCop rate-limits per true-client-ip and which we
# would rather not put in NRMA's logs at volume, so a proxy is always preferred
# when one works. Measured 2026-09-11: none of them did (403 on the protected
# POST from all three providers, 200 direct), which is why it exists at all.
PROXY_POOLS = ("iproyal", "oxylabs", "goproxies", "direct")
PROXY_PROVIDER = os.getenv("NRMA_PROXY_PROVIDER", "oxylabs").strip().lower()
GOPROXY_USER = secret("GOPROXY_USER")
GOPROXY_PASS = secret("GOPROXY_PASS")
GOPROXY_HOST = os.getenv("GOPROXY_HOST", "proxy.goproxies.com")
GOPROXY_PORT = int(os.getenv("GOPROXY_PORT", "1080"))

# iProyal, from Youi/motors/utilities.py. First in PROXY_POOLS because it is the
# only pool measured as a real consumer ISP: Telstra AS1221, against GoProxies'
# IINET/Neighbourhood Cable and Oxylabs' rotating estate. On 2026-09-11 Akamai
# refused the protected POST from both of those while accepting it direct, so
# the ASN a pool presents is the variable that matters, not its history.
#
# Note iProyal takes its parameters in the PASSWORD, not the username, and
# needs an explicit session lifetime. Verified sticky 2026-09-11.
IPROYAL_USER = secret("IPROYAL_USER")
IPROYAL_PASS = secret("IPROYAL_PASS")
IPROYAL_HOST = os.getenv("IPROYAL_HOST", "geo.iproyal.com")
IPROYAL_PORT = int(os.getenv("IPROYAL_PORT", "12321"))
IPROYAL_LIFETIME = os.getenv("IPROYAL_LIFETIME", "30m")
PROXY_ENABLED = os.getenv("NRMA_PROXY", "1").strip().lower() not in ("0", "false", "no")
# NRMA_HEADFUL=1 shows the browser — for watching a journey by eye. Needs a
# display (X11/Wayland or Xvfb); headless stays the default for batch runs.
HEADLESS = os.getenv("NRMA_HEADFUL", "0").strip().lower() in ("0", "false", "no", "")


# Reasons that are a real answer about the property rather than a refused
# session. _row_is_complete() matches these prefixes to decide what a resume pass
# may skip, so they must survive shortening byte-for-byte.
TERMINAL_REASON_PREFIXES = (
    "SumInsuredOutOfRange",
    "No address found:",
    "Could not provide estimates for this property",
    "We don't cover properties in your area",
)

_HTTP_FAILURE = re.compile(
    r"^(?P<method>[A-Z]+)\s+(?P<url>\S+)\s+→\s+(?P<status>\d{3}):\s*(?P<body>.*)$",
    re.S,
)

# What the edge is actually saying, in the words we use everywhere else.
_REASON_GISTS = (
    ("Access Denied", "Akamai bot block"),
    ("DynaCop", "DynaCop rate limit"),
    ("Unauthorized", "quote token rejected"),
    ("Bad Gateway", "upstream 502"),
    ("Service Unavailable", "upstream 503"),
)


def short_failure_reason(reason: str, limit: int = 140) -> str:
    """Condense a failure into one readable CSV cell.

    Akamai refuses with a multi-line HTML document, which landed in
    Reason_for_quote_failure verbatim: the cell wrapped across several visual
    rows, carried escaped entities, and buried the only two facts that matter —
    which request was refused, and with what status.

    Terminal reasons pass through untouched so resume behaviour cannot change.
    """
    text = " ".join((reason or "").split()) if reason else ""
    if not text or reason.strip().startswith(TERMINAL_REASON_PREFIXES):
        return (reason or "").strip()[:limit]

    match = _HTTP_FAILURE.match(reason.strip())
    if not match:
        return text[:limit]

    body = html.unescape(re.sub(r"<[^>]+>", " ", match["body"]))
    body = " ".join(body.split())
    for needle, gist in _REASON_GISTS:
        if needle.casefold() in body.casefold():
            body = gist
            break
    # The full path repeats "question/api" twice and adds nothing; the tail
    # identifies the step and the quote.
    path = urlsplit(match["url"]).path or match["url"]
    short = "/".join(path.split("/")[-3:])
    return f"{match['method']} {short} → {match['status']}: {body}"[:limit]


# ---------------------------------------------------------------------------
# Bot-block circuit breaker
# ---------------------------------------------------------------------------
# A sustained Akamai 403 streak is reputation, not a per-profile fault. Measured
# 2026-09-08/09: yield decays with cumulative attempt volume (33% -> 0.3%) and
# the only recovery ever observed came from an idle gap (0.2% -> 7.8% off two
# days off). So a streak has to stop the run instead of sweeping the remaining
# profiles to rediscover the same block a few hundred times.
#
# Cooldown is 6h, raised from 30 min on 2026-09-10 after measuring what the
# short version actually bought. A run holds ~2.8 launches/row for ~430
# launches / ~5h and then the wall comes down; once it has, probing every 30
# min collected 3 rows from 288 launches across 10 hours. The 30-min cooldown
# was not a compromise between recovery and deadline — it forfeited both.
#
# 6h is chosen off the only recovery evidence in the log: on 09-03 a run that
# collapsed at 08:00 was back to ~16 rows/h by 14:00. That is a partial
# recovery, not a full one, so 6h buys roughly one more useful window per rest —
# which is what a next-day deadline needs. With schedule slack, 24h is the
# figure with the strongest evidence behind it.
#
# The default has to carry this, not the env var: cron relaunches through
# monitor_status.py's Popen, which inherits cron's stripped environment.
#
# The cooldown is a file rather than in-process state on purpose: it is shared
# by building.py and contents.py (one NRMA edge, one proxy account, so one
# reputation), and it survives the cron relaunch. Delete the file to resume now.
BLOCK_STREAK_LIMIT = int(os.getenv("NRMA_BLOCK_STREAK", "10"))
BLOCK_COOLDOWN_HOURS = float(os.getenv("NRMA_BLOCK_COOLDOWN_HOURS", "6"))
# Per pool, because reputation is per pool — that is the premise of having a
# second one. A shared file would let a GoProxies trip park Oxylabs, and would
# let an Oxylabs rest block a GoProxies run, which would make the A/B
# meaningless and waste the rest.
_HERE = os.path.dirname(os.path.abspath(__file__))


def cooldown_file(provider: str) -> str:
    return os.path.join(_HERE, f".nrma_cooldown.{provider}")


# The active pool's file. Single source of truth for this run: start_cooldown
# writes it and cooldown_hours_left reads it, so repointing this one name (a
# test, a one-off override) moves both. pool_rest_hours is the cross-pool
# reader and deliberately does not consult it.
COOLDOWN_FILE = cooldown_file(PROXY_PROVIDER)

_BLOCK_MARKERS = ("Access Denied", "→ 403", "Akamai bot block")


def is_bot_block(reason: str | None) -> bool:
    """True for an Akamai 403 refusal, in either the raw or shortened wording."""
    return any(m in (reason or "") for m in _BLOCK_MARKERS)


def _rest_hours(path: str) -> float:
    """Hours until the expiry stamped in `path`; 0.0 if absent or unreadable."""
    try:
        with open(path, encoding="utf-8") as fh:
            until = float(fh.read().strip())
    except (OSError, ValueError):
        return 0.0
    return max(0.0, (until - time.time()) / 3600)


def pool_rest_hours(provider: str) -> float:
    """Rest left for any pool, named. Used by launch_free_pool.py to choose."""
    return _rest_hours(cooldown_file(provider))


def cooldown_hours_left() -> float:
    """Hours until this run's pool counts as rested; 0.0 when it already does."""
    return _rest_hours(COOLDOWN_FILE)


def start_cooldown(hours: float | None = None) -> float:
    """Park every scraper for `hours`. Returns the hours actually parked."""
    hours = BLOCK_COOLDOWN_HOURS if hours is None else hours
    with open(COOLDOWN_FILE, "w", encoding="utf-8") as fh:
        fh.write(str(time.time() + hours * 3600))
    return hours


# ---------------------------------------------------------------------------
# CSV dates
# ---------------------------------------------------------------------------
# The client's CSVs ship Policy_Effective_Date in two spellings: "31-Aug-2026"
# (monthly files) and "23-Jul-26" (quarterly files). Only the 4-digit form was
# ever parsed, and the callers swallowed the failure and quoted date.today()
# instead — so every quarterly delivery carried a cover start of whenever the
# scraper happened to run, not the date the sample asked for. A cover date the
# CSV does not actually state must stop the run, never be guessed.
COVER_START_FORMATS = ("%d-%b-%Y", "%d-%b-%y")


def cover_start_date(value: str) -> str:
    """Normalise a CSV Policy_Effective_Date to CoverStartDate (YYYY-MM-DD).

    Raises ValueError on anything unparseable, blanks included, so a bad input
    column fails the run instead of silently repricing to a different date.
    """
    text = (value or "").strip()
    for fmt in COVER_START_FORMATS:
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    raise ValueError(
        f"Policy_Effective_Date {text!r} is not a date "
        f"(expected {' or '.join(COVER_START_FORMATS)})"
    )


class AkamaiNotValidated(RuntimeError):
    """Raised when a session never earns a validated `_abck`.

    Raised before any quote is created so a doomed profile costs one page load
    instead of ten API calls and a burnt quote id.
    """


def _abck_validated(abck: str | None) -> bool:
    """Akamai marks bot-suspected sessions with `-1` in `_abck`'s second field.

    It flips to `0` once the sensor accepts the session. Anything that is not
    `-1` (on a real multi-segment cookie) counts as validated.
    """
    if not abck:
        return False
    parts = abck.split("~")
    return len(parts) > 1 and parts[1] != "-1"


def landing_url(state: str = "NSW", product: str = "home-buildings-standard") -> str:
    return (
        f"{BASE_URL}/get-cover/home?serenityproduct={product}"
        f"&serenitydefaultstate={state}&serenityselectedstate={state}"
    )


def _proxy_config(sid: str) -> dict | None:
    """Per-quote proxy config; the session id pins this quote to one exit IP."""
    if not PROXY_ENABLED or PROXY_PROVIDER == "direct":
        return None
    if PROXY_PROVIDER == "iproyal":
        return {
            "server": f"http://{IPROYAL_HOST}:{IPROYAL_PORT}",
            "username": IPROYAL_USER,
            "password": (f"{IPROYAL_PASS}_country-{OXY_COUNTRY}"
                         f"_session-{sid}_lifetime-{IPROYAL_LIFETIME}"),
        }
    if PROXY_PROVIDER == "goproxies":
        return {
            "server": f"http://{GOPROXY_HOST}:{GOPROXY_PORT}",
            "username": f"{GOPROXY_USER}-country-{OXY_COUNTRY}-sessionid-{sid}",
            "password": GOPROXY_PASS,
        }
    return {
        "server": f"http://{OXY_HOST}:{OXY_PORT}",
        "username": f"customer-{OXY_USER}-cc-{OXY_COUNTRY}-sessid-{sid}",
        "password": OXY_PASS,
    }


class CamoufoxSession:
    """Issues NRMA API calls as same-origin `fetch()` from a validated page."""

    def __init__(self, page):
        self.page = page
        self.request_count = 0
        # Per page load, matching the capture's 16 ids across 24 requests.
        self.b3_trace_id = uuid.uuid4().hex[:16]

    async def _align_page_url(self, headers: dict | None) -> None:
        """Put the page on the journey step the caller says it is on.

        `referer` is a forbidden fetch header, so the browser sends the *page's*
        URL — and the page never left the landing URL for the whole journey. The
        capture shows the app sitting on `/get-cover/home/3/risk-address` when it
        POSTs the answers that always 403 for us, so our referer announced a step
        that could not have produced the request.

        replaceState moves the URL without navigating and without firing
        popstate, so the SPA is untouched while the browser's own Referer becomes
        the one the app would have sent.
        """
        referer = next(
            (v for k, v in (headers or {}).items() if k.lower() == "referer"), None
        )
        if not referer or not referer.startswith(BASE_URL):
            return
        path = referer[len(BASE_URL):] or "/"
        try:
            await self.page.evaluate("p => history.replaceState(null, '', p)", path)
        except Exception as exc:  # a page mid-teardown must not fail the request
            log.debug("│  referer align skipped (%s)", type(exc).__name__)

    async def request(
        self,
        url: str,
        method: str = "GET",
        headers: dict | None = None,
        body: str | None = None,
        cookies: dict | None = None,  # ignored: the browser owns the cookie jar
    ) -> tuple[int, list[tuple[str, str]], str, list[dict]]:
        """Execute a request from the page context.

        Returns (status_code, response_headers, body_text, response_cookies) to
        match ZyteSession. Response headers come back empty — `fetch` only
        exposes CORS-safelisted ones — which is fine because the callers read
        cookies from the fourth element, and the live browser jar is returned
        there in the `experimental.responseCookies` shape.
        """
        safe_headers = browser_safe_headers(headers)
        safe_headers.update(trace_headers(self.b3_trace_id))
        await self._align_page_url(headers)
        result = await self.page.evaluate(
            """async ([url, method, headers, body]) => {
                const opts = {method, headers, credentials: 'include'};
                if (body) opts.body = body;
                const resp = await fetch(url, opts);
                return {status: resp.status, body: await resp.text()};
            }""",
            [url, method, safe_headers, body],
        )
        self.request_count += 1
        jar = [
            {"name": c["name"], "value": c["value"]}
            for c in await self.page.context.cookies()
        ]
        if os.getenv("NRMA_TRACE_REQUESTS", "0") == "1":
            log.info("│  → req#%d %s %s → %d",
                     self.request_count, method,
                     url.replace(BASE_URL, "").split("?")[0][-60:],
                     result["status"])
        if os.getenv("NRMA_TRACE_REQUESTS", "0") == "1" and body:
            log.info("│    req#%d body=%dB hdrs=%s", self.request_count,
                     len(body), sorted(safe_headers))
        if result["status"] >= 400:
            # Which call failed, and was Akamai's session cookie still good when
            # it did? A 403 with a VALID _abck is the edge refusing this request
            # on its merits (IP/account reputation); a 403 with a BOT-FLAGGED
            # _abck means Akamai revoked the session mid-flow and the fix is to
            # re-validate, not to burn another IP. The jar is already read here,
            # so this costs nothing.
            abck = next((c["value"] for c in jar if c["name"] == "_abck"), None)
            log.warning(
                "│  ✖ req#%d %s → %d | _abck=%s",
                self.request_count,
                url.replace(BASE_URL, "").split("?")[0][-52:],
                result["status"],
                "VALID" if _abck_validated(abck) else "BOT-FLAGGED",
            )
            log.warning("│  ✖ body[:400]=%s",
                        " ".join(result["body"][:400].split()))
        return result["status"], [], result["body"], jar


async def _bootstrap(page, state: str) -> None:
    """Load the landing page and interact until Akamai validates the session.

    Passive waiting is not enough — the sensor needs real input events before it
    will accept the session, which is why Zyte's action-less renders never got
    past `~-1~`.
    """
    page.set_default_navigation_timeout(NAV_TIMEOUT_MS)
    await page.goto(
        landing_url(state), wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS
    )
    for attempt in range(1, BOOTSTRAP_ATTEMPTS + 1):
        await page.wait_for_timeout(BOOTSTRAP_SETTLE_MS)
        await page.mouse.move(300, 400)
        await page.mouse.move(640, 520)
        await page.mouse.wheel(0, 1400)
        await page.wait_for_timeout(BOOTSTRAP_INTERACT_MS)

        jar = {c["name"]: c["value"] for c in await page.context.cookies()}
        validated = _abck_validated(jar.get("_abck"))
        # A validated `_abck` is necessary but not sufficient. Measured across
        # 09-10's healthy window: a 13-cookie jar priced 34.5% of profiles
        # (n=238) against 23.7% for a 9-cookie one (n=38). A thin jar is a
        # bootstrap that stopped early, and it costs about a third of the yield.
        # Reload for a fuller one while attempts remain — but never fail the
        # profile over it, because a thin jar still quotes sometimes and burning
        # the row would be worse than accepting the lower odds.
        thin = validated and len(jar) < MIN_BOOTSTRAP_COOKIES
        if validated and not (thin and attempt < BOOTSTRAP_ATTEMPTS):
            log.info(
                "│  Akamai validated on bootstrap attempt %d (%d cookies)%s",
                attempt,
                len(jar),
                " — thin jar, accepted on the last attempt" if thin else "",
            )
            return
        if thin:
            log.info(
                "│  Bootstrap attempt %d/%d: validated but jar is thin "
                "(%d < %d cookies) — reloading for a fuller one",
                attempt,
                BOOTSTRAP_ATTEMPTS,
                len(jar),
                MIN_BOOTSTRAP_COOKIES,
            )
        else:
            log.warning(
                "│  Bootstrap attempt %d/%d: _abck still bot-flagged (~-1~)",
                attempt,
                BOOTSTRAP_ATTEMPTS,
            )
        if attempt < BOOTSTRAP_ATTEMPTS:
            # A slow residential exit can hang the reload past any timeout we
            # set; that is a reason to try again, not to abandon the profile.
            try:
                await page.goto(
                    landing_url(state),
                    wait_until="domcontentloaded",
                    timeout=NAV_TIMEOUT_MS,
                )
            except Exception as exc:  # noqa: BLE001 — next attempt still useful
                log.warning("│  Reload failed (%s) — retrying anyway", type(exc).__name__)
    raise AkamaiNotValidated(
        f"AkamaiNotValidated: _abck never validated after "
        f"{BOOTSTRAP_ATTEMPTS} bootstrap attempts"
    )


@asynccontextmanager
async def quote_session(state: str = "NSW"):
    """Yield a `CamoufoxSession` on an Akamai-validated page.

    One browser per quote: Akamai flags the exit IP after roughly one completed
    quote, and a fresh context on a flagged IP fails at the first API POST.
    Raises AkamaiNotValidated if the session never validates.
    """
    from camoufox.async_api import AsyncNewBrowser
    from playwright.async_api import async_playwright

    sid = uuid.uuid4().hex[:12]
    proxy = _proxy_config(sid)
    launch: dict = {"headless": HEADLESS, "os": "linux", "humanize": True}
    if not HEADLESS and not os.environ.get("DISPLAY"):
        # A headful launch with no DISPLAY has nowhere to draw — the window just
        # never appears. Point it at the console session rather than fail quietly.
        os.environ["DISPLAY"] = os.getenv("NRMA_DISPLAY", ":0")
        log.warning("│  DISPLAY was unset — using %s", os.environ["DISPLAY"])
    log.info(
        "│  launching camoufox %s%s",
        "headless" if HEADLESS else f"headful on DISPLAY={os.environ.get('DISPLAY')}",
        f" via {PROXY_PROVIDER}" if proxy else " on the local IP",
    )
    if proxy:
        # geoip aligns timezone/locale/fingerprint with the exit IP; a mismatch
        # is itself a bot signal.
        launch["proxy"] = proxy
        launch["geoip"] = True

    # Driving playwright's own context manager rather than AsyncCamoufox's:
    # AsyncCamoufox.__aenter__ starts the playwright driver and *then* launches
    # the browser, and Python never calls __aexit__ when __aenter__ raises — so
    # every failed launch (a refused proxy, mostly) permanently orphaned the
    # driver subprocess and its pipes. ~500 of those exhausted the fd table and
    # the tail of the run died on EMFILE. Here the launch failure is raised
    # inside `async with pw`, so the driver is always torn down.
    async with async_playwright() as pw:
        browser = await AsyncNewBrowser(pw, **launch)
        try:
            page = await browser.new_page()
            try:
                await _bootstrap(page, state)
                yield CamoufoxSession(page)
            finally:
                await page.close()
        finally:
            await browser.close()
