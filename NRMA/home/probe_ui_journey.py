"""Does the app's OWN request get through where our fetch() does not?

    python NRMA/home/probe_ui_journey.py [NRMA_PROXY_PROVIDER=goproxies]

The whole API path issues same-origin `fetch()` from a page that never
navigates. A real customer's protected POST is issued by the app's own JS, from
its own SPA route, carrying whatever state the app happens to hold. Every
difference we found by diffing `.har` was us imitating that; this asks the app
to do it for us and just watches the wire.

Decisive either way:
  * app's POST /2.x.x/home/quote/{id} → 200  — the UI journey is the way out,
    finish `ui_flow.py` and route collection through it.
  * app's POST → 403                          — the block is not about who
    issues the request, and ui_flow is not worth building out.

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


async def main() -> int:
    seen: list[tuple[str, str, int]] = []

    async with quote_session("NSW") as session:
        page = session.page

        def on_response(resp):
            url = resp.url
            if "/question/api/" in url or "/cb/anonymous/" in url:
                seen.append((resp.request.method, url, resp.status))

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
    if any(s < 400 for s in quote_posts):
        print(f"  BREAKTHROUGH — app's own POST returned {quote_posts}.")
        print("  The block is about WHO issues the request. Finish ui_flow.py.")
        return 0
    print(f"  NO — app's own POST also refused {quote_posts}.")
    print("  Not about who issues it. ui_flow.py would not help; drop that lead.")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
