"""Check our request shape against the real journey captured in `.har`.

    python NRMA/home/test_trace_headers.py

The 403 we cannot clear is on the single most-protected call in the flow, so the
headers it carries have to match what the app sends there. This asserts the four
distributed-trace headers exist and are internally coherent, that the constants
still match the capture, and that we did not "fix" the fingerprint by adding a
Chrome-only header to a Firefox session.
"""

import asyncio
import base64
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)

import nrma_browser as nb  # noqa: E402

FAILING_ENDPOINT = "2.x.x/home/quote/QTEN"


# ── The header set is coherent across all three carriers ────────────────────
def test_trace_headers_agree():
    h = nb.trace_headers("aabbccddeeff0011")

    version, trace_id, span_id, flags = h["traceparent"].split("-")
    assert version == "00" and flags == "01", h["traceparent"]
    assert len(trace_id) == 32 and len(span_id) == 16, h["traceparent"]
    assert re.fullmatch(r"[0-9a-f]{32}", trace_id), trace_id
    assert re.fullmatch(r"[0-9a-f]{16}", span_id), span_id

    nr = json.loads(base64.b64decode(h["newrelic"]))
    # A real agent derives every carrier from one W3C context. Mismatched ids
    # would be a tell of their own, so this is the assertion that matters.
    assert nr["d"]["tr"] == trace_id, "newrelic trace id must match traceparent"
    assert nr["d"]["id"] == span_id, "newrelic span id must match traceparent"
    assert span_id in h["tracestate"], "tracestate span id must match traceparent"
    assert str(nr["d"]["ti"]) in h["tracestate"], "tracestate must carry the same ts"

    assert h["x-b3-traceid"] == "aabbccddeeff0011"
    # Per page load, not per request — so two requests on one session share it.
    assert nb.trace_headers("aabbccddeeff0011")["x-b3-traceid"] == h["x-b3-traceid"]
    # ...while the W3C ids are fresh every time.
    assert nb.trace_headers("aabbccddeeff0011")["traceparent"] != h["traceparent"]


# ── The constants and shapes still match the real capture ───────────────────
def test_matches_the_capture():
    har = os.path.join(REPO, ".har")
    if not os.path.exists(har):
        print("  skip test_matches_the_capture (no .har in repo root)")
        return
    txt = open(har, encoding="utf-8", errors="replace").read()

    real_nr = re.findall(r"-H 'newrelic: ([^']*)'", txt)
    assert real_nr, "capture has no newrelic header to compare against"
    real = json.loads(base64.b64decode(real_nr[0] + "=="))["d"]
    assert real["ac"] == nb.NR_ACCOUNT, f'account drifted: {real["ac"]}'
    assert real["ap"] == nb.NR_APP, f'app id drifted: {real["ap"]}'
    assert real["tk"] == nb.NR_TRUST_KEY, f'trust key drifted: {real["tk"]}'
    assert real["ty"] == "Browser"

    # Same field set, same order — the payload is a byte-level artefact.
    ours = json.loads(base64.b64decode(nb.trace_headers("0" * 16)["newrelic"]))
    assert list(ours["d"]) == list(real), f'{list(ours["d"])} != {list(real)}'
    assert ours["v"] == [0, 1]

    real_ts = re.findall(r"-H 'tracestate: ([^']*)'", txt)[0]
    ours_ts = nb.trace_headers("0" * 16)["tracestate"]
    strip = lambda s: re.sub(r"[0-9a-f]{16}|\d{13}", "X", s)  # noqa: E731
    assert strip(ours_ts) == strip(real_ts), f"{ours_ts}\n{real_ts}"

    # Every header the app sends on the failing call, minus the ones the browser
    # owns and the one Chrome-only header we refuse to forge.
    block = next(
        b for b in txt.split("curl '") if FAILING_ENDPOINT in b.split("'")[0]
    )
    real_headers = {h.lower() for h in re.findall(r"-H '([^:]+):", block)}
    browser_owned = {
        "accept-language", "origin", "referer", "user-agent",
        "sec-ch-ua", "sec-ch-ua-mobile", "sec-ch-ua-platform",
    }
    ours_sent = set(nb.browser_safe_headers({
        "accept": "application/json, text/plain, */*",
        "cache-control": "no-cache",
        "content-type": "application/json",
        "pragma": "no-cache",
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "x-iag-consumer-system": "RETAIL_WEB",
        "x-iag-session-id": "s",
    })) | set(nb.trace_headers("0" * 16))
    missing = real_headers - browser_owned - ours_sent - {"priority"}
    assert not missing, f"app sends headers we do not: {sorted(missing)}"


# ── Do not trade one contradiction for another ──────────────────────────────
def test_no_chrome_only_headers():
    sent = set(nb.trace_headers("0" * 16))
    # `priority: u=1, i` is in the capture; Firefox does not send it. Forging it
    # would recreate the Chromium-hints-on-Firefox tell fixed on 2026-09-08.
    assert "priority" not in sent
    assert not any(k.startswith("sec-ch-") for k in sent)


# ── The page URL is moved to the step the caller claims ─────────────────────
def test_referer_alignment():
    class FakePage:
        def __init__(self):
            self.calls = []

        async def evaluate(self, script, arg=None):
            self.calls.append(arg)

    async def run():
        s = nb.CamoufoxSession.__new__(nb.CamoufoxSession)
        s.page = FakePage()

        # The failing call's intended referer becomes the page's own URL.
        await s._align_page_url(
            {"referer": f"{nb.BASE_URL}/get-cover/home/3/risk-address"}
        )
        assert s.page.calls == ["/get-cover/home/3/risk-address"], s.page.calls

        # No referer, or an off-origin one, must not touch the page.
        s.page.calls.clear()
        await s._align_page_url({})
        await s._align_page_url(None)
        await s._align_page_url({"referer": "https://connect.nrma.com.au/cb"})
        assert s.page.calls == [], s.page.calls

        # Case-insensitive, because callers spell headers however they like.
        await s._align_page_url({"Referer": f"{nb.BASE_URL}/get-cover/home"})
        assert s.page.calls == ["/get-cover/home"], s.page.calls

        # A dead page must not lose the row.
        class Dead(FakePage):
            async def evaluate(self, script, arg=None):
                raise RuntimeError("Target page closed")

        s.page = Dead()
        await s._align_page_url({"referer": f"{nb.BASE_URL}/x"})

    asyncio.run(run())


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ok  {name}")
    print("trace headers + referer alignment: all checks passed")
