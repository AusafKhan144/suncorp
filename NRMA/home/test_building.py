# test_building.py — offline checks for building.py's pure logic.
# Run: python3 test_building.py
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])

import building as B
from nrma_browser import FETCH_FORBIDDEN_HEADERS, _abck_validated


def test_building_type():
    """Every spelling seen in delivered inputs must map, not fall back."""
    cases = {
        # the 202608 batch spells it with a space; the map key does not
        "Free standing house": "FREESTANDING_HOME",
        "Freestanding house": "FREESTANDING_HOME",
        "  free   STANDING house ": "FREESTANDING_HOME",
        "Townhouse, duplex or terrace": "TOWNHOUSE_TERRACE_HOUSE_OR_DUPLEX",
        "Townhouse, terrace, duplex": "TOWNHOUSE_TERRACE_HOUSE_OR_DUPLEX",
        "Apartment or unit": "APARTMENT",
        "Hobby farm": "HOME_HOBBY_FARM_OR_LIFESTYLE_BLOCK",
        "Unit in retirement village": "RETIREMENT_VILLAGE_UNIT",
    }
    for raw, expected in cases.items():
        got = B._building_type(raw)
        assert got == expected, f"{raw!r} -> {got!r}, expected {expected!r}"

    # An unknown value still defaults, but must not be silent.
    assert B._building_type("Houseboat") == "FREESTANDING_HOME"
    assert B._building_type("") == "FREESTANDING_HOME"


def test_parse_row_uses_mapping():
    """The regression that priced 33 townhouses as freestanding houses."""
    row = {"Sample_Number": "1", "Building_Type": "Townhouse, duplex or terrace"}
    assert B._parse_row(row).building_type == "TOWNHOUSE_TERRACE_HOUSE_OR_DUPLEX"
    assert B._parse_row({"Sample_Number": "2"}).building_type == "FREESTANDING_HOME"


def test_enum_maps_match_the_live_ui_values():
    """Every mapping must land on a value the NRMA journey actually accepts.

    These enums were read off the live selects/radios. The old maps emitted
    STEEP, 4+, ABOVE_AVERAGE and HIGH — none of which NRMA accepts — and the
    silent fallback meant 7 'Severe' slope rows were priced as flat.
    """
    ui_slope = {"FLAT_GENTLE", "MODERATE", "SEVERE"}
    ui_storeys = {"1", "2", "3", "4PLUS"}
    ui_fixtures = {"STANDARD", "QUALITY", "PRESTIGE"}
    ui_property = {
        "FREESTANDING_HOME", "APARTMENT", "TOWNHOUSE_TERRACE_HOUSE_OR_DUPLEX",
        "RELOCATABLE_OR_TINY_HOME", "HOME_ON_A_FARM",
        "HOME_HOBBY_FARM_OR_LIFESTYLE_BLOCK", "RETIREMENT_VILLAGE_UNIT",
    }
    assert set(B.SLOPE_MAP.values()) <= ui_slope, B.SLOPE_MAP
    assert set(B.STOREY_MAP.values()) <= ui_storeys, B.STOREY_MAP
    assert set(B.FIXTURE_MAP.values()) <= ui_fixtures, B.FIXTURE_MAP
    assert set(B.BUILDING_TYPE.values()) <= ui_property, B.BUILDING_TYPE

    # The specific rows that were being mispriced
    assert B._map_enum("Severe", B._SLOPE_NORM, "FLAT_GENTLE", "Slope") == "SEVERE"
    assert B._map_enum("Steep", B._SLOPE_NORM, "FLAT_GENTLE", "Slope") == "SEVERE"
    assert B._map_enum("4+", B._STOREY_NORM, "1", "Storeys") == "4PLUS"
    assert B._map_enum("Above average", B._FIXTURE_NORM, "STANDARD", "Fx") == "QUALITY"
    assert B._map_enum("High", B._FIXTURE_NORM, "STANDARD", "Fx") == "PRESTIGE"

    # Unmapped input still defaults, and blank input must not warn
    assert B._map_enum("Vertical", B._SLOPE_NORM, "FLAT_GENTLE", "Slope") == "FLAT_GENTLE"
    assert B._map_enum("", B._SLOPE_NORM, "FLAT_GENTLE", "Slope") == "FLAT_GENTLE"


def test_parse_row_applies_fixed_enums():
    row = {
        "Sample_Number": "1", "Slope": "Severe",
        "Storeys_If_House": "4+", "Fixture_Quality": "High",
    }
    p = B._parse_row(row)
    assert p.slope == "SEVERE"
    assert p.storeys == "4PLUS"
    assert p.fixture_quality == "PRESTIGE"


