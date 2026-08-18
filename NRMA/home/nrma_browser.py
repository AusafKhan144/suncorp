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

import html
import logging
import os
import re
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
    }
)

BOOTSTRAP_ATTEMPTS = int(os.getenv("NRMA_BOOTSTRAP_ATTEMPTS", "3"))
BOOTSTRAP_SETTLE_MS = int(os.getenv("NRMA_BOOTSTRAP_SETTLE_MS", "12000"))
BOOTSTRAP_INTERACT_MS = int(os.getenv("NRMA_BOOTSTRAP_INTERACT_MS", "6000"))
# Residential proxies are slow; Playwright's 30s navigation default is not enough
# to load this page and let the Akamai sensor finish posting.
NAV_TIMEOUT_MS = int(os.getenv("NRMA_NAV_TIMEOUT_MS", "120000"))

# Oxylabs AU residential. A proxy is not optional: Akamai flags the exit IP
# after roughly one completed quote, and DynaCop rate-limits quote creation per
# true-client-ip. `sessid` gives each quote its own exit IP, which is what keeps
# both of those off us. Set NRMA_PROXY=0 to run direct (single quote only).
OXY_USER = os.getenv("OXY_USER", "wmc_user_aakFe")
OXY_PASS = os.getenv("OXY_PASS", "WmcLetmein_123")
OXY_HOST = os.getenv("OXY_HOST", "pr.oxylabs.io")
OXY_PORT = int(os.getenv("OXY_PORT", "7777"))
OXY_COUNTRY = os.getenv("OXY_COUNTRY", "au")
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
    """Per-quote Oxylabs config; `sessid` pins this quote to its own exit IP."""
    if not PROXY_ENABLED:
        return None
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
        safe_headers = {
            k: v
            for k, v in (headers or {}).items()
            if k.lower() not in FETCH_FORBIDDEN_HEADERS
        }
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
        if _abck_validated(jar.get("_abck")):
            log.info(
                "│  Akamai validated on bootstrap attempt %d (%d cookies)",
                attempt,
                len(jar),
            )
            return
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
    from camoufox.async_api import AsyncCamoufox

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
        " via proxy" if proxy else " on the local IP",
    )
    if proxy:
        # geoip aligns timezone/locale/fingerprint with the exit IP; a mismatch
        # is itself a bot signal.
        launch["proxy"] = proxy
        launch["geoip"] = True

    async with AsyncCamoufox(**launch) as browser:
        page = await browser.new_page()
        try:
            await _bootstrap(page, state)
            yield CamoufoxSession(page)
        finally:
            await page.close()
