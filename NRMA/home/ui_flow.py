# ui_flow.py
"""Full browser automation of the NRMA home-buildings quote journey.

This is the sibling of the hybrid transport in `nrma_browser.py`. The hybrid
drives NRMA's JSON API from inside a validated Camoufox page; this module drives
the actual UI — typing the address, picking rooms, reading the rendered premium.

Why keep both:
  * hybrid  — production workhorse, ~60 s/profile
  * full UI — independent verification that the API path returns what a real
              customer sees, and a fallback if NRMA ever blocks `fetch` too

Two facts, both read out of the app's own bundle, make this tractable:

1. Every step is a route under `/get-cover/home/3/`, and deep-linking with
   `?quoteId=<id>` renders that step directly with progress preserved. So a page
   object can jump straight to its step instead of clicking through every
   predecessor.
2. Form controls carry `name` attributes equal to the API answer keys —
   `name="Parties.0.PartyType"` with `value="Individual"`. The same dot-notation
   `building.py` already builds. So fillers are keyed on answer names, not on
   scraped CSS paths that rot on every redeploy.

Selectors confirmed live: `risk-address-question`, `next-button`, `back-button`,
`policy-type-button`, `start-date-input`, `owner-details-question-party-type-input-0`.

Status: routes and navigation are mapped and verified. Per-page fillers are being
filled in behind `probe_journey()`, which dumps each page's real controls so the
remaining mapping is mechanical rather than guesswork. Do not treat this module
as production-ready until `test_ui_flow` covers the page it is used for.
"""

import asyncio
import json
import logging
import os
import re

log = logging.getLogger(__name__)

BASE_URL = "https://insurance.nrma.com.au"
JOURNEY_BASE = f"{BASE_URL}/get-cover/home/3"

# Extracted verbatim from the au-home bundle's route table. Only the routes the
# buildings journey touches are listed; the payment/retrieve/error routes are
# deliberately omitted since this never buys a policy.
ROUTES = {
    "risk_address": "risk-address",
    "address_search_results": "address-search-results",
    "manual_address": "manual-address",
    "prefill": "prefill",
    "choose_policy": "choose-policy-type",
    "customer_situation": "customer-situation",
    "construction_of_home": "construction-of-home",
    "features_of_home": "features-of-home",
    "features_and_fixtures": "features-and-fixtures",
    "rooms_and_structures": "rooms-and-structures",
    "outdoor_structures": "outdoor-structures",
    "cyclone_upgrades": "cyclone-upgrades",
    "occupancy": "occupancy",
    "strata_body_corporate": "strata-body-corporate",
    "about_property": "about-property",
    "farm": "farm",
    "sum_insured": "sum-insured",
    "owner_details": "owner-details",
    "owners_history": "owners-history",
    "claims_history": "claims-history",
    "optional_benefits": "optional-benefits",
    "quote_consent": "quote-consent",
    "quote_summary": "quote-summary",
}

# Order the buildings journey visits them. Pages are conditional — a unit skips
# `farm`, a non-cyclone postcode skips `cyclone-upgrades` — so the driver follows
# whatever route the app actually lands on rather than assuming this sequence.
BUILDINGS_SEQUENCE = (
    "risk_address",
    "prefill",
    "construction_of_home",
    "features_of_home",
    "rooms_and_structures",
    "features_and_fixtures",
    "occupancy",
    "sum_insured",
    "owner_details",
    "owners_history",
    "claims_history",
    "optional_benefits",
    "quote_consent",
)

NAV_TIMEOUT_MS = int(os.getenv("NRMA_UI_NAV_TIMEOUT_MS", "180000"))
# The SPA needs ~75 s to paint its first form over a residential proxy; later
# routes are quicker because the bundles are cached in the page.
RENDER_TIMEOUT_MS = int(os.getenv("NRMA_UI_RENDER_TIMEOUT_MS", "150000"))
SETTLE_MS = int(os.getenv("NRMA_UI_SETTLE_MS", "4000"))
# Individual controls either respond quickly or the page has stalled; waiting the
# default 30 s per click only slows down the retry that actually fixes it.
CLICK_TIMEOUT_MS = int(os.getenv("NRMA_UI_CLICK_TIMEOUT_MS", "8000"))

NEXT = "[data-testid=next-button]"
BACK = "[data-testid=back-button]"

# Collects every visible control on the current step, descending shadow roots.
# `name` is the interesting one — it is the API answer key.
_DUMP_JS = """() => {
  const vis = e => { const r = e.getBoundingClientRect();
      return r.width > 0 && r.height > 0 && getComputedStyle(e).visibility !== 'hidden'; };
  const SEL = 'input,select,textarea,button,[role=button],[role=radio],'
            + '[role=checkbox],[role=combobox],[role=spinbutton]';
  const label = e => {
    if (e.labels && e.labels.length) return e.labels[0].innerText.trim().slice(0, 80);
    if (e.getAttribute('aria-label')) return e.getAttribute('aria-label').slice(0, 80);
    const id = e.getAttribute('id');
    if (id) { const l = document.querySelector('label[for="' + CSS.escape(id) + '"]');
              if (l) return l.innerText.trim().slice(0, 80); }
    const fs = e.closest('fieldset');
    if (fs && fs.querySelector('legend'))
      return '(' + fs.querySelector('legend').innerText.trim().slice(0, 60) + ')';
    return '';
  };
  const out = {url: location.href,
               text: (document.body.innerText || '').replace(/\\s+/g, ' ').slice(0, 2000),
               controls: []};
  const walk = (root) => {
    root.querySelectorAll(SEL).forEach(e => { if (!vis(e)) return;
      out.controls.push({
        tag: e.tagName.toLowerCase(), type: e.getAttribute('type') || '',
        id: e.getAttribute('id') || '', name: e.getAttribute('name') || '',
        testid: e.getAttribute('data-testid') || e.getAttribute('data-test-id') || '',
        label: label(e), value: (e.value || '').slice(0, 40),
        text: (e.innerText || '').trim().slice(0, 55),
        options: e.tagName === 'SELECT'
                 ? [...e.options].map(o => o.value).slice(0, 20) : undefined});
    });
    root.querySelectorAll('*').forEach(e => { if (e.shadowRoot) walk(e.shadowRoot); });
  };
  walk(document);
  return out;
}"""


def _yn(value: bool) -> str:
    """The journey's yes/no radios carry the strings 'true'/'false'."""
    return "true" if value else "false"


def _norm(text: str) -> str:
    return " ".join(str(text or "").split()).upper()


# The fittings radios are unnamed, so they are reached by their visible label.
_FIXTURE_LABEL = {
    "STANDARD": "Standard",
    "QUALITY": "Quality",
    "PRESTIGE": "Prestige",
}


