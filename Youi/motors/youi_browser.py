"""Camoufox transport for the Youi motors scraper.

Why this exists
---------------
Youi now ships a `configure-enhanced-routing` feature flag (internally
`enableWafBotDetection`). A session the WAF does not recognise is not blocked —
it is silently routed to the call centre: `switchFlowDecision` comes back
`CallCentre`, every pricing call answers 200 with zero `dataDeltaOperations`,
and the row lands with no premium. Confirmed against a July trace that priced
$767.47 with byte-identical answers, where that flag was absent.

Replaying the rendered browser's cookies, `x-client-fingerprint` and
`hqsudwprpp-*` sensor headers onto a `requests` session does not help: the
sensor values are recomputed per request by the site's own JS, so replayed ones
are stale, and the proxy exit IP differs from the render IP.

The fix is the one `NRMA/home/nrma_browser.py` already uses for Akamai — issue
the API calls as same-origin `fetch()` from inside a real browser page. The
site's instrumentation patches `window.fetch`, so calls made from the page
context are signed by the sensor exactly like the site's own, and the cookie jar
is the live one rather than a copy.

`BrowserSession` implements the slice of the `requests.Session` API that
`YouiApiClient` uses (`headers`, `cookies`, `proxies`, `request()` returning an
object with `status_code` / `text` / `json()` / `raise_for_status()`), so it
drops in without touching the client or any of the ~40 flow steps.
"""

import json
import logging
import random
import os
import uuid
from contextlib import contextmanager

log = logging.getLogger(__name__)

QUOTE_START_URL = "https://www.youi.com.au/quote-start"
BFF_ORIGIN = "https://quotes.youi.com.au"

# `fetch()` refuses to set these (forbidden header names); the browser sets them
# itself, and correctly — which is the entire point of routing through one.
FETCH_FORBIDDEN_HEADERS = frozenset({
    "accept-encoding", "connection", "content-length", "cookie", "host",
    "origin", "referer", "user-agent",
})

NAV_TIMEOUT_MS = int(os.getenv("YOUI_NAV_TIMEOUT_MS", "120000"))
INIT_TIMEOUT_MS = int(os.getenv("YOUI_INIT_TIMEOUT_MS", "90000"))
SETTLE_MS = int(os.getenv("YOUI_SETTLE_MS", "6000"))
# The bot gate is probabilistic; a fresh browser on a fresh exit IP clears it.
BOOTSTRAP_ATTEMPTS = int(os.getenv("YOUI_BOOTSTRAP_ATTEMPTS", "4"))

# Oxylabs AU residential, same account the NRMA scrapers use. `sessid` pins one
# quote to one exit IP; a shared IP across quotes is itself a bot signal.
OXY_USER = os.getenv("OXY_USER", "wmc_user_aakFe")
OXY_PASS = os.getenv("OXY_PASS", "WmcLetmein_123")
OXY_HOST = os.getenv("OXY_HOST", "pr.oxylabs.io")
OXY_PORT = int(os.getenv("OXY_PORT", "7777"))
OXY_COUNTRY = os.getenv("OXY_COUNTRY", "au")
PROXY_ENABLED = os.getenv("YOUI_PROXY", "1").strip().lower() not in ("0", "false", "no")
HEADLESS = os.getenv("YOUI_HEADFUL", "0").strip().lower() in ("0", "false", "no", "")


class BootstrapFailed(RuntimeError):
    """The journey never reached /bff/api/v1/initialize — retry on a fresh IP."""


class BrowserResponse:
    """Enough of `requests.Response` for YouiApiClient to work unchanged."""

    def __init__(self, status_code: int, text: str, url: str):
        self.status_code = status_code
        self.text = text
        self.url = url

    def json(self):
        return json.loads(self.text)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(
                f"{self.status_code} Client Error: for url: {self.url}"
            )


class _CookieShim:
    """`session.cookies.set(...)` is a no-op — the browser owns the jar."""

    def __init__(self, page):
        self._page = page

    def set(self, *args, **kwargs):
        return None

    def as_dict(self) -> dict:
        return {c["name"]: c["value"] for c in self._page.context.cookies()}


class BrowserSession:
    """Drop-in for `requests.Session`, executing from the page context."""

    def __init__(self, page):
        self.page = page
        self.headers: dict = {}
        self.proxies: dict = {}  # unused; the browser is already behind the proxy
        self.cookies = _CookieShim(page)
        self.request_count = 0

    def request(self, method, url, json=None, params=None, timeout=None,
                verify=None, **_ignored):
        merged = dict(self.headers)
        body = None
        # requests happily attaches a body to a GET (the lookup helpers pass
        # payload={}), but fetch throws "HEAD or GET Request cannot have a body".
        if json is not None and method.upper() not in ("GET", "HEAD"):
            import json as _json
            body = _json.dumps(json)
            merged.setdefault("Content-Type", "application/json")
        safe = {
            k: v for k, v in merged.items()
            if k.lower() not in FETCH_FORBIDDEN_HEADERS and v is not None
        }
        if params:
            from urllib.parse import urlencode
            url = f"{url}{'&' if '?' in url else '?'}{urlencode(params)}"

        # Uses the page's own (sensor-patched) fetch, so the WAF headers the site
        # attaches to its own XHRs are attached to ours too.
        result = self.page.evaluate(
            """async ([url, method, headers, body]) => {
                const opts = {method, headers, credentials: 'include'};
                if (body !== null) opts.body = body;
                try {
                    const resp = await fetch(url, opts);
                    return {status: resp.status, body: await resp.text()};
                } catch (e) {
                    return {status: 0, body: 'fetch failed: ' + e};
                }
            }""",
            [url, method.upper(), safe, body],
        )
        self.request_count += 1
        return BrowserResponse(result["status"], result["body"], url)


