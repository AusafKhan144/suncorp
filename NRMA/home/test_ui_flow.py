# test_ui_flow.py — offline checks for ui_flow's pure logic, no browser needed.
# Run: python3 test_ui_flow.py
import asyncio
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])

import ui_flow as U

API = (
    "https://insurance.nrma.com.au/domain/question/api/question/api/2.x.x"
    "/home/quote/QTEN1/price"
)


class FakePage:
    """Only the surface UIJourney.__init__ and the pacing helpers touch."""

    def __init__(self):
        self.handlers = []
        self.waits = []

    def set_default_navigation_timeout(self, *a):
        pass

    def on(self, event, fn):
        self.handlers.append(fn)

    async def wait_for_timeout(self, ms):
        self.waits.append(ms)


class FakeResponse:
    def __init__(self, status, url):
        self.status = status
        self.url = url


def test_refusals_are_shared_per_page():
    """The sweep builds a second journey over the same page.

    One listener each meant every refusal was logged twice while neither
    instance could see the other's history — so the sweep never noticed the
    throttle the journey had already recorded.
    """
    page = FakePage()
    drive, sweep = U.UIJourney(page), U.UIJourney(page)
    assert len(page.handlers) == 1

    page.handlers[0](FakeResponse(429, API))
    assert drive.rate_limited() and sweep.rate_limited()
    assert sweep.refusals is drive.refusals


def test_only_real_refusals_count():
    page = FakePage()
    journey = U.UIJourney(page)
    for response in (
        FakeResponse(200, API),
        FakeResponse(404, "https://insurance.nrma.com.au/static/app.js"),
    ):
        page.handlers[0](response)
    assert journey.refusals == []
    assert journey.blocked_by() is None

    # A 403 on a journey API call is the Akamai ban the hybrid also hits.
    page.handlers[0](FakeResponse(403, API))
    assert journey.blocked_by().startswith("403")
    assert not journey.rate_limited()  # a ban is not a throttle


def test_pace_backs_off_only_when_throttled():
    page = FakePage()
    journey = U.UIJourney(page)
    asyncio.run(U._pace(journey, page))
    assert page.waits == [U.SWEEP_PAUSE_MS]

    page.handlers[0](FakeResponse(429, API))
    asyncio.run(U._pace(journey, page))
    assert page.waits[-1] == U.RATE_PAUSE_MS
    # The backoff clears the record, or every later tier would also back off.
    assert not journey.rate_limited()


def test_pick_survives_a_refused_selection():
    """While throttled, React never commits the click and Playwright raises.

    That cost a whole priced quote, including the tiers already read.
    """

    class Throttled:
        async def set_radio(self, name, value):
            raise Exception("Locator.check: Clicking the checkbox did not change its state")

    assert asyncio.run(U._pick(Throttled(), "LevelOfCover", "PLUS")) is False


def test_accept_rejects_a_stale_reading():
    """The bug that filed 4000's premium under 5000 and 10000.

    A refused selection leaves the previous premium on screen, so three PLUS
    tiers came back as identical copies of the 4000 reading — real-looking
    numbers under the wrong excess, which no downstream check would catch.
    """
    assert U._accept({"annual": "2261.59", "excess": "5000"}, "5000") == "2261.59"
    assert U._accept({"annual": "2261.59", "excess": "5000"}, 5000) == "2261.59"
    # The page is still showing 4000 — the click did not take.
    assert U._accept({"annual": "2261.59", "excess": "4000"}, "5000") == ""
    # Nothing selected, or nothing priced yet.
    assert U._accept({"annual": "2261.59", "excess": ""}, "5000") == ""
    assert U._accept({"annual": "", "excess": "5000"}, "5000") == ""


def test_retryable_hierarchy():
    """Session failures retry on a fresh IP; validation errors must not."""
    assert issubclass(U.AkamaiBlocked, U.Retryable)
    assert issubclass(U.RenderTimeout, U.Retryable)
    assert issubclass(U.Retryable, U.UIFlowError)
    assert not issubclass(U.UIFlowError, U.Retryable)


def test_fixture_labels_cover_the_enum():
    """The fittings radios are unnamed, so they are reached by visible label."""
    assert set(U._FIXTURE_LABEL) == {"STANDARD", "QUALITY", "PRESTIGE"}


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ok  {name}")
    print("all checks passed")