# Where the journey stops being data entry and starts showing money. Confirmed
# live: quote-consent is NOT one of these — at 90% it only asks for an optional
# email address, so treating it as priced yielded a "successful" run with no
# premium at all.
PRICED_ROUTES = ("quote-summary", "quote-price", "price-quote")

# Pages that carry no answers we set — acknowledge and move on.
PASSTHROUGH_ROUTES = (
    "features-and-fixtures",
    "cyclone-upgrades",
    "about-property",
    "farm",
    "customer-situation",
    "choose-policy-type",
)


# route -> filler method name. The driver looks the landed route up here rather
# than assuming a fixed order, because pages are conditional: a unit skips farm,
# a non-cyclone postcode skips cyclone-upgrades.
FILLERS = {
    "risk-address": "fill_risk_address",
    "address-search-results": "fill_address_search_results",
    "prefill": "fill_prefill",
    "construction-of-home": "fill_construction_of_home",
    "features-of-home": "fill_features_of_home",
    "rooms-and-structures": "fill_rooms_and_structures",
    "occupancy": "fill_occupancy",
    "strata-body-corporate": "fill_strata_body_corporate",
    "owners-history": "fill_owners_history",
    "sum-insured": "fill_sum_insured",
    "owner-details": "fill_owner_details",
    "claims-history": "fill_claims_history",
    "quote-consent": "fill_quote_consent",
}


class UIFlowError(RuntimeError):
    """The journey could not be driven to the next step."""


class Retryable(UIFlowError):
    """A failure caused by the session, not by this property's answers.

    Worth a fresh browser and exit IP; a validation error, by contrast, would
    fail identically on every retry and must stay terminal.
    """


class RenderTimeout(Retryable):
    """A step never painted its controls — a slow or half-dead residential exit."""


class AkamaiBlocked(Retryable):
    """Akamai returned 403 to one of the journey's own API calls.

    The same block that broke the hybrid transport, seen from inside the UI: the
    SPA swallows the 403 and paints its generic "we're having a technical
    problem" card, which reads like an NRMA outage. It is not one — it is this
    session's reputation, and only a new IP clears it.
    """


