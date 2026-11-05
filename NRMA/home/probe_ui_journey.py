"""Does the app's OWN request get through where our fetch() does not?

    python NRMA/home/probe_ui_journey.py [NRMA_PROXY_PROVIDER=goproxies]

The whole API path issues same-origin `fetch()` from a page that never
navigates. A real customer's protected POST is issued by the app's own JS, from
its own SPA route, carrying whatever state the app happens to hold. Every
difference we found by diffing `.har` was us imitating that; this asks the app
to do it for us and just watches the wire.

First runs the scraper's own full flow on the fresh exit IP (the control arm),
then lets the app quote on the same IP, then replays the app's POST body through
our `fetch()` path seconds later — once with the app's headers, once with the
scraper's (the 2026-09-22 discriminating experiment):
  * app's POST → 403                    — egress; no code change helps.
  * app 200, ours 403 with app headers  — the issuer matters: `ui_flow.py`.
  * app 200, ours 403 only with ours    — our header set: diff the two.
  * replays 200, our own flow 403       — our call sequence or payload.
  * every arm through                   — a clean IP: volume/reputation.

Costs one camoufox launch. Drives only as far as the address step, which is the
step whose POST always 403s, so it never needs ui_flow's unfinished fillers.
"""

import asyncio
import logging
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("probe")

from nrma_browser import PROXY_PROVIDER, quote_session  # noqa: E402
from ui_flow import UIJourney  # noqa: E402

# One real NSW address from the pending Contents input.
ADDRESS = "10 SPRING STREET, ABBOTSFORD NSW 2046"
QUOTE_POST = "/2.x.x/home/quote/"


async def replay(session, req) -> tuple[int, int]:
    """The 09-22 discriminating A/B: same session, same exit IP, same second.

    Re-issues the app's own protected POST body through our `fetch()` path,
    once with the app's headers and once with the scraper's header set.
    Returns (status with app headers, status with scraper headers).
    """
    from building import NRMAClient  # lazy: only this arm needs the scraper

    hdrs = await req.all_headers()
    body = req.post_data
    app_status, *_ = await session.request(req.url, "POST", headers=hdrs, body=body)
    ours = NRMAClient(session)
    ours.session_id = hdrs.get("x-iag-session-id", ours.session_id)
    ours.auth_session_token = hdrs.get("x-iag-auth-session")
    ours.customer_token = hdrs.get("x-iag-customer")
    our_status, *_ = await session.request(
        req.url, "POST", headers=ours._json_headers(hdrs.get("referer", "")), body=body
    )
    log.info("app's headers: %s", sorted(hdrs))
    return app_status, our_status


async def ours_first(session) -> str:
    """Control arm: the scraper's own full flow on this exit IP, before the app.

    Without it, "everything got through" cannot tell a clean IP from a flow
    that differs from the app's. Runs first so the IP is as fresh for ours as
    it will be for the app's.
    """
    import random

    import building as B
    from nrma_browser import redact

    profile = random.choice(B.load_profiles(B.INPUT_MAIN_CSV))
    try:
        r = await B.run_profile(profile, session, mode="main")
    except Exception as exc:  # the control arm must not stop the app's arm
        return f"{profile.sample_number}: {type(exc).__name__}: {redact(exc)[:150]}"
    if r.nrma_yearly:
        return f"{profile.sample_number}: priced, yearly {r.nrma_yearly}"
    return f"{profile.sample_number}: {redact(r.failure_reason or '?')[:150]}"


async def main() -> int:
    seen: list[tuple[str, str, int]] = []
    app_posts = []
    ab = None

    async with quote_session("NSW") as session:
        page = session.page
        ours = await ours_first(session)
        log.info("our flow: %s", ours)

        def on_response(resp):
            url = resp.url
            if "/question/api/" in url or "/cb/anonymous/" in url:
                seen.append((resp.request.method, url, resp.status))
            if QUOTE_POST in url and resp.request.method == "POST":
                app_posts.append(resp.request)

        page.on("response", on_response)
        log.info("Akamai validated; driving the real UI on %s", PROXY_PROVIDER)

        journey = UIJourney(page)
        try:
            quote_id = await journey.start("NSW")
            log.info("app minted quoteId=%s", quote_id)
            await journey.enter_address(ADDRESS)
            log.info("address entered; letting the app submit it")
            # Walk forward a few steps: the protected POST is the app
            # committing the address answers, which lands a step or two on.
            for i in range(3):
                route = await journey.next()
                log.info("advanced to route=%s", route or "?")
                await page.wait_for_timeout(6000)
                if any(QUOTE_POST in u and m == "POST" for m, u, _ in seen):
                    log.info("protected POST observed — stopping here")
                    break
        except Exception as exc:  # a half-driven journey still shows the wire
            log.warning("journey stopped early (%s: %s)", type(exc).__name__, exc)
        if app_posts:
            try:
                ab = await replay(session, app_posts[-1])
            except Exception as exc:
                log.warning("replay failed (%s: %s)", type(exc).__name__, exc)

    print("\n=== requests the APP made ===")
    for method, url, status in seen:
        tail = url.split("/question/api/")[-1].split("?")[0][-58:]
        flag = "  <-- the call that always 403s for us" if QUOTE_POST in url else ""
        print(f"  {method:5} {status}  {tail}{flag}")

    quote_posts = [s for m, u, s in seen if QUOTE_POST in u and m == "POST"]
    print("\n=== verdict ===")
    if not quote_posts:
        print("  INCONCLUSIVE — the app never reached the protected POST.")
        print("  Drive one step further, or the journey broke before it.")
        return 2
    # 403 is the edge refusing; any other status means Akamai let it through
    # (a re-POSTed answer set may draw a JSON 4xx from the origin — irrelevant).
    print(f"  our scraper's own flow, first        → {ours}")
    print(f"  app's own POST                       → {quote_posts}")
    if ab:
        print(f"  same body, our fetch, app's headers  → {ab[0]}")
        print(f"  same body, our fetch, our headers    → {ab[1]}")
    if all(s == 403 for s in quote_posts):
        print("  NO — the app's own POST is refused too: egress, not request shape.")
        return 1
    if not ab:
        print("  App got through; replay failed, so issuer vs headers is undecided.")
    elif ab[0] == 403:
        print("  The ISSUER matters: our fetch() is refused with the app's own headers.")
        print("  ui_flow.py (let the app issue the POST) is the lead.")
    elif ab[1] == 403:
        print("  Our HEADER SET is the difference — diff it against the app's above.")
    elif "→ 403" in ours:
        print("  Same IP: the app and both replays get through, our own flow is")
        print("  refused — our call SEQUENCE or PAYLOAD is the difference.")
    else:
        print("  Every arm got through, ours included: this IP is clean right now.")
        print("  Not shape — volume/reputation: pacing and clean IPs, not code.")
    # 2026-10-16: one pair said "our sequence" (ours 403, app 200); across five
    # more IPs the app's own POSTs were refused on four. An IP can flip mid-quote.
    print("  One run is one exit IP — repeat on several before acting on this.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
