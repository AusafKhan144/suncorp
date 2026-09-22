"""Check the thin-jar bootstrap retry.

    python NRMA/home/test_bootstrap_jar.py

A validated `_abck` turned out to be necessary but not sufficient. Measured over
2026-09-10's healthy window, a 13-cookie jar priced 34.5% of profiles (n=238)
against 23.7% for a 9-cookie one (n=38), so a thin jar earns one more reload.
The two things that must not regress: a thin jar may never burn a row, and the
retry may never mask a genuinely bot-flagged session.
"""

import asyncio
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.chdir(tempfile.mkdtemp())

import nrma_browser as nb  # noqa: E402

FULL = {f"c{i}": "v" for i in range(12)} | {"_abck": "x~0~y"}
THIN = {f"c{i}": "v" for i in range(8)} | {"_abck": "x~0~y"}
FLAGGED = {f"c{i}": "v" for i in range(12)} | {"_abck": "x~-1~y"}


class Page:
    """Feeds a scripted jar per bootstrap attempt and counts reloads."""

    def __init__(self, jars):
        self.jars, self.n, self.reloads = jars, 0, 0
        self.context = self

    def set_default_navigation_timeout(self, *a):
        pass

    async def goto(self, *a, **k):
        self.reloads += 1

    async def wait_for_timeout(self, ms):
        pass

    @property
    def mouse(self):
        return self

    async def move(self, *a):
        pass

    async def wheel(self, *a):
        pass

    async def cookies(self):
        jar = self.jars[min(self.n, len(self.jars) - 1)]
        self.n += 1
        return [{"name": k, "value": v} for k, v in jar.items()]


def run(jars):
    """Returns (outcome, attempts made, reloads beyond the initial load)."""
    page = Page(jars)

    async def go():
        try:
            await nb._bootstrap(page, "NSW")
            return "ok"
        except nb.AkamaiNotValidated:
            return "raised"

    return asyncio.run(go()), page.n, page.reloads - 1


def test_full_jar_accepted_immediately():
    assert run([FULL]) == ("ok", 1, 0)


def _test_thin_jar_reloads_then_accepts_covered_by_enabled_case():
    assert run([THIN, FULL]) == ("ok", 2, 1)


def _test_thin_jar_never_burns_the_row_covered_by_enabled_case():
    # Thin on every attempt: accept it rather than fail the profile — a thin jar
    # still quotes ~24% of the time, which beats losing the row outright.
    assert run([THIN, THIN, THIN]) == ("ok", 3, 2)


def test_bot_flag_still_raises():
    # The thin-jar branch must not swallow a session Akamai actually refused.
    assert run([FLAGGED, FLAGGED, FLAGGED]) == ("raised", 3, 2)


def test_flagged_then_validated_recovers():
    assert run([FLAGGED, FULL]) == ("ok", 2, 1)


def test_disabled_by_default():
    # Off by default since 2026-09-10: it measured ~0.73x throughput, and the
    # gain behind it was correlational. A thin jar must be accepted at once.
    assert nb.MIN_BOOTSTRAP_COOKIES == 0
    assert run([THIN, FULL]) == ("ok", 1, 0)


def test_thin_retry_when_enabled():
    was = nb.MIN_BOOTSTRAP_COOKIES
    try:
        nb.MIN_BOOTSTRAP_COOKIES = 13
        assert run([THIN, FULL]) == ("ok", 2, 1)
        assert run([THIN, THIN, THIN]) == ("ok", 3, 2)
        assert run([FLAGGED, FLAGGED, FLAGGED]) == ("raised", 3, 2)
    finally:
        nb.MIN_BOOTSTRAP_COOKIES = was


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ok  {name}")
    print("bootstrap jar: all checks passed")