def test_available_excess_tiers():
    """Sweep the tiers the API advertises, in ALL_EXCESS_LEVELS order."""
    resp = {
        "conditions": {
            "Building": {
                "BaseExcess": ["1100", "300", "10000", "750", "99999"],
            }
        }
    }
    # 99999 is not a column we emit, so it is dropped; order follows the grid
    assert B._available_excess_tiers(resp) == [300, 750, 1100, 10000]

    # No conditions block -> the known 13 tiers, all of which are real columns
    fallback = B._available_excess_tiers({})
    assert fallback == list(B.DEFAULT_BUILDING_EXCESS_TIERS)
    assert set(fallback) <= set(B.ALL_EXCESS_LEVELS)

    # Junk values must not blow up the sweep
    assert B._available_excess_tiers(
        {"conditions": {"Building": {"BaseExcess": [None, "", "abc"]}}}
    ) == list(B.DEFAULT_BUILDING_EXCESS_TIERS)


def test_output_columns_unchanged():
    """The excess-request cut must not change the delivered schema."""
    assert len(B.ALL_EXCESS_LEVELS) == 102
    for tier in B.ALL_EXCESS_LEVELS:
        assert f"NRMA_excess_{tier}" in B.TOGGLE_FIELDS
        assert f"NRMAPlus_excess_{tier}" in B.TOGGLE_FIELDS


def test_row_keys_match_the_headers_exactly():
    """Every key a row emits must be a declared column, and vice versa.

    csv.DictWriter is built with extrasaction="ignore", so a key that does not
    match a fieldname is dropped with no error, and a fieldname with no key
    writes an empty cell. Both directions fail silently in a delivered file, so
    the two lists and the two builders have to be pinned against each other.
    """
    r = B.QuoteResult(sample_number="1")
    r.excess_prices = {x: "1.00" for x in B.ALL_EXCESS_LEVELS}
    r.excess_plus_prices = {x: "2.00" for x in B.ALL_EXCESS_LEVELS}

    for fields, row, name in (
        (B.MAIN_FIELDS, B.to_main_row(r), "main"),
        (B.TOGGLE_FIELDS, B.to_toggle_row(r), "toggle"),
    ):
        assert len(fields) == len(set(fields)), f"{name} has duplicate columns"
        missing = set(fields) - set(row)
        extra = set(row) - set(fields)
        assert not missing, f"{name} columns never written: {sorted(missing)}"
        assert not extra, f"{name} keys silently dropped: {sorted(extra)}"

    # The main file is a projection of the toggle file, so a column added to one
    # cannot drift out of the other.
    assert set(B.MAIN_FIELDS) <= set(B.TOGGLE_FIELDS)

    # Toggle carries the sweep and the per-frequency prices; main must not.
    only_toggle = set(B.TOGGLE_FIELDS) - set(B.MAIN_FIELDS)
    assert len(only_toggle) == 2 * len(B.ALL_EXCESS_LEVELS) + 10, sorted(only_toggle)
    assert not [f for f in B.MAIN_FIELDS if "_excess_" in f]


def test_toggle_sweep_columns_carry_their_own_values():
    """A tier with no price must be blank, not inherit a neighbour's."""
    r = B.QuoteResult(sample_number="1")
    r.excess_prices = {300: "3505.64"}
    row = B.to_toggle_row(r)
    assert row["NRMA_excess_300"] == "3505.64"
    assert row["NRMA_excess_400"] == ""
    assert row["NRMAPlus_excess_300"] == ""


def test_failure_reasons_stay_one_readable_line():
    """Akamai's HTML refusal used to land in the CSV cell verbatim.

    It wrapped across several visual rows, carried escaped entities, and buried
    the two facts that matter: which request was refused, and with what status.
    """
    from nrma_browser import short_failure_reason as short

    akamai = (
        "POST https://insurance.nrma.com.au/domain/question/api/question/api/"
        "2.x.x/home/quote/QTEN0022478895 → 403: <HTML><HEAD>\n"
        "<TITLE>Access Denied</TITLE>\n</HEAD><BODY>\n<H1>Access Denied</H1>\n"
        'You don\'t have permission to access ""http&#58;&#47;&#47;insurance"" '
        "on this server.<P>\n"
    )
    assert short(akamai) == (
        "POST home/quote/QTEN0022478895 → 403: Akamai bot block"
    )

    dyna = (
        "POST https://insurance.nrma.com.au/domain/question/api/question/api/"
        '2.x.x/home/quote → 429: {"message":"SIMPLE: DynaCop blocked request"}'
    )
    assert short(dyna).endswith("→ 429: DynaCop rate limit")

    # No markup, no newlines, and short enough to read in a spreadsheet cell.
    for reason in (akamai, dyna, "", "BrowserSessionFailed: Page.goto: NS_ERROR"):
        out = short(reason)
        assert "\n" not in out and "<" not in out and len(out) <= 140

    # Terminal reasons must survive byte-for-byte: _row_is_complete matches them
    # to decide what a resume pass is allowed to skip.
    for reason in (
        "SumInsuredOutOfRange|building|high|target=900000|min=1|max=2",
        "No address found: 1 FAKE ST, NOWHERE NSW 2000",
        "Could not provide estimates for this property",
        "We don't cover properties in your area",
    ):
        assert short(reason) == reason
        assert B._row_is_complete({"Reason_for_quote_failure": short(reason)})

    # And a shortened session refusal must still come back for a retry.
    assert not B._row_is_complete({"Reason_for_quote_failure": short(akamai)})
    assert not B._row_is_complete({"Reason_for_quote_failure": short(dyna)})