class UIJourney:
    """Drives one quote through the real NRMA UI on an already-validated page.

    Construct it with a page from `nrma_browser.quote_session()` so the Akamai
    session is already validated — this class deliberately does not do its own
    bootstrap, keeping session handling in exactly one place.
    """

    def __init__(self, page, quote_id: str | None = None):
        self.page = page
        self.quote_id = quote_id
        page.set_default_navigation_timeout(NAV_TIMEOUT_MS)
        # The SPA renders every backend refusal as the same generic error card, so
        # the DOM cannot tell an Akamai ban from a real outage. Watch the wire.
        # The record lives on the page, not on self: the sweep builds a second
        # journey over the same page, and a listener each meant every refusal was
        # logged twice while neither instance saw the other's history.
        self.refusals: list[str] = getattr(page, "_nrma_refusals", None)
        if self.refusals is None:
            self.refusals = page._nrma_refusals = []
            page.on("response", self._note_refusal)

    def _note_refusal(self, response) -> None:
        if response.status < 400 or "/domain/question/api/" not in response.url:
            return
        self.refusals.append(f"{response.status} {response.url.split('?')[0]}")
        log.warning("│  API refused: %s", self.refusals[-1])

    async def _abck_state(self) -> str:
        """'validated' or 'bot-flagged', read at the moment of the refusal.

        Tells apart a session that was never trusted from one Akamai downgraded
        part-way through the journey — different problems with different fixes.
        """
        for cookie in await self.page.context.cookies():
            if cookie["name"] == "_abck":
                parts = cookie["value"].split("~")
                flag = parts[1] if len(parts) > 1 else "?"
                return f"{'bot-flagged' if flag == '-1' else 'validated'} (f2={flag})"
        return "absent"

    def blocked_by(self) -> str | None:
        """The most recent 403/429 on a journey API call, if any."""
        for refusal in reversed(self.refusals):
            if refusal.startswith(("403", "429")):
                return refusal
        return None

    def rate_limited(self) -> bool:
        """A 429 since the last backoff — IAG's rate limiter, not Akamai's ban."""
        return any(r.startswith("429") for r in self.refusals)

    # ── navigation ────────────────────────────────────────────────────

    def route_url(self, key: str) -> str:
        route = ROUTES.get(key, key)
        url = f"{JOURNEY_BASE}/{route}"
        return f"{url}?quoteId={self.quote_id}" if self.quote_id else url

    async def start(self, state: str = "NSW") -> str:
        """Land on the journey and let the app mint its own quote id."""
        url = (
            f"{BASE_URL}/get-cover/home?serenityproduct=home-buildings-standard"
            f"&serenitydefaultstate={state}&serenityselectedstate={state}"
        )
        await self.page.goto(url, wait_until="load", timeout=NAV_TIMEOUT_MS)
        await self.wait_rendered()
        self.quote_id = await self.detect_quote_id()
        if not self.quote_id:
            raise UIFlowError("no quoteId after landing on the journey")
        log.info("│  UI journey started, quoteId=%s", self.quote_id)
        return self.quote_id

    async def goto(self, key: str) -> None:
        await self.page.goto(
            self.route_url(key), wait_until="load", timeout=NAV_TIMEOUT_MS
        )
        await self.wait_rendered()

    async def wait_rendered(self) -> None:
        """Block until the step paints controls, not merely until DOM ready.

        The shell renders in a second or two and then spins; only the presence of
        a real control means the micro-frontend has mounted.
        """
        try:
            await self.page.wait_for_function(
                "() => document.querySelectorAll("
                "'input,select,[role=radio],[data-testid=next-button]').length > 0",
                timeout=RENDER_TIMEOUT_MS,
            )
        except Exception as exc:
            raise RenderTimeout(
                f"step did not render within {RENDER_TIMEOUT_MS / 1000:.0f}s "
                f"at {self.page.url}"
            ) from exc
        await self.page.wait_for_timeout(SETTLE_MS)

    async def current_route(self) -> str:
        m = re.search(r"/get-cover/home/3/([a-z0-9\-]+)", self.page.url)
        return m.group(1) if m else ""

    async def detect_quote_id(self) -> str | None:
        return await self.page.evaluate(
            """() => {
                const m = location.href.match(/QTEN[0-9]+/); if (m) return m[0];
                for (const k of Object.keys(sessionStorage)) {
                  const hit = (sessionStorage.getItem(k) || '').match(/QTEN[0-9]+/);
                  if (hit) return hit[0];
                }
                const b = document.body.innerHTML.match(/QTEN[0-9]+/);
                return b ? b[0] : null;
            }"""
        )

    # ── generic field setters, keyed on the API answer name ────────────

    async def set_radio(self, answer_name: str, value: str) -> bool:
        """Tick the radio for an answer key, e.g. Parties.0.PartyType=Individual."""
        loc = self.page.locator(
            f'input[type=radio][name="{answer_name}"][value="{value}"]'
        )
        if not await loc.count():
            return False
        await loc.first.check(force=True, timeout=CLICK_TIMEOUT_MS)
        return True

    async def set_text(self, answer_name: str, value: str) -> bool:
        loc = self.page.locator(f'input[name="{answer_name}"]')
        if not await loc.count():
            return False
        await loc.first.fill(str(value))
        return True

    async def set_by_testid(self, testid: str, value: str) -> bool:
        loc = self.page.locator(f'[data-testid="{testid}"]')
        if not await loc.count():
            return False
        await loc.first.fill(str(value))
        return True

    async def click_testid(self, testid: str) -> bool:
        loc = self.page.locator(f'[data-testid="{testid}"]')
        if not await loc.count():
            return False
        await loc.first.click()
        return True

    async def next(self) -> str:
        """Advance a step and return the route landed on.

        Most steps advance on `next-button`, but some don't have one — the
        sum-insured step commits with a Confirm button instead — so both are
        tried before giving up.
        """
        before = await self.current_route()
        clicked = False
        loc = self.page.locator(NEXT)
        if await loc.count() and await loc.first.is_enabled():
            await loc.first.click()
            clicked = True
        else:
            confirm = self.page.get_by_role(
                "button", name=re.compile(r"confirm|continue|save", re.I)
            )
            if await confirm.count():
                await confirm.first.click()
                clicked = True
        if not clicked:
            # Usually means a required answer is still missing, so the step has
            # not revealed its Next yet. Report the fields so the gap is obvious.
            names = await self.field_names()
            raise UIFlowError(
                f"nothing to advance with on {before or self.page.url}"
                + (f" | fields now: {names}" if names else "")
            )
        try:
            await self.page.wait_for_function(
                "r => !location.pathname.endsWith(r)",
                arg=before,
                timeout=RENDER_TIMEOUT_MS,
            )
        except Exception:
            # Validation errors keep us on the page. Report the field names too,
            # so one failed run tells us what to implement instead of needing a
            # separate recon pass per page.
            errs = await self.validation_errors()
            names = await self.field_names()
            detail = (
                f"stuck on {before}"
                + (f" — {errs}" if errs else " (no error shown)")
                + (f" | fields: {names}" if names else "")
            )
            # A 403 on the parties write is the Akamai block, not an NRMA outage —
            # the card says "technical problem" either way, so trust the status.
            blocked = self.blocked_by()
            if blocked:
                raise AkamaiBlocked(f"{blocked} | _abck {await self._abck_state()} — {detail}")
            if re.search(r"technical problem|try again in a few minutes", detail, re.I):
                raise Retryable(detail)
            raise UIFlowError(detail)
        await self.wait_rendered()
        return await self.current_route()

    async def fill_address_search_results(self, profile) -> None:
        """Confirm the resolved address is the one we asked for.

        This step is a confirmation, not a chooser — the autocomplete has already
        resolved to a single property ("Here's what we found") and offers only
        Back/Next. So the job is to *verify* rather than select: accepting
        whatever it resolved to is how you silently quote the wrong house.
        """
        text = _norm(await self.page.inner_text("body"))
        street, suburb = _norm(profile.address), _norm(profile.suburb)
        postcode = str(profile.postcode).strip()
        missing = [
            label
            for label, token in (
                ("street", street),
                ("suburb", suburb),
                ("postcode", postcode),
            )
            if token and token not in text
        ]
        if missing:
            shown = re.search(r"YOUR HOME (.{0,90})", text)
            raise UIFlowError(
                f"resolved address does not match on {', '.join(missing)} — "
                f"wanted {profile.address}, {profile.suburb} {profile.postcode}; "
                f"page shows {shown.group(1).strip() if shown else text[:90]!r}"
            )
        log.info("│  ✓ address confirmed: %s, %s %s",
                 profile.address, profile.suburb, postcode)

    async def validation_errors(self) -> list[str]:
        return await self.page.evaluate(
            """() => [...document.querySelectorAll(
                 '[role=alert],[class*=error],[class*=Error]')]
               .map(e => (e.innerText || '').trim()).filter(t => t).slice(0, 6)"""
        )

    # ── address entry (the one step with real interaction) ─────────────

    async def enter_address(self, full_address: str) -> None:
        """Type into the autocomplete and take the first matching suggestion."""
        if not await self.set_by_testid("risk-address-question", full_address):
            raise UIFlowError("address input (risk-address-question) not found")
        await self.page.wait_for_timeout(1500)
        # The list is an ARIA combobox; Enter selects the highlighted option.
        options = self.page.locator("[role=option]")
        try:
            await options.first.wait_for(timeout=20000)
            await options.first.click()
        except Exception:
            await self.page.keyboard.press("ArrowDown")
            await self.page.keyboard.press("Enter")

    async def answer_yes_no(self, value: bool) -> bool:
        """Tick Yes/No on a single-question page, by label not field name.

        Several steps are one Yes/No question with identical structure and
        differently-named inputs. Selecting on the visible label handles all of
        them, and survives NRMA renaming the field.
        """
        want = "Yes" if value else "No"
        by_role = self.page.get_by_role("radio", name=re.compile(rf"^\s*{want}\s*$", re.I))
        if await by_role.count():
            await by_role.first.check(force=True)
            return True
        # Fall back to the value convention used elsewhere in the journey.
        by_value = self.page.locator(f'input[type=radio][value="{_yn(value)}"]')
        if await by_value.count():
            await by_value.first.check(force=True)
            return True
        return False

    async def field_names(self) -> list[str]:
        """Answer-keyed control names on this step — for self-documenting errors."""
        return await self.page.evaluate(
            """() => [...new Set([...document.querySelectorAll(
                 'input[name],select[name]')].map(e => e.getAttribute('name')))]
               .filter(Boolean).slice(0, 20)"""
        )

    async def set_select(self, name: str, value: str) -> bool:
        loc = self.page.locator(f'select[name="{name}"]')
        if not await loc.count():
            return False
        await loc.first.select_option(value)
        return True

    async def set_counter(self, key: str, target: int, max_reset: int = 16) -> None:
        """Drive a +/- stepper to `target`.

        The steppers expose no readable value, so this zeroes them first (clicking
        decrement until it goes disabled) and then increments exactly `target`
        times. Deterministic without having to scrape the displayed number.
        """
        dec = self.page.locator(f'[data-testid="{key}-decrement"]')
        inc = self.page.locator(f'[data-testid="{key}-increment"]')
        if not await dec.count() or not await inc.count():
            log.debug("│  counter %s not on this step", key)
            return
        # A stepper that will not move means the value stays NRMA's, not ours, so
        # this must never be swallowed — but it is a stalled page rather than a
        # driver defect, so it asks for a fresh session instead of failing the
        # profile. The short timeout keeps that decision quick.
        try:
            for _ in range(max_reset):
                if await dec.first.is_disabled():
                    break
                await dec.first.click(timeout=CLICK_TIMEOUT_MS)
            for _ in range(max(0, target)):
                if await inc.first.is_disabled():
                    log.warning("│  counter %s capped below target %d", key, target)
                    break
                await inc.first.click(timeout=CLICK_TIMEOUT_MS)
        except Exception as exc:
            blocker = await self._blocker(f'[data-testid="{key}-increment"]')
            raise Retryable(
                f"counter {key} would not move: {_why(exc)}{blocker}"
            ) from exc

    async def _blocker(self, selector: str) -> str:
        """Name whatever sits over `selector`, if anything does.

        Playwright reports a covered control as a plain timeout, which reads like
        a slow page. Asking the document what is actually at that point turns it
        into a specific answer — a sticky footer, a cookie bar, a spinner.
        """
        try:
            found = await self.page.evaluate(
                """sel => {
                     const el = document.querySelector(sel);
                     if (!el) return 'element absent';
                     const r = el.getBoundingClientRect();
                     if (!r.width || !r.height) return 'element has no box';
                     const at = document.elementFromPoint(
                       r.left + r.width / 2, r.top + r.height / 2);
                     if (!at || el.contains(at) || at.contains(el)) return '';
                     return `covered by <${at.tagName.toLowerCase()}`
                       + `${at.getAttribute('data-testid')
                            ? ' testid=' + at.getAttribute('data-testid') : ''}`
                       + `${at.className ? ' class=' + String(at.className).slice(0, 40) : ''}>`;
                   }""",
                selector,
            )
        except Exception:
            return ""
        return f" | {found}" if found else ""

    # ── per-page fillers ───────────────────────────────────────────────
    # Field names and enum values below were read off the rendered journey by
    # probe_journey(); they are the same answer keys building.py already builds.

    async def fill_risk_address(self, profile) -> None:
        full = (
            f"{profile.address}, {profile.suburb} {profile.state} {profile.postcode}"
        )
        await self.enter_address(full)

    async def fill_construction_of_home(self, profile) -> None:
        await self.set_by_testid(
            "home-year-built-question-year-input", profile.building_year
        )
        await self.set_radio("Storeys", profile.storeys)
        await self.set_select("roof-material-question", profile.roof_type)
        await self.set_select("external-wall-material-question", profile.construction_material)
        await self.set_radio("LandSlope", profile.slope)
        await self.set_radio(
            "how-far-above-ground-level-question-input", _yn(profile.elevated)
        )

    async def fill_features_of_home(self, profile) -> None:
        await self.set_radio(
            "BuiltInHeatingOrCooling", _yn(profile.ducted_air_conditioning)
        )
        await self.set_radio("InGroundPool", _yn(profile.pool))
        await self.set_radio("TennisCourt", _yn(profile.tennis_court))
        await self.set_radio("SolarPanels", _yn(profile.solar_panels))
        await self.set_radio("jetty-question-input", _yn(profile.jetty))
        await self.set_fixture_quality(profile.fixture_quality)

    async def fill_rooms_and_structures(self, profile) -> None:
        await self.set_select("PropertyType", profile.building_type)
        for key, value in (
            ("SmallBedrooms", profile.small_bedrooms),
            ("MediumBedrooms", profile.medium_bedrooms),
            ("LargeBedrooms", profile.large_bedrooms),
            ("Bathrooms", profile.bathrooms),
            ("ParkingSpacesCarport", profile.carport),
            ("ParkingSpacesGarage", profile.garage),
            ("SmallSheds", profile.small_shed),
            ("LargeSheds", profile.large_shed),
            ("Verandahs", profile.verandahs),
            ("BalconiesAndDecks", profile.balconies),
        ):
            await self.set_counter(key, value)

    # Counter steppers are buttons with testids, not named inputs, so they need
    # their own presence probe alongside field_names().
    COUNTER_KEYS = (
        "SmallBedrooms",
        "MediumBedrooms",
        "LargeBedrooms",
        "Bathrooms",
        "ParkingSpacesCarport",
        "ParkingSpacesGarage",
        "SmallSheds",
        "LargeSheds",
        "Verandahs",
        "BalconiesAndDecks",
    )

    async def present_keys(self) -> set[str]:
        """Every answer key currently editable on the page, named or stepper."""
        named = set(await self.field_names())
        steppers = await self.page.evaluate(
            """keys => keys.filter(k =>
                 document.querySelector(`[data-testid="${k}-increment"]`) !== null)""",
            list(self.COUNTER_KEYS),
        )
        # The year field is a testid, not a name.
        extra = set()
        if await self.page.locator(
            '[data-testid="home-year-built-question-year-input"]'
        ).count():
            extra.add("YearConstructed")
        if await self._fixture_radios().count():
            extra.add("QualityOfFixtures")
        return named | set(steppers) | extra

    def _fixture_radios(self):
        """The Standard/Quality/Prestige radios, which carry no `name` attribute.

        Because they are unnamed, field_names() cannot see them and set_radio()
        cannot set them — the fittings section looked empty and silently kept
        NRMA's own answer. "Prestige" is unique enough on the page to serve as the
        section's presence marker.
        """
        return self.page.locator(
            ",".join(
                f'input[type=radio][value="{v}"]'
                for v in ("STANDARD", "QUALITY", "PRESTIGE")
            )
        ).or_(self.page.get_by_role("radio", name=re.compile(r"^\s*prestige\b", re.I)))

    async def set_fixture_quality(self, value: str) -> bool:
        if await self.set_radio("QualityOfFixtures", value):
            return True
        by_value = self.page.locator(f'input[type=radio][value="{value}"]')
        if await by_value.count():
            await by_value.first.check(force=True)
            return True
        label = _FIXTURE_LABEL.get(value)
        if label:
            loc = self.page.get_by_role(
                "radio", name=re.compile(rf"^\s*{label}\b", re.I)
            )
            if await loc.count():
                await loc.first.check(force=True)
                return True
        return False

    async def apply_property_answers(self, profile, only=None) -> list[str]:
        """Set property answers on the current page, optionally restricted.

        Used where fields appear inline rather than on their own route — the
        prefill page's editable sections. Passing `only` confines the writes to
        one section's keys, which matters because opening a section leaves the
        previously opened ones expanded: without it, every section re-applies the
        earlier ones and the counters get churned repeatedly.
        """
        applied: list[str] = []

        def wanted(key: str) -> bool:
            return only is None or key in only

        async def radio(name: str, value: str) -> None:
            if wanted(name) and await self.set_radio(name, value):
                applied.append(name)

        async def select(name: str, value: str) -> None:
            if wanted(name) and await self.set_select(name, value):
                applied.append(name)

        # construction
        if wanted("YearConstructed") and await self.set_by_testid(
            "home-year-built-question-year-input", profile.building_year
        ):
            applied.append("YearConstructed")
        await radio("Storeys", profile.storeys)
        await radio("LandSlope", profile.slope)
        await radio(
            "how-far-above-ground-level-question-input", _yn(profile.elevated)
        )
        await select("roof-material-question", profile.roof_type)
        await select("external-wall-material-question", profile.construction_material)
        # features
        await radio("BuiltInHeatingOrCooling", _yn(profile.ducted_air_conditioning))
        await radio("InGroundPool", _yn(profile.pool))
        await radio("TennisCourt", _yn(profile.tennis_court))
        await radio("SolarPanels", _yn(profile.solar_panels))
        await radio("jetty-question-input", _yn(profile.jetty))
        if wanted("QualityOfFixtures") and await self.set_fixture_quality(
            profile.fixture_quality
        ):
            applied.append("QualityOfFixtures")
        # rooms and structures
        await select("PropertyType", profile.building_type)
        for key, value in (
            ("SmallBedrooms", profile.small_bedrooms),
            ("MediumBedrooms", profile.medium_bedrooms),
            ("LargeBedrooms", profile.large_bedrooms),
            ("Bathrooms", profile.bathrooms),
            ("ParkingSpacesCarport", profile.carport),
            ("ParkingSpacesGarage", profile.garage),
            ("SmallSheds", profile.small_shed),
            ("LargeSheds", profile.large_shed),
            ("Verandahs", profile.verandahs),
            ("BalconiesAndDecks", profile.balconies),
        ):
            if not wanted(key):
                continue
            if await self.page.locator(f'[data-testid="{key}-increment"]').count():
                await self.set_counter(key, value)
                applied.append(key)
        return applied

    async def fill_prefill(self, profile) -> None:
        """Overwrite NRMA's prefilled property details with the CSV's values.

        NRMA prefills this page from its own property data, and accepting it
        quotes *their* house rather than ours — for 7 Colvin St it claimed
        0/5/0 bedrooms and 3 bathrooms where the CSV says 1/2/1 and 4, which moved
        the annual premium by ~7%. So every section gets edited.

        Each section's Edit link navigates to the same route the standalone step
        uses (construction-of-home, rooms-and-structures, …), so this reuses the
        existing fillers rather than duplicating field knowledge. Returning is via
        deep link, which is reliable regardless of where Next would have gone.
        """
        edited: list[str] = []
        # The page mixes link- and button-role Edits across its sections, so both
        # must be collected. Preferring one and only falling back when the other
        # was absent found 3 of 6 and silently skipped bedrooms, construction and
        # fittings — the fields that matter most.
        edit = re.compile(r"^\s*edit\s*$", re.I)
        links = self.page.get_by_role("link", name=edit).or_(
            self.page.get_by_role("button", name=edit)
        )
        # Expanding a section *removes* its own Edit link, so the list shrinks and
        # reindexes under us. Walking nth(0..n) against a shrinking list skipped
        # every second section — which is exactly how bedrooms, construction and
        # fittings went unedited. Always take the first remaining Edit, and only
        # advance the cursor when a click failed to consume one.
        cursor = 0
        for index in range(12):  # bounded: 6 sections today
            count = await links.count()
            if index == 0:
                log.info("│  prefill: %d editable section(s) found", count)
            if cursor >= count:
                break
            before = await self.present_keys()
            await links.nth(cursor).click()
            await self.page.wait_for_timeout(2500)  # the section expands in place
            if await links.count() >= count:
                cursor += 1  # that Edit stayed put; step past it next time
            after = await self.present_keys()

            # Edit expands the section inline rather than navigating. Scope the
            # writes to the keys this click revealed: earlier sections stay
            # expanded, so applying everything would re-churn them each time.
            revealed = after - before
            applied = await self.apply_property_answers(profile, only=revealed)
            if applied:
                edited.append(f"section{index}:{','.join(sorted(applied))}")
            else:
                # Dump the controls this section actually rendered. An expanded
                # section that reveals nothing means its inputs are unnamed (as
                # the fittings radios are), and without this the log gives no way
                # to tell that apart from a section that failed to open.
                unnamed = await self.page.evaluate(
                    """() => [...document.querySelectorAll(
                         'input:not([name]),select:not([name])')]
                       .slice(0, 8).map(e => e.outerHTML.slice(0, 140))"""
                )
                log.warning(
                    "│  prefill section %d revealed no known fields (revealed=%s)"
                    " | unnamed controls: %s",
                    index, sorted(revealed) or "nothing", unnamed,
                )
            # Commit the section if it has its own Save/Done control.
            for label in ("save", "done", "update", "confirm"):
                btn = self.page.get_by_role(
                    "button", name=re.compile(rf"^\s*{label}\s*$", re.I)
                )
                if await btn.count():
                    await btn.first.click()
                    await self.page.wait_for_timeout(2000)
                    break

        log.info(
            "│  prefill: applied CSV values to %d section(s)%s",
            len(edited), " — " + "; ".join(edited) if edited else "",
        )
        if not edited:
            raise UIFlowError(
                "prefill page exposed no editable sections — refusing to accept "
                "NRMA's property data silently"
            )

    async def fill_occupancy(self, profile) -> None:
        await self.set_radio("PropertyOccupancyType", profile.occupancy_type)

    async def fill_strata_body_corporate(self, profile) -> None:
        """'Is your home under a strata scheme or owners corporation?'"""
        if not await self.answer_yes_no(profile.strata):
            raise UIFlowError("strata Yes/No not found")

    async def fill_owners_history(self, profile) -> None:
        """Fraud conviction and previously-refused-insurance declarations."""
        await self.set_radio("fraud-conviction-input", _yn(profile.fraud))
        await self.set_radio(
            "insurance-declined-question-input", _yn(profile.insurance_refused)
        )

    async def fill_sum_insured(self, profile) -> None:
        """Type the target sum insured and confirm.

        NRMA validates against its own calculated range here; when the target is
        outside it the page raises a validation modal rather than advancing, which
        is the UI equivalent of the API's SumInsuredOutOfRange.
        """
        if not await self.set_by_testid(
            "sum-insured-input", str(profile.buildings_sum_insured)
        ):
            raise UIFlowError("sum-insured-input not found")
        confirm = self.page.get_by_role("button", name=re.compile("confirm", re.I))
        if await confirm.count():
            await confirm.first.click()

    async def fill_owner_details(self, profile) -> None:
        """Primary policyholder card: type, DOB, gender, membership, then Save.

        Choosing Person expands the card with the fields that actually move the
        premium — DOB, gender and the NRMA-member discount question. The card
        commits with its own Save button; the page-level Next only appears once
        the card is saved.
        """
        party_type = "Individual" if profile.owner_type == "Person" else "Business"
        if not await self.set_radio("Parties.0.PartyType", party_type):
            label = "Person" if party_type == "Individual" else "Business"
            by_label = self.page.get_by_role(
                "radio", name=re.compile(rf"^\s*{label}\s*$", re.I)
            )
            if not await by_label.count():
                raise UIFlowError("policyholder type radios not found")
            await by_label.first.check(force=True)
        await self.page.wait_for_timeout(2500)  # let the card expand

        # The DOB field carries no name or testid — only its placeholder.
        dob = f"{profile.dob_dd:02d}/{profile.dob_mm:02d}/{profile.dob_yyyy}"
        # Matched on the placeholder via CSS: a regex here would be serialised
        # into an invalid selector because DD/MM/YYYY contains slashes.
        dob_input = self.page.locator(
            'input[placeholder*="YYYY"], input[placeholder*="yyyy"]'
        )
        if not await dob_input.count():
            raise UIFlowError("date-of-birth input not found on owner-details")
        await dob_input.first.fill(dob)

        await self.set_radio("Parties.0.PartyGender", profile.gender)
        # "Are you an existing NRMA Insurance customer or NRMA member?" — drives
        # the member discount. Note the hybrid hardcodes IsCustomer=False and
        # never reads profile.membership, so the two paths can differ here.
        if not await self.set_radio(
            "Parties.0.IsCustomer", _yn(profile.membership)
        ):
            await self.answer_yes_no(profile.membership)

        saved = self.page.get_by_role("button", name=re.compile(r"^\s*save\s*$", re.I))
        if await saved.count():
            await saved.first.click()
            await self.page.wait_for_timeout(3000)

    async def fill_claims_history(self, profile) -> None:
        count = "1" if (profile.claims_5yrs and profile.claim_type) else "0"
        await self.set_radio("Parties.0.HowManyPriorClaims", count) or await self.set_select(
            "Parties.0.HowManyPriorClaims", count
        )

    async def fill_quote_consent(self, profile) -> None:
        """'We're here to help with your quote' — an optional email address.

        Left deliberately blank: supplying one has NRMA email the address about
        the quote, and these are dummy addresses. Nothing here affects price.
        """
        return None

    # ── recon ─────────────────────────────────────────────────────────

    async def dump(self) -> dict:
        """Every visible control on the current step, with its answer key."""
        return await self.page.evaluate(_DUMP_JS)


