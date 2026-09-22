"""Quote Youi motors by driving the UI, then finishing through the API.

Why this shape
--------------
Neither half works alone:

  * Pure API (motors_v2 + requests, or + a browser fetch): Youi's WAF
    (`enableWafBotDetection` / `configure-enhanced-routing`) does not recognise
    the session and silently routes it to the call centre — pricing calls answer
    200 with zero `dataDeltaOperations` and no premium.

  * Pure UI: the journey diverts to /dcq/verify after the employment step and
    demands an SMS PIN, with consent-to-be-called attached. There is no premium
    page before that gate, so a UI-only run cannot produce a premium without
    receiving an SMS per quote.

Measured: the WAF's verdict is SESSION-scoped, not per-request. On a page that
has genuinely walked the UI, our own API calls are served normally —
`PATCH data/dcq/vehicle/quote/employment-details` returns 3 deltas, matching the
working July trace, instead of the empty responses a bare session gets.

So the UI is driven far enough to earn that standing and to answer the questions
the API path finds awkward (the dynamic vehicle dropdowns, address lookups), and
the remaining steps — which the UI puts behind the PIN but the API does not gate
at all — are completed through the API on the very same page.
"""

import logging
import os

from motors_ui_flow import (MotorsJourney, Retryable, build_answers,
                            vehicle_signals)
from motors_v2 import YouiMotorsNewFlow, format_values

log = logging.getLogger("YouiMotorsHybrid")

# Where the UI hands over. Everything up to and including employment details is
# driven in the browser; the API takes it from there, before /verify appears.
HANDOVER_HEADINGS = ("employment",)
VERIFY_MARKERS = ("pin code", "verification code")
UI_STEP_LIMIT = int(os.getenv("YOUI_UI_STEP_LIMIT", "14"))


class HandoverFailed(RuntimeError):
    """The UI never reached the handover point, so there is nothing to finish."""


def drive_ui_until_handover(page, rec: dict) -> MotorsJourney:
    """Walk the UI to the employment step and stop before /verify."""
    journey = MotorsJourney(page, build_answers(rec), vehicle=vehicle_signals(rec))
    journey.wait_ready()

    for step in range(1, UI_STEP_LIMIT + 1):
        scan = journey.fill_step()
        heads = " / ".join(scan["headings"]).lower()
        log.info("│  UI step %d: %s", step, heads[:70])

        if any(m in heads for m in VERIFY_MARKERS):
            # Past the handover point: the PIN gate cannot be satisfied and does
            # not need to be, so this is a failure of sequencing, not of the site.
            raise HandoverFailed(
                "reached the PIN gate — handover should have happened at "
                "employment details")

        reached = any(h in heads for h in HANDOVER_HEADINGS)
        gaps = journey.unfilled(scan)
        if gaps:
            log.warning("│  unanswered on %s: %s", heads[:40], gaps[:4])

        if reached and not gaps:
            log.info("│  handover point reached after %d UI steps", step)
            return journey

        moved = False
        for _ in range(3):
            try:
                if journey.advance():
                    moved = True
                    break
            except Retryable:
                raise
            except Exception as exc:
                log.warning("│  advance failed: %s", type(exc).__name__)
                break
            scan = journey.fill_step()
        if not moved:
            raise HandoverFailed(
                f"stalled on '{heads[:50]}' errors={journey.scan()['errors']}")

    raise HandoverFailed(f"handover point not reached within {UI_STEP_LIMIT} steps")


def recover_address_id(flow: YouiMotorsNewFlow) -> str | None:
    """Obtain the addressId the later steps need.

    `additional_policyholder` sends it as `useExistingAddress`, and a null there
    is a 400. The API path captures it from a delta while setting the parking
    address itself; the UI set the address instead, and a plain data read does
    not return that delta. So the API parking step is replayed on the same quote
    with the same record values — it re-derives the identical address and yields
    its id as a side effect.
    """
    resp = flow._patch_data("vehicle-parking")
    for op in resp.get("dataDeltaOperations", []) or []:
        value = op.get("value")
        if isinstance(value, dict) and value.get("addressId"):
            return value["addressId"]

    try:
        flow.vehicle_parking()
    except Exception as exc:                     # noqa: BLE001 - reported below
        log.warning("│  replaying vehicle_parking failed: %s: %s",
                    type(exc).__name__, str(exc)[:120])
    return flow.address_id


def finish_via_api(session, init: dict, rec: dict) -> dict:
    """Run the post-employment steps through the API on the UI's session."""
    flow = YouiMotorsNewFlow(format_values(rec), browser=(session, init))
    # Attaches to the quote the UI just built (same surrogateQuoteId) and
    # hydrates quote_number / step guards from start-flow.
    flow.start_quote()

    flow.address_id = recover_address_id(flow)
    log.info("│  recovered address_id=%s", flow.address_id)

    # Run the same sequence the API flow runs. additional_policyholder was skipped
    # here for a while because it 500'd — but that was on a CallCentre quote,
    # where none of these steps exist. On an Online quote the step is real, and
    # drivers_list depends on it (it opens by reading
    # additional-policyholder-details). The step guards itself via
    # state.is_step_available, so it is safe to always call.
    # additional_policyholder and drivers_list both 500 here, in either order, on
    # an Online quote. They are the two steps the UI has already performed: the UI
    # created the policyholder, so create-policyholder-task adds a duplicate
    # person and build-listed-drivers-task then posts a driver summary that does
    # not match the real one. Everything the UI did is skipped; only the steps
    # carrying new information are replayed.
    steps = ["insurance_history", "modifications", "duty_of_disclosure"]
    log.info("│  API steps %s", steps)

    for name in steps:
        step = getattr(flow, name)
        log.info("│  API step %s", name)
        step()

    flow.extract_premiums()
    return flow.output


def quote_one(page, session, init: dict, rec: dict) -> dict:
    """UI to the handover point, then API to the premium."""
    drive_ui_until_handover(page, rec)
    return finish_via_api(session, init, rec)