def test_retry_classifier():
    """Session refusals retry; real answers about the property never do."""

    def reason(text):
        r = B.QuoteResult(sample_number="1")
        r.failure_reason = text
        return r

    for text in (
        'POST .../home/quote/QTEN1 → 403: <HTML><HEAD>\n<TITLE>Access Denied</TITLE>',
        'POST .../home/quote/QTEN1 → 401: {"error":"Unauthorized"}',
        'POST .../home/quote → 429: {"message":"SIMPLE: DynaCop blocked request"}',
        "AkamaiNotValidated: _abck never validated after 3 bootstrap attempts",
        "BrowserSessionFailed: Page.reload: Timeout 120000ms exceeded.",
        # transport failures from inside the page — a dead residential exit
        "Page.evaluate: NetworkError when attempting to fetch resource.",
        "Page.goto: NS_ERROR_NET_TIMEOUT",
        "Target page, context or browser has been closed",
    ):
        assert B._should_retry_on_new_session(reason(text)), text

    # Terminal answers about this property — retrying would burn IPs and could
    # overwrite a correct result.
    for text in (
        "SumInsuredOutOfRange|building|high|target=900000|min=1|max=2",
        "No address found: 1 FAKE ST, NOWHERE NSW 2000",
        "Could not provide estimates for this property",
        "We don't cover properties in your area",
        "",
    ):
        assert not B._should_retry_on_new_session(reason(text)), text

    # A successful row is never retried
    ok = B.QuoteResult(sample_number="1")
    ok.nrma_yearly = "3460.65"
    assert not B._should_retry_on_new_session(ok)


def test_rate_limit_gets_the_long_backoff():
    """DynaCop 429 is a throttle — it needs time, not just a new IP."""

    def reason(text):
        r = B.QuoteResult(sample_number="1")
        r.failure_reason = text
        return r

    dyna = (
        'POST .../2.x.x/home/quote → 429: {"status":429,"message":"SIMPLE: '
        'DynaCop blocked request \\"cookies.x-iag-user-access\\", '
        '\\"headers.true-client-ip\\"."}'
    )
    assert B._should_retry_on_new_session(reason(dyna))
    assert B._is_rate_limited(reason(dyna))

    # A ban is not a throttle: new IP immediately, no long wait.
    ban = "POST x → 403: <TITLE>Access Denied</TITLE>"
    assert B._should_retry_on_new_session(reason(ban))
    assert not B._is_rate_limited(reason(ban))
    assert not B._is_rate_limited(reason(""))
    assert B.RATE_LIMIT_BACKOFF[0] <= B.RATE_LIMIT_BACKOFF[1]


def test_terminal_reasons_are_not_rerun():
    """Terminal failures stay collected on resume; session refusals come back."""
    assert B._row_is_complete({"Reason_for_quote_failure": "SumInsuredOutOfRange|x"})
    assert B._row_is_complete({"Reason_for_quote_failure": "No address found: x"})
    assert not B._row_is_complete(
        {"Reason_for_quote_failure": "AkamaiNotValidated: nope"}
    )
    assert not B._row_is_complete(
        {"Reason_for_quote_failure": "POST x → 403: Access Denied"}
    )
    assert B._row_is_complete({"NRMA_YearlyPayment": "3460.65"})


def test_abck_validated():
    """Akamai marks bot-suspected sessions with -1 in the second field."""
    assert not _abck_validated(None)
    assert not _abck_validated("")
    assert not _abck_validated("abc~-1~foo~bar")
    assert _abck_validated("abc~0~foo~bar")
    assert not _abck_validated("nosegments")


def test_forbidden_headers():
    """fetch() throws on these; the browser must supply them itself."""
    for name in ("user-agent", "referer", "origin", "cookie", "content-length"):
        assert name in FETCH_FORBIDDEN_HEADERS
    # content-type must survive — the API needs it
    assert "content-type" not in FETCH_FORBIDDEN_HEADERS


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ok  {name}")
    print("all checks passed")