_MONEY = re.compile(r"\$\s?([0-9][0-9,]*(?:\.[0-9]{2})?)")


def _money(text: str) -> str:
    """First dollar amount in a blob, normalised to a bare number."""
    m = _MONEY.search(text or "")
    return m.group(1).replace(",", "") if m else ""


async def drive(page, profile, max_steps: int = 30, trace_dir: str | None = None) -> dict:
    """Drive one profile through the real UI until the quote prices.

    Follows whichever route the app lands on rather than a fixed sequence —
    pages are conditional, so assuming an order breaks on the first unit or
    non-cyclone postcode. Returns {route, quote_id, steps, dump} for the priced
    page; raises UIFlowError with the page and its validation messages otherwise.
    """
    journey = UIJourney(page)
    await journey.start(profile.state or "NSW")
    seen: list[str] = []

    for step in range(1, max_steps + 1):
        route = await journey.current_route()
        seen.append(route)
        if trace_dir:
            os.makedirs(trace_dir, exist_ok=True)
            await page.screenshot(
                path=os.path.join(trace_dir, f"{step:02d}_{route or 'unknown'}.png"),
                full_page=True,
            )
        if route in PRICED_ROUTES:
            log.info("│  UI journey priced at %s after %d steps", route, step)
            return {
                "route": route,
                "quote_id": journey.quote_id,
                "steps": seen,
                "dump": await journey.dump(),
            }

        if route in FILLERS:
            try:
                await getattr(journey, FILLERS[route])(profile)
            except Exception:
                # Whatever went wrong is on screen right now; without this the
                # only evidence is a truncated selector and diagnosis costs a
                # whole extra session.
                if trace_dir:
                    await page.screenshot(
                        path=os.path.join(trace_dir, f"{step:02d}_{route}_FAILED.png"),
                        full_page=True,
                    )
                raise
        elif route not in PASSTHROUGH_ROUTES:
            # An unmapped page is a real gap, not something to click past: doing
            # so would submit whatever defaults it shipped with and quietly
            # produce a quote for the wrong property.
            raise UIFlowError(
                f"unmapped step {route!r} (visited {seen}) — add a filler or "
                f"list it in PASSTHROUGH_ROUTES once confirmed answer-free"
            )

        if trace_dir:
            # The pre-fill shot above cannot show whether a filler took effect,
            # or what selecting an option revealed. This one can.
            await page.screenshot(
                path=os.path.join(
                    trace_dir, f"{step:02d}_{route or 'unknown'}_filled.png"
                ),
                full_page=True,
            )
        await journey.next()

    raise UIFlowError(f"journey did not price within {max_steps} steps: {seen}")