def _proxy_config(sid: str) -> dict | None:
    if not PROXY_ENABLED:
        return None
    return {
        "server": f"http://{OXY_HOST}:{OXY_PORT}",
        "username": f"customer-{OXY_USER}-cc-{OXY_COUNTRY}-sessid-{sid}",
        "password": OXY_PASS,
    }


def _bootstrap(page) -> dict:
    """Drive quote-start -> Car -> Continue and return the /initialize body.

    The SPA calls /initialize itself, so the session that the WAF has already
    seen is the one the quote belongs to — which is the whole point of doing this
    in a browser rather than replaying a captured response.
    """
    page.set_default_navigation_timeout(NAV_TIMEOUT_MS)
    page.goto(QUOTE_START_URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
    page.wait_for_selector("label[for='toggleGroup-Car']", timeout=NAV_TIMEOUT_MS)

    # Real input events before the click, spread over time. The sensor scores
    # interaction, and an instant programmatic click on a freshly loaded page is
    # exactly the shape it is looking for — a session that skips this earns the
    # redirectUrl gate (empty authorizationToken) from /initialize.
    for x, y in ((320, 420), (505, 380), (660, 540), (430, 610)):
        page.mouse.move(x, y)
        page.wait_for_timeout(random.randint(180, 420))
    page.mouse.wheel(0, random.randint(400, 900))
    page.wait_for_timeout(SETTLE_MS)

    page.click("label[for='toggleGroup-Car']", timeout=30000)
    page.wait_for_timeout(random.randint(900, 1800))

    try:
        with page.expect_response(
            lambda r: "bff/api/v1/initialize" in r.url, timeout=INIT_TIMEOUT_MS
        ) as info:
            page.click("#quoteStartContinueButton", timeout=30000)
        response = info.value
        body = response.json()
    except Exception as exc:
        raise BootstrapFailed(
            f"never captured /bff/api/v1/initialize ({type(exc).__name__}: "
            f"{str(exc)[:120]})"
        ) from exc

    session_id = body.get("sessionId") or body.get("surrogateQuoteId")
    if not session_id:
        # The bot gate: /initialize answers 200 with a redirectUrl back to
        # quote-start and empty tokens instead of a session. A property of this
        # session/exit IP, so a fresh browser on a fresh IP is worth trying.
        if body.get("redirectUrl"):
            raise BootstrapFailed(
                f"bot gate — /initialize returned redirectUrl "
                f"{body['redirectUrl']} with no session"
            )
        raise BootstrapFailed(f"no session handle in /initialize body: {body}")
    body["sessionId"] = session_id

    # Let the SPA finish its own start-flow so the quote is in the same state the
    # site would leave it in before we take over.
    page.wait_for_timeout(SETTLE_MS)
    log.info(
        "│  browser bootstrap OK — surrogateQuoteId=%s flowStateId=%s",
        session_id, body.get("flowStateId"),
    )
    return body


@contextmanager
def quote_session(attempts: int = BOOTSTRAP_ATTEMPTS):
    """Yield (BrowserSession, initialize_body) on a WAF-recognised page.

    The bot gate is not deterministic — the same warm-up clears on one exit IP
    and gets redirected on the next — so a failed bootstrap is retried on a
    brand-new browser and `sessid` (a new exit IP) rather than failing the record.
    """
    from camoufox.sync_api import Camoufox

    last_error = None
    for attempt in range(1, attempts + 1):
        sid = uuid.uuid4().hex[:12]
        proxy = _proxy_config(sid)
        launch: dict = {"headless": HEADLESS, "os": "linux", "humanize": True}
        if proxy:
            # geoip aligns timezone/locale/fingerprint with the exit IP; a
            # mismatch is itself a bot signal.
            launch["proxy"] = proxy
            launch["geoip"] = True
        log.info(
            "│  launching camoufox %s%s (bootstrap attempt %d/%d)",
            "headless" if HEADLESS else "headful",
            " via proxy" if proxy else " on the local IP",
            attempt, attempts,
        )

        with Camoufox(**launch) as browser:
            page = browser.new_page()
            try:
                init = _bootstrap(page)
            except Exception as exc:
                # Not just BootstrapFailed: a dead residential exit surfaces as
                # NS_ERROR_ABORT / NS_ERROR_NET_TIMEOUT out of page.goto, and that
                # is a reason to try another IP, not to fail the record.
                last_error = exc
                log.warning(
                    "│  bootstrap attempt %d/%d failed: %s: %s",
                    attempt, attempts, type(exc).__name__, str(exc)[:120]
                )
                try:
                    page.close()
                except Exception:
                    pass
                continue
            try:
                yield BrowserSession(page), init
            finally:
                try:
                    page.close()
                except Exception:
                    pass
            return

    raise BootstrapFailed(
        f"bootstrap failed on {attempts} fresh browsers/IPs — last: {last_error}"
    )