_PER_YEAR = re.compile(r"That's\s*\$([0-9][0-9,]*(?:\.[0-9]{2})?)\s*per year", re.I)


async def read_price(page) -> dict:
    """Read the currently displayed premium off quote-summary.

    Confirmed field names: the headline sits in `[data-testid=quote-price]` at the
    frequency shown on `[data-testid=frequency-button]`, and the annual equivalent
    is stated in prose as "That's $X per year". The annual figure is the one worth
    trusting for comparison — it needs no frequency arithmetic.
    """
    data = await page.evaluate(
        """() => {
            const t = s => { const e = document.querySelector(s);
                             return e ? (e.innerText || '').replace(/\\s+/g, ' ').trim() : ''; };
            const checked = n => { const e = document.querySelector(
                                     `input[name="${n}"]:checked`);
                                   return e ? e.value : ''; };
            return {
                headline: t('[data-testid=quote-price]'),
                frequency: t('[data-testid=frequency-button]'),
                level: checked('LevelOfCover'),
                excess: checked('BuildingExcess'),
                body: (document.body.innerText || '').replace(/\\s+/g, ' ').slice(0, 3000),
            };
        }"""
    )
    annual = _PER_YEAR.search(data.get("body", ""))
    return {
        "headline": _money(data["headline"]),
        "frequency": data["frequency"],
        "level": data["level"],
        "excess": data["excess"],
        "annual": annual.group(1).replace(",", "") if annual else "",
    }


async def extract_prices(page) -> dict:
    """Back-compat wrapper plus the raw labelled rows, for tracing."""
    price = await read_price(page)
    raw = await page.evaluate(
        """() => {
            const out = {rows: [], money: []};
            const text = (document.body.innerText || '').replace(/\\s+/g, ' ');
            document.querySelectorAll('[data-testid]').forEach(e => {
                const t = (e.innerText || '').trim();
                if (t.includes('$') && t.length < 220)
                    out.rows.push({testid: e.getAttribute('data-testid'),
                                   text: t.replace(/\\s+/g, ' ').slice(0, 160)});
            });
            (text.match(/\\$\\s?[0-9][0-9,]*(?:\\.[0-9]{2})?/g) || [])
                .forEach(m => out.money.push(m));
            return out;
        }"""
    )
    return {**raw, **price}


REPRICE_SETTLE_MS = int(os.getenv("NRMA_UI_REPRICE_MS", "5000"))


async def _reprice(page, previous: str) -> dict:
    """Wait for the premium to refresh after changing cover level or excess.

    Waits for the displayed annual figure to actually change, then falls back to a
    fixed settle — two tiers can legitimately price the same, so a changed value
    is a useful signal but not a requirement.
    ponytail: polling the rendered figure, not the underlying request. Hook the
    /price response instead if this proves flaky under load.
    """
    for _ in range(int(REPRICE_SETTLE_MS / 500) + 12):
        await page.wait_for_timeout(500)
        now = await read_price(page)
        if now["annual"] and now["annual"] != previous:
            await page.wait_for_timeout(800)  # let the DOM settle on the new value
            return await read_price(page)
    # Fall back to a fixed settle, then re-read: the first tier of a sweep can
    # catch the page mid-update and report a blank annual.
    await page.wait_for_timeout(REPRICE_SETTLE_MS)
    final = await read_price(page)
    if not final["annual"]:
        await page.wait_for_timeout(3000)
        final = await read_price(page)
    return final


def _brief(exc: Exception, limit: int = 120) -> str:
    return " ".join(str(exc).split())[:limit]


def _why(exc: Exception) -> str:
    """The informative tail of a Playwright error.

    Playwright puts the reason a click failed at the *end* of its call log
    ("element is not stable", "intercepts pointer events"), so truncating the
    message from the front keeps only "Timeout 30000ms exceeded" — the symptom
    without the cause.
    """
    lines = [line.strip(" -") for line in str(exc).splitlines() if line.strip(" -")]
    head, tail = lines[:1], lines[-2:]
    return " | ".join(dict.fromkeys(head + tail))[:300]


SWEEP_PAUSE_MS = int(os.getenv("NRMA_UI_SWEEP_PAUSE_MS", "2500"))
RATE_PAUSE_MS = int(os.getenv("NRMA_UI_RATE_PAUSE_MS", "45000"))


async def _pace(journey, page) -> None:
    """Space out the price reads, backing off hard once DynaCop throttles them.

    The sweep fires one /price PUT per tier per level. Left unpaced it trips IAG's
    rate limiter, and a throttled page stops committing radio selections at all —
    which is why losing the tier reads also lost the cover-level switch.
    """
    if journey.rate_limited():
        log.warning("│  rate limited — backing off %ds", RATE_PAUSE_MS // 1000)
        await page.wait_for_timeout(RATE_PAUSE_MS)
        journey.refusals.clear()
    else:
        await page.wait_for_timeout(SWEEP_PAUSE_MS)


def _accept(reading: dict, tier) -> str:
    """The premium, but only if the page agrees it belongs to `tier`.

    The rendered figure lags the click, and a refused selection leaves the old
    one on screen. Reading it anyway files a real premium under the wrong excess —
    indistinguishable from data in the output CSV. The page tells us which radio
    is checked, so that, not our own click, decides what gets recorded.
    """
    if not reading.get("annual"):
        return ""
    return "" if str(reading.get("excess")) != str(tier) else reading["annual"]


async def _pick(journey, name: str, value: str) -> bool:
    """set_radio, but a selection the app refuses to commit is not fatal.

    While throttled, React never applies the click and Playwright's own state
    assertion raises — worth a warning and a skipped reading, not a dead quote.
    """
    try:
        return await journey.set_radio(name, value)
    except Exception as exc:
        log.warning("│  %s=%s did not take: %s", name, value, _brief(exc, 60))
        return False


async def _expand_tiers(journey, page, tier: str) -> None:
    """Make `tier`'s radio clickable, expanding the excess list if it collapsed."""
    radio = page.locator(f'input[name="BuildingExcess"][value="{tier}"]')
    if await radio.count() and not await radio.first.is_visible():
        await journey.click_testid("building-excess-expand-button")
        await page.wait_for_timeout(1200)


async def sweep_quote(page, tiers=None, levels=("STANDARD", "PLUS")) -> dict:
    """On quote-summary, read the annual premium for each cover level and excess.

    Returns {level: {excess_tier: annual}} plus the default excess per level. This
    is the UI equivalent of the hybrid's excess sweep — same `LevelOfCover` and
    `BuildingExcess` values, read from the rendered page instead of the price API.
    """
    journey = UIJourney(page)
    # Reveal the full tier list; only six of thirteen show until expanded.
    await journey.click_testid("building-excess-expand-button")
    await page.wait_for_timeout(1500)
    available = await page.evaluate(
        """() => [...document.querySelectorAll('input[name="BuildingExcess"]')]
             .map(e => e.value)"""
    )
    wanted = [str(t) for t in (tiers or available) if str(t) in available]
    log.info("│  UI sweep: %d tiers available %s", len(available), available)

    out: dict = {"available_tiers": available, "levels": {}, "default_excess": {}}
    for level in levels:
        try:
            current = await read_price(page)
            if current["level"] != level:
                await _pace(journey, page)
                if not await _pick(journey, "LevelOfCover", level):
                    log.warning("│  cover level %s not selectable", level)
                    continue
                current = await _reprice(page, current["annual"])
            # Only the first level's default is real. By the time the next level
            # is selected the excess radio is wherever the previous sweep left it,
            # so reporting that as a default invents one — PLUS came back as
            # "default 10000" purely because STANDARD's sweep ended there.
            out["default_excess"][level] = None if out["levels"] else current["excess"]
        except Exception as exc:  # losing PLUS must not lose STANDARD too
            log.warning("│  cover level %s failed: %s", level, _brief(exc))
            continue
        prices: dict = {}
        for tier in wanted:
            # Rebound every tier. Leaving this to carry over from the previous
            # iteration recorded the last tier's premium under the new tier when a
            # selection failed to take — three PLUS tiers came back as identical
            # copies of the 4000 reading, which looks like real data.
            after = {"annual": "", "excess": ""}
            try:
                before = (await read_price(page))["annual"]
                # Two attempts: a tier that comes back blank because DynaCop
                # throttled the /price call is worth re-reading after a backoff,
                # since the sweep is what provoked the throttle in the first place.
                for attempt in (1, 2):
                    await _pace(journey, page)
                    # The tier list re-collapses after every reprice, leaving the
                    # last seven radios present in the DOM but hidden — a forced
                    # click on a hidden input silently fails ("did not change its
                    # state"), which killed the sweep right after the six visible
                    # tiers.
                    await _expand_tiers(journey, page, tier)
                    if not await _pick(journey, "BuildingExcess", tier):
                        continue
                    after = await _reprice(page, before)
                    if after["annual"] or not journey.rate_limited():
                        break
                annual = _accept(after, tier)
                if after["annual"] and not annual:
                    log.warning(
                        "│    %s excess %s discarded — page shows excess %s",
                        level, tier, after["excess"] or "none",
                    )
                prices[int(tier)] = annual
                log.info("│    %s excess %-6s -> %s/yr", level, tier, annual or "-")
            except Exception as exc:  # one bad tier must not discard the quote
                log.warning("│    %s excess %s failed: %s", level, tier, _brief(exc))
                prices[int(tier)] = ""
        out["levels"][level] = prices
    return out


async def probe_journey(page, out_dir: str, sequence=BUILDINGS_SEQUENCE) -> dict:
    """Walk the journey by deep link and dump each step's real controls.

    This is the mapping tool: it turns "what are this page's fields called" into
    one command per run instead of a bespoke script, and it doubles as a canary —
    when NRMA renames a testid, the diff shows up here before the driver breaks.
    Returns {route: dump}.
    """
    os.makedirs(out_dir, exist_ok=True)
    journey = UIJourney(page)
    await journey.start()
    found: dict = {}
    for key in sequence:
        route = ROUTES.get(key, key)
        try:
            await journey.goto(key)
        except UIFlowError as exc:
            log.warning("  [%s] did not render: %s", route, exc)
            continue
        dump = await journey.dump()
        found[route] = dump
        with open(os.path.join(out_dir, f"{route}.json"), "w") as fh:
            json.dump(dump, fh, indent=1)
        await page.screenshot(path=os.path.join(out_dir, f"{route}.png"), full_page=True)
        named = [c for c in dump["controls"] if c["name"]]
        log.info(
            "  [%-22s] %2d controls, %2d answer-keyed | %s",
            route,
            len(dump["controls"]),
            len(named),
            ", ".join(sorted({c["name"] for c in named})[:6]) or "-",
        )
    return found


async def with_session(state: str, run, attempts: int | None = None):
    """Run `run(session)` on a validated session, retrying dead exit IPs.

    A burnt or unresponsive residential exit shows up as a navigation timeout
    during bootstrap, before any journey work happens; that deserves a fresh
    browser rather than losing the run.
    """
    try:
        from .nrma_browser import quote_session
    except ImportError:
        from nrma_browser import quote_session

    attempts = attempts or int(os.getenv("NRMA_UI_ATTEMPTS", "4"))
    for attempt in range(1, attempts + 1):
        try:
            async with quote_session(state) as session:
                return await run(session)
        except Retryable as exc:
            # Environmental: a slow/dead exit never painted. Retry on a new IP.
            log.warning(
                "session attempt %d/%d: %s", attempt, attempts, str(exc)[:120]
            )
            if attempt == attempts:
                raise
            await asyncio.sleep(3)
            continue
        except UIFlowError:
            raise  # a real journey defect is ours to fix, not the IP's
        except Exception as exc:
            log.warning(
                "session attempt %d/%d failed: %s: %s",
                attempt, attempts, type(exc).__name__, _why(exc),
            )
            if attempt == attempts:
                raise
            await asyncio.sleep(3)


async def _drive_one(out: str, which: str = "0") -> None:
    """Drive one profile through the real UI and report the quote page.

    `which` picks the profile: an index, a Sample_Number, or "random". Testing the
    same first profile forever hides input-specific breakage, so once the parsing
    is trusted the random pick is the honest check.
    """
    try:
        from .nrma_browser import quote_session
        from . import building as B
    except ImportError:
        from nrma_browser import quote_session
        import building as B

    profiles = B.load_profiles(B.INPUT_MAIN_CSV)
    if which == "random":
        import random as _random

        profile = _random.choice(profiles)
        log.info("│  randomly selected sample %s", profile.sample_number)
    elif which.replace(".", "").isdigit() and any(
        p.sample_number == which for p in profiles
    ):
        profile = next(p for p in profiles if p.sample_number == which)
    else:
        profile = profiles[int(which) if which.isdigit() else 0]
    log.info(
        "▶ UI drive profile=%s | %s, %s %s %s",
        profile.sample_number, profile.address, profile.suburb,
        profile.state, profile.postcode,
    )
    async def _run(session):
        result = await drive(session.page, profile, trace_dir=out)
        prices = await extract_prices(session.page)
        sweep = await sweep_quote(session.page) if os.getenv("NRMA_UI_SWEEP") else {}
        return result, prices, sweep

    result, prices, sweep = await with_session(profile.state or "NSW", _run)
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "priced.json"), "w") as fh:
        json.dump({"result": result, "prices": prices, "sweep": sweep}, fh, indent=1)
    print(f"\npriced at {result['route']} (quote {result['quote_id']})")
    print(f"steps: {' -> '.join(result['steps'])}")
    print(f"\nlevel={prices.get('level')} excess={prices.get('excess')} "
          f"{prices.get('frequency')}=${prices.get('headline')} annual=${prices.get('annual')}")
    if sweep:
        for level, tiers in sweep.get("levels", {}).items():
            print(f"  {level:9} default_excess={sweep['default_excess'].get(level)} "
                  f"{ {k: v for k, v in sorted(tiers.items())} }")
    print(f"\nlabelled amounts ({len(prices['rows'])}):")
    for row in prices["rows"][:25]:
        print(f"   {row['testid']:44} {row['text'][:70]}")
    print(f"\nall amounts seen: {prices['money'][:20]}")
    print(f"\ntrace + priced.json written to {out}/")


async def main() -> None:
    """python3 ui_flow.py map [dir]   — dump every route's controls
    python3 ui_flow.py drive [dir] — drive one profile to a priced quote
    """
    import sys

    try:
        from .nrma_browser import quote_session
    except ImportError:
        from nrma_browser import quote_session

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
    )
    argv = sys.argv[1:]
    cmd = argv[0] if argv and argv[0] in ("map", "drive") else "map"
    rest = [a for a in argv if a != cmd]
    if cmd == "drive":
        await _drive_one(
            rest[0] if rest else "ui_drive", rest[1] if len(rest) > 1 else "0"
        )
        return
    out = rest[0] if rest else "ui_map"
    found = await with_session(
        "NSW", lambda session: probe_journey(session.page, out)
    ) or {}
    print(f"\nmapped {len(found)} routes into {out}/")
    for route, dump in found.items():
        keys = sorted({c["name"] for c in dump["controls"] if c["name"]})
        print(f"  {route:24} {len(dump['controls']):2} controls  keys={keys}")


if __name__ == "__main__":
    asyncio.run(main())
