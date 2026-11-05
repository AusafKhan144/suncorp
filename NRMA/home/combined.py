# combined.py
# NRMA Home & Contents (combined) Quote Scraper — async via Zyte API
#
# Sibling of building.py (SubProduct=HOME) and contents.py (SubProduct=CONTENTS).
# This scraper drives the combined product (SubProduct=HOME_AND_CONTENTS), which
# returns a single bundled price covering both building and contents plus two
# default excesses (building + contents).
#
# Key combined-specific behaviour:
#   • Bootstrap product       → home-buildings-and-contents-standard
#   • SubProduct              → HOME_AND_CONTENTS
#   • IDs captured            → buildingPublicId + contentPublicId
#   • Sum insured fields      → SumInsuredHomeCustomAmount + SumInsuredContentCustomAmount
#   • Step 8 split            → 8a occupancy/rooms (no SI), then 8b sum-insured
#   • SI validation           → both home* and contents* ranges (building first)
#   • Excess sent on /price   → BuildingExcess only (default contents excess)
#   • Mode                    → main only (no excess toggle sweep)

import asyncio
import base64
import csv
import json
import logging
import os
import random
import sys
import uuid
from datetime import datetime
from typing import Optional
import httpx
from dataclasses import dataclass, field

# Transport: same as building.py/contents.py — one Akamai-validated camoufox
# session per quote via nrma_browser.quote_session. Ported 2026-10-06 off the
# Zyte API, which "cannot get a session validated" (see nrma_browser.py); the
# request-shape fixes, the 403 breaker and the shared cooldown come with it.
try:  # package import (python -m NRMA.home.combined)
    from .nrma_browser import (
        AkamaiNotValidated,
        BLOCK_STREAK_LIMIT,
        cooldown_hours_left,
        COOLDOWN_FILE,
        cover_start_date,
        is_bot_block,
        redact,
        quote_session,
        secret,
        start_cooldown,
        TERMINAL_REASON_PREFIXES,
    )
    from .si_steps import CONTENTS_FIELDS as SI_FIELDS, SumInsuredOutOfRange, quote as quote_si
except ImportError:  # direct script run
    from nrma_browser import (
        AkamaiNotValidated,
        BLOCK_STREAK_LIMIT,
        cooldown_hours_left,
        COOLDOWN_FILE,
        cover_start_date,
        is_bot_block,
        redact,
        quote_session,
        secret,
        start_cooldown,
        TERMINAL_REASON_PREFIXES,
    )
    from si_steps import CONTENTS_FIELDS as SI_FIELDS, SumInsuredOutOfRange, quote as quote_si

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("scraper.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

# Silence noisy third-party request logging (per-request HTTP lines from httpx)
for _noisy in ("httpx", "httpcore", "hpack", "urllib3"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
ZYTE_API_KEY = secret("ZYTE_API_KEY")  # secrets.conf — never inline, this repo is public
ZYTE_API_URL = "https://api.zyte.com/v1/extract"
BASE_URL = "https://insurance.nrma.com.au"
CONNECT_URL = "https://connect.nrma.com.au"  # auth token endpoint
# Paths come from the repo-root period module, like building.py/contents.py.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import period  # noqa: E402

# 1, not the old 10: on NRMA attempt volume is what destroys yield (2026-09-08).
CONCURRENCY = int(os.getenv("CONCURRENCY", "1"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "90"))
INPUT_MAIN_CSV = os.getenv("INPUT_MAIN_CSV") or period.input_csv("NRMA", "Combined") or ""
OUTPUT_MAIN_CSV = os.getenv("OUTPUT_MAIN_CSV") or period.output_csv("NRMA", "Combined") or ""
RETRY_ATTEMPTS = int(os.getenv("RETRY_ATTEMPTS", "3"))
RETRY_DELAY = float(os.getenv("RETRY_DELAY", "5.0"))

# Set FORCE_RERUN=1 to ignore any existing output and re-scrape every profile
# from scratch. Default (0) resumes: only profiles missing a complete row are run.
FORCE_RERUN = os.getenv("FORCE_RERUN", "0").strip().lower() in (
    "1",
    "true",
    "yes",
    "y",
)


# ---------------------------------------------------------------------------
# Field Mappings
# ---------------------------------------------------------------------------
WALL_MATERIAL_MAP = {
    "AAC panel": "AAC_PANEL",
    "Blockwork": "BLOCKWORK",
    "Brick veneer": "BRICK_VENEER",
    "Double brick": "DOUBLE_BRICK",
    "Panel wall system": "PANEL_WALL_SYSTEM",
    "Rendered masonry": "RENDERED_MASONRY",
    "Sheet cladding": "SHEET_CLADDING",
    "Stonework": "STONEWORK",
    "Stonework veneer": "STONEWORK_VENEER",
    "Stonework solid": "STONEWORK_VENEER",
    "Weatherboard - timber": "WEATHERBOARD_TIMBER",
    "Weatherboard - artificial": "WEATHERBOARD_ARTIFICIAL",
    "Other": "OTHER",
}

ROOF_MATERIAL_MAP = {
    "Concrete tile": "CONCRETE_TILE",
    "Fibre cement covering": "FIBRECEMENT_COVERING",
    "Metal covering": "METAL_COVERING",
    "Slate": "SLATE",
    "Terracotta": "TERRACOTTA",
    "Timber shingles": "TIMBER_SHINGLES",
    "Other": "OTHER",
}

SLOPE_MAP = {
    "Flat or gentle": "FLAT_GENTLE",
    "Moderate": "MODERATE",
    "Steep": "STEEP",
}

STOREY_MAP = {
    "1": "1",
    "2": "2",
    "3": "3",
    "4+": "4+",
}

FIXTURE_MAP = {
    "Standard": "STANDARD",
    "Above average": "ABOVE_AVERAGE",
    "High": "HIGH",
}

ALARM_MAP = {
    "No alarm": "NO_ALARM",
    "Local alarm": "LOCAL_ALARM",
    "Monitored by an app": "MONITORED_ALARM_MOBILE_PHONE_APP",
    "Monitored by a company": "MONITORED_ALARM_SECURITY_COMPANY",
}

LIVE_WITH_MAP = {
    "Myself or family": "MYSELF_OR_FAMILY",
    "Family": "MYSELF_OR_FAMILY",
    "Flatmates": "FLATMATES",
}

CLAIM_TYPE_MAP = {
    "Accidental breakage": "ACCIDENTAL_BREAKAGE",
    "Bushfire": "BUSHFIRE",
    "Cyclone": "CYCLONE",
    "Earthquake": "EARTHQUAKE",
    "Electrical motor burnout": "ELECTRICAL_MOTOR_BURNOUT",
    "Escape of water": "ESCAPE_OF_WATER",
    "Fire or explosion": "FIRE_OR_EXPLOSION",
    "Flood": "FLOOD",
    "Glass breakage": "GLASS_BREAKAGE",
    "Impact damage": "IMPACT_DAMAGE",
    "Intentional damage": "INTENTIONAL_DAMAGE",
    "Legal liability": "LEGAL_LIABILITY",
    "Lost items": "LOST_ITEM",
    "Storm or lightning": "STORM_OR_LIGHTNING",
    "Storm surge": "STORM_SURGE",
    "Stormwater run-off": "STORM_RUN_OFF",
    "Theft or burglary": "THEFT_OR_BURGLARY",
}

GENDER_MAP = {
    "Male": "MALE",
    "Female": "FEMALE",
    "Other": "OTHER",
}

BUILDING_TYPE = {
    "Freestanding house": "FREESTANDING_HOME",
    "Apartment or unit": "APARTMENT",
    "Townhouse, duplex or terrace": "TOWNHOUSE_TERRACE_HOUSE_OR_DUPLEX",
    "Relocatable or tiny home": "RELOCATABLE_OR_TINY_HOME",
    "Home on a farm": "HOME_ON_A_FARM",
    "Hobby farm": "HOME_HOBBY_FARM_OR_LIFESTYLE_BLOCK",
    "Unit in retirement village": "RETIREMENT_VILLAGE_UNIT",
}


# ---------------------------------------------------------------------------
# Data Classes
# ---------------------------------------------------------------------------
@dataclass
class Profile:
    sample_number: str
    substate: str = ""
    policy_effective_date: str = ""
    address: str = ""
    suburb: str = ""
    postcode: str = ""
    state: str = ""
    building_type: str = "FREESTANDING_HOME"
    # occupancy
    owns: bool = True
    holiday_home: bool = False
    live_in_property: bool = True
    flatmates: bool = False
    rent_out: bool = False
    live_with: str = "MYSELF_OR_FAMILY"
    good_condition: bool = True
    renovations: bool = False
    strata: bool = False
    business_use: bool = False
    slope: str = "FLAT_GENTLE"
    elevated: bool = False
    storeys: str = "1"
    construction_material: str = "BRICK_VENEER"
    roof_type: str = "CONCRETE_TILE"
    heritage_listed: bool = False
    building_year: str = "2000"
    electrical_wiring_replaced: bool = False
    alarm: str = "NO_ALARM"
    small_bedrooms: int = 1
    medium_bedrooms: int = 2
    large_bedrooms: int = 1
    bathrooms: int = 2
    living_rooms: int = 1
    dining_rooms: int = 1
    fixture_quality: str = "STANDARD"
    carport: int = 0
    garage: int = 1
    small_shed: int = 0
    large_shed: int = 0
    balconies: int = 0
    verandahs: int = 0
    ducted_air_conditioning: bool = False
    solar_panels: bool = False
    pool: bool = False
    tennis_court: bool = False
    jetty: bool = False
    adults: int = 1
    children: int = 0
    buildings_sum_insured: int = 500000
    contents_sum_insured: int = 150000
    owner_type: str = "Person"
    membership: bool = False
    owner_age: int = 40
    dob_dd: int = 1
    dob_mm: int = 1
    dob_yyyy: int = 1984
    first_name: str = "Anonymous"
    last_name: str = "Person"
    gender: str = "MALE"
    email: str = ""
    mortgage: bool = False
    claims_5yrs: bool = False
    claim_type: str = ""
    claim_month: int = 0
    claim_year: int = 0
    claim_for_address: bool = False
    claim_owner: str = ""
    fraud: bool = False
    insurance_refused: bool = False
    roof_replacement: bool = False
    roof_upgrade: bool = False
    window_protection: bool = False
    garage_door_upgrade: bool = False
    shed_tied_down: bool = False
    # derived occupancy enums (filled in _parse_row)
    occupancy_type: str = "OWN_AND_LIVE_IN_HOME"
    usage_arrangement: str = "OWN_AND_LIVE_IN_HOME"
    who_do_you_rent_with: str = ""


@dataclass
class QuoteResult:
    sample_number: str
    substate: str = ""
    dob_dd: str = ""
    dob_mm: str = ""
    dob_yyyy: str = ""
    postcode: str = ""
    quote_date: str = ""
    timestamp: str = ""
    quote_link: str = ""
    quote_number: str = ""
    # ── STANDARD ──
    nrma_default_excess: str = ""
    nrma_default_contents_excess: str = ""
    nrma_yearly: str = ""
    nrma_base_prem: str = ""
    nrma_gst: str = ""
    nrma_stamp_duty: str = ""
    nrma_esl: str = ""
    # ── PLUS ──
    nrma_plus_default_excess: str = ""
    nrma_plus_default_contents_excess: str = ""
    nrma_plus_yearly: str = ""
    # ── Error tracking ──
    failure_reason: str = ""
    # ── SI adjustment history (for rerun reporting) ──
    rerun: dict = field(default_factory=dict)  # lookup column -> value, see si_steps.py


# ---------------------------------------------------------------------------
# Occupancy derivation (mirrors the contents web flow)
# ---------------------------------------------------------------------------
def _derive_occupancy(
    owns: bool,
    holiday_home: bool,
    live_in: bool,
    rent_out: bool,
    flatmates: bool,
    live_with_enum: str,
) -> tuple[str, str, str]:
    """Return (PropertyOccupancyType, UsageArrangement, WhoDoYouRentWith)."""
    if owns and live_in and not holiday_home:
        return "OWN_AND_LIVE_IN_HOME", "OWN_AND_LIVE_IN_HOME", ""
    if owns and holiday_home:
        return "HOLIDAY_HOME", "HOLIDAY_HOME", ""
    if owns and rent_out:
        return "RENTED_TO_OTHERS", "OWN_AND_RENT_TO_OTHERS", ""
    who = "FLATMATES" if (flatmates or live_with_enum == "FLATMATES") else "MYSELF_OR_FAMILY"
    if who == "FLATMATES":
        usage = "RENT_WITH_FLATMATES_FROM_LANDLORD"
    else:
        usage = "RENT_WITH_FAMILY_FROM_LANDLORD"
    return "RENT_FROM_LANDLORD", usage, who


# ---------------------------------------------------------------------------
# CSV Reader
# ---------------------------------------------------------------------------
def _bool(val: str) -> bool:
    return str(val).strip().lower() in ("yes", "true", "1", "y")


def _int(val: str, default: int = 0) -> int:
    try:
        return int(float(str(val).strip()))
    except (ValueError, TypeError):
        return default


def load_profiles(csv_path: str) -> list[Profile]:
    profiles, errors = [], []
    with open(csv_path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            try:
                profiles.append(_parse_row(row))
            except Exception as exc:
                errors.append(f"  row {row.get('Sample_Number', '?')}: {exc}")
    if errors:
        # A malformed row used to be a warning and a dropped profile, so a bad
        # column silently shrank the delivery instead of stopping it. Report
        # every bad row at once so one pass fixes the input.
        shown = "\n".join(errors[:20])
        more = f"\n  ... and {len(errors) - 20} more" if len(errors) > 20 else ""
        raise ValueError(
            f"{len(errors)} unusable row(s) in {csv_path}:\n{shown}{more}"
        )
    log.info("Loaded %d profiles from %s", len(profiles), csv_path)
    return profiles


def _parse_row(row: dict) -> Profile:
    bt_raw = row.get("Building_Type", "").lower()
    building_type = BUILDING_TYPE.get(bt_raw, "FREESTANDING_HOME")

    owns = _bool(row.get("Ownership", "Yes"))
    holiday_home = _bool(row.get("Holiday_Home", ""))
    live_in = _bool(row.get("Live_In_Property", "Yes"))
    rent_out = _bool(row.get("Rent_Out", ""))
    flatmates = _bool(row.get("Flatmates", ""))
    live_with_enum = LIVE_WITH_MAP.get(
        row.get("Live_With", "Myself or family"), "MYSELF_OR_FAMILY"
    )

    occ_type, usage, who = _derive_occupancy(
        owns, holiday_home, live_in, rent_out, flatmates, live_with_enum
    )

    return Profile(
        sample_number=str(row.get("Sample_Number", "")).strip(),
        substate=str(row.get("State", "")).strip(),
        policy_effective_date=cover_start_date(row.get("Policy_Effective_Date", "")),
        address=str(row.get("Address", "")).strip().upper(),
        suburb=str(row.get("Suburb", "")).strip().upper(),
        postcode=str(row.get("Postcode", "")).strip().zfill(4),
        state=str(row.get("State", "")).strip().upper(),
        building_type=building_type,
        owns=owns,
        holiday_home=holiday_home,
        live_in_property=live_in,
        flatmates=flatmates,
        rent_out=rent_out,
        live_with=live_with_enum,
        good_condition=_bool(row.get("Good_Building_Condition", "Yes")),
        renovations=_bool(row.get("Renovations", "")),
        strata=_bool(row.get("Strata", "")),
        business_use=_bool(row.get("Business_Use", "")),
        slope=SLOPE_MAP.get(row.get("Slope", "Flat or gentle"), "FLAT_GENTLE"),
        elevated=_bool(row.get("Elevated", "")),
        storeys=STOREY_MAP.get(str(row.get("Storeys_If_House", "1")).strip(), "1"),
        construction_material=WALL_MATERIAL_MAP.get(
            row.get("Construction_Material", "Brick veneer"), "BRICK_VENEER"
        ),
        roof_type=ROOF_MATERIAL_MAP.get(
            row.get("Roof_Type", "Concrete tile"), "CONCRETE_TILE"
        ),
        heritage_listed=_bool(row.get("Heritage_Listed", "")),
        building_year=str(row.get("Building_Year", "2000")).strip(),
        electrical_wiring_replaced=_bool(row.get("Electrical_Wiring_Replaced", "")),
        alarm=ALARM_MAP.get(row.get("Alarm", "No alarm"), "NO_ALARM"),
        small_bedrooms=_int(row.get("Small_Bedrooms", "1"), 1),
        medium_bedrooms=_int(row.get("Medium_Bedrooms", "2"), 2),
        large_bedrooms=_int(row.get("Large_Bedrooms", "1"), 1),
        bathrooms=_int(row.get("Bathrooms", "2"), 2),
        living_rooms=_int(row.get("Living_Rooms", "1"), 1),
        dining_rooms=_int(row.get("Dining_Rooms", "1"), 1),
        fixture_quality=FIXTURE_MAP.get(
            row.get("Fixture_Quality", "Standard"), "STANDARD"
        ),
        carport=_int(row.get("Carport", "0"), 0),
        garage=_int(row.get("Garage", "1"), 1),
        small_shed=_int(row.get("Small_Shed", "0"), 0),
        large_shed=_int(row.get("Large_Shed", "0"), 0),
        balconies=_int(row.get("Balconies", "0"), 0),
        verandahs=_int(row.get("Verandahs", "0"), 0),
        ducted_air_conditioning=_bool(row.get("Ducted_Air_Conditioning", "")),
        solar_panels=_bool(row.get("Solar_Panels", "")),
        pool=_bool(row.get("Pool", "")),
        tennis_court=_bool(row.get("Tennis_Court", "")),
        jetty=_bool(row.get("Jetty", "")),
        adults=_int(row.get("Adults", "1"), 1),
        children=_int(row.get("Children", "0"), 0),
        buildings_sum_insured=_int(row.get("Buildings_Sum_Insured", "500000"), 500000),
        contents_sum_insured=_int(row.get("Contents_Sum_Insured", "150000"), 150000),
        owner_type=str(row.get("Owner_Type", "Person")).strip(),
        membership=_bool(row.get("Membership", "")),
        owner_age=_int(row.get("Owner_Age", "40"), 40),
        dob_dd=_int(row.get("Owner_DOB_DD_Dummy", "1"), 1),
        dob_mm=_int(row.get("Owner_DOB_MM_Dummy", "1"), 1),
        dob_yyyy=_int(row.get("Owner_DOB_YYYY_Dummy", "1984"), 1984),
        first_name=str(row.get("First_Name_Dummy", "Anonymous")).strip(),
        last_name=str(row.get("Last_Name_Dummy", "Person")).strip(),
        gender=GENDER_MAP.get(row.get("Gender", "Male"), "MALE"),
        email=str(row.get("Email_Dummy", "")).strip(),
        mortgage=_bool(row.get("Mortgage", "")),
        claims_5yrs=_bool(row.get("Claims_5yrs", "")),
        claim_type=CLAIM_TYPE_MAP.get(row.get("Type_Of_Claim_Home", ""), ""),
        claim_month=_int(row.get("Claim_Month", "0"), 0),
        claim_year=_int(row.get("Claim_Year", "0"), 0),
        claim_for_address=_bool(row.get("Claim_For_Address", "")),
        claim_owner=str(row.get("Claim_Owner", "")).strip(),
        fraud=_bool(row.get("Fraud", "")),
        insurance_refused=_bool(row.get("Insurance_Refused", "")),
        roof_replacement=_bool(row.get("Roof_Replacement", "")),
        roof_upgrade=_bool(row.get("Roof_Upgrade", "")),
        window_protection=_bool(row.get("Window_Protection", "")),
        garage_door_upgrade=_bool(row.get("Garage_Door_Upgrade", "")),
        shed_tied_down=_bool(row.get("Shed_Tied_down", "")),
        occupancy_type=occ_type,
        usage_arrangement=usage,
        who_do_you_rent_with=who,
    )


# ---------------------------------------------------------------------------
# Zyte API wrapper
# ── KEY FIX: cookies sent via experimental.requestCookies, NOT headers ──
# ---------------------------------------------------------------------------
class ZyteSession:
    def __init__(self, client: httpx.AsyncClient):
        self.client = client
        self.auth = (ZYTE_API_KEY, "")

    async def request(
        self,
        url: str,
        method: str = "GET",
        headers: dict | None = None,
        body: str | None = None,
        cookies: dict | None = None,
    ) -> tuple[int, list[tuple[str, str]], str, list[dict]]:
        """
        Execute a request via Zyte API.

        Cookies go via experimental.requestCookies (Zyte ignores cookie headers);
        response cookies come back in experimental.responseCookies.

        Returns: (status_code, response_headers_as_list_of_tuples, body_text,
                  experimental_response_cookies)
        """
        payload: dict = {
            "url": url,
            "httpResponseBody": True,
            "httpResponseHeaders": True,
            "httpRequestMethod": method,
            "experimental": {
                "responseCookies": True,
            },
        }

        if cookies:
            payload["experimental"]["requestCookies"] = [
                {"name": k, "value": v, "domain": _cookie_domain(url)}
                for k, v in cookies.items()
                if v
            ]

        if headers:
            payload["customHttpRequestHeaders"] = [
                {"name": k, "value": v}
                for k, v in headers.items()
                if k.lower() not in ("cookie", "content-length")
            ]

        if body:
            payload["httpRequestBody"] = base64.b64encode(body.encode()).decode()

        for attempt in range(1, RETRY_ATTEMPTS + 1):
            try:
                resp = await self.client.post(
                    ZYTE_API_URL,
                    json=payload,
                    auth=self.auth,
                    timeout=REQUEST_TIMEOUT,
                )
                resp.raise_for_status()
                data = resp.json()

                raw_body = base64.b64decode(data.get("httpResponseBody", "")).decode(
                    "utf-8", errors="replace"
                )

                status = data.get("statusCode", 200)

                resp_headers: list[tuple[str, str]] = [
                    (h["name"].lower(), h["value"])
                    for h in data.get("httpResponseHeaders", [])
                ]

                experimental_cookies: list[dict] = data.get("experimental", {}).get(
                    "responseCookies", []
                )

                return status, resp_headers, raw_body, experimental_cookies

            except Exception as exc:
                if attempt == RETRY_ATTEMPTS:
                    raise
                log.warning(
                    "Zyte attempt %d/%d failed: %s — retrying in %.0fs",
                    attempt,
                    RETRY_ATTEMPTS,
                    exc,
                    RETRY_DELAY * attempt,
                )
                await asyncio.sleep(RETRY_DELAY * attempt)

        raise RuntimeError("All Zyte retries exhausted")


def _cookie_domain(url: str) -> str:
    """Extract domain from URL for Zyte requestCookies domain field."""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    return parsed.netloc


# ---------------------------------------------------------------------------
# NRMA API Client
# ---------------------------------------------------------------------------
class NRMAClient:

    def __init__(self, zyte: ZyteSession):
        self.zyte = zyte
        self.session_id = str(uuid.uuid4())
        self.quote_id = None
        self.quote_signature = None
        self.signature_expiry = None
        self.customer_token = None  # x-iag-customer (Okta JWT)
        self.auth_session_token = None  # x-iag-auth-session JWT
        self.cookies: dict = {}  # full cookie jar
        self.si_overrides: Optional[dict] = None  # answers a sum-insured re-quote changes

    # ── Cookie management ─────────────────────────────────────────────

    def _update_cookies_from_headers(self, resp_headers: list[tuple[str, str]]):
        """Parse set-cookie from response header list of tuples."""
        for name, value in resp_headers:
            if name != "set-cookie":
                continue
            cookie_part = value.split(";")[0].strip()
            if "=" not in cookie_part:
                continue
            k, v = cookie_part.split("=", 1)
            k, v = k.strip(), v.strip()
            if not k:
                continue
            self.cookies[k] = v
            log.debug("Cookie set via header: %s", k)
            self._capture_auth_tokens(k, v)

    def _update_cookies_from_experimental(self, experimental_cookies: list[dict]):
        """Parse cookies from Zyte experimental.responseCookies block (primary source)."""
        for cookie in experimental_cookies:
            k = cookie.get("name", "").strip()
            v = cookie.get("value", "").strip()
            if not k:
                continue
            self.cookies[k] = v
            log.debug("Cookie set via experimental: %s", k)
            self._capture_auth_tokens(k, v)

    def _capture_auth_tokens(self, k: str, v: str):
        """Capture auth JWTs whenever they appear in cookies."""
        if k == "x-iag-auth-session":
            self.auth_session_token = v
            log.debug("Auth session token updated (len=%d)", len(v))
        if k == "x-iag-customer":
            self.customer_token = v
            log.debug("Customer token updated (len=%d)", len(v))

    def _update_all_cookies(
        self, resp_headers: list[tuple[str, str]], experimental_cookies: list[dict]
    ):
        """Update cookies from both sources after every request."""
        self._update_cookies_from_headers(resp_headers)
        # Experimental takes priority — run second to overwrite if conflict
        self._update_cookies_from_experimental(experimental_cookies)

    # ── Header builders ───────────────────────────────────────────────

    def _base_headers(self, referer: str = "") -> dict:
        """Build request headers — NO cookie header here (sent separately)."""
        h = {
            "accept": "application/json, text/plain, */*",
            "accept-language": "en-US,en;q=0.9",
            "cache-control": "no-cache",
            "pragma": "no-cache",
            "sec-ch-ua": '"Not)A;Brand";v="8", "Chromium";v="138"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Linux"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "user-agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
            ),
            "x-iag-consumer-system": "RETAIL_WEB",
            "x-iag-session-id": self.session_id,
        }
        if referer:
            h["referer"] = referer
        if self.auth_session_token:
            h["x-iag-auth-session"] = self.auth_session_token
        if self.customer_token:
            h["x-iag-customer"] = self.customer_token
        return h

    def _json_headers(self, referer: str = "") -> dict:
        h = self._base_headers(referer)
        h["content-type"] = "application/json"
        h["origin"] = BASE_URL
        return h

    # ── Low-level request wrappers ────────────────────────────────────

    async def _get(self, url: str, referer: str = "") -> dict:
        status, hdrs, body, exp_cookies = await self.zyte.request(
            url,
            method="GET",
            headers=self._base_headers(referer),
            cookies=self.cookies,
        )
        self._update_all_cookies(hdrs, exp_cookies)
        if status >= 400:
            raise RuntimeError(f"GET {url} → {status}: {body[:300]}")
        try:
            json.loads(body)
        except Exception:
            return {}
        return json.loads(body) if body.strip() else {}

    async def _post(
        self,
        url: str,
        payload: dict,
        referer: str = "",
        override_cookies: dict | None = None,
        surpass=False,
    ) -> dict:
        status, hdrs, body, exp_cookies = await self.zyte.request(
            url,
            method="POST",
            headers=self._json_headers(referer),
            body=json.dumps(payload),
            cookies=override_cookies or self.cookies,
        )
        self._update_all_cookies(hdrs, exp_cookies)
        if status >= 400 and not surpass:
            raise RuntimeError(f"POST {url} → {status}: {body[:300]}")
        return json.loads(body) if body.strip() else {}

    async def _put(self, url: str, payload: dict, referer: str = "") -> dict:
        status, hdrs, body, exp_cookies = await self.zyte.request(
            url,
            method="PUT",
            headers=self._json_headers(referer),
            body=json.dumps(payload),
            cookies=self.cookies,
        )
        self._update_all_cookies(hdrs, exp_cookies)
        if status >= 400:
            raise RuntimeError(f"PUT {url} → {status}: {body[:300]}")
        return json.loads(body) if body.strip() else {}

    # ================================================================
    # Step 1 — Bootstrap: land on site, get Akamai cookies
    # ================================================================
    async def step1_bootstrap(self, state: str = "NSW"):
        url = (
            f"{BASE_URL}/get-cover/home?"
            f"serenityproduct=home-buildings-and-contents-standard"
            f"&serenitydefaultstate={state}"
            f"&serenityselectedstate={state}"
        )
        await self._get(url)
        log.debug(
            "[%s] Step1 bootstrap done, cookies=%d",
            self.session_id[:8],
            len(self.cookies),
        )

    # ================================================================
    # Step 2 — Validate entitlements
    # ================================================================
    async def step2_validate_entitlements(self):
        """The app's own first API call. Restored 2026-09-09 in contents.py.

        Not needed for the data, but the `.har` capture of a real journey calls
        it, and skipping it made our sequence open with quote creation — one of
        the request-shape contradictions behind the 403 wall. A failure here
        says nothing about the property, so it must not lose the row.
        """
        try:
            await self._get(
                f"{BASE_URL}/domain/identity/api/identity-mfe/validateEntitlements",
                referer=f"{BASE_URL}/get-cover/home",
            )
        except Exception as exc:  # noqa: BLE001 — sequence padding, not data
            log.debug("[%s] Step2 entitlements: %s", self.session_id[:8], exc)
        log.debug("[%s] Step2 done", self.session_id[:8])

    # ================================================================
    # Step 3 — Create quote → quoteId + signature
    # ================================================================
    async def step3_create_quote(self) -> str:
        resp = await self._post(
            f"{BASE_URL}/domain/question/api/question/api/2.x.x/home/quote",
            payload={},
            referer=f"{BASE_URL}/get-cover/home",
        )
        self.quote_id = resp["quoteId"]
        self.quote_signature = resp["quoteIdSignature"]
        self.signature_expiry = resp["quoteIdSignatureExpiry"]
        log.debug("[%s] Step3 quoteId=%s", self.session_id[:8], self.quote_id)
        return self.quote_id

    # ================================================================
    # Step 4 — Exchange signature → x-iag-customer JWT
    # ================================================================
    async def step4_exchange_token(self):
        payload = {
            "payload": {
                "quoteId": self.quote_id,
                "expiry": self.signature_expiry,
            },
            "signature": self.quote_signature,
        }

        headers = {
            "accept": "application/json, text/plain, */*",
            "accept-language": "en-US,en;q=0.9",
            "content-type": "application/json",
            "origin": BASE_URL,
            "referer": f"{BASE_URL}/get-cover/home",
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-site",
            "user-agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
            ),
        }

        connect_cookies = {
            k: v for k, v in self.cookies.items() if k in ("ak_bmsc", "bm_sz", "_abck")
        }

        status, hdrs, body, exp_cookies = await self.zyte.request(
            f"{CONNECT_URL}/cb/anonymous/quote/nrma",
            method="POST",
            headers=headers,
            body=json.dumps(payload),
            cookies=connect_cookies,
        )
        self._update_all_cookies(hdrs, exp_cookies)

        if status >= 400:
            raise RuntimeError(f"Step4 token exchange → {status}: {body[:300]}")

        resp_json = json.loads(body) if body.strip() else {}
        access_token = resp_json.get("accessToken", "")
        if not access_token:
            raise RuntimeError(f"Step4: no accessToken in response: {body[:300]}")

        self.customer_token = access_token
        self.cookies["x-iag-customer"] = access_token
        log.debug(
            "[%s] Step4 token acquired (len=%d)", self.session_id[:8], len(access_token)
        )

    # ================================================================
    # Step 5 — Address lookup
    # ================================================================
    async def step5_lookup_address(
        self, address: str, suburb: str, postcode: str, state: str
    ) -> list[dict]:
        full_addr = f"{address}, {suburb} {state} {postcode}"
        resp = await self._post(
            f"{BASE_URL}/domain/question/api/question/api/1.x.x"
            f"/reference/address/lookup/",
            payload={
                "excludePostalAddresses": True,
                "address": full_addr,
                "brand": "nrma",
            },
            referer=f"{BASE_URL}/get-cover/home/3/risk-address",
        )
        if isinstance(resp, list):
            return resp
        return resp.get("addresses", resp.get("results", []))

    # ================================================================
    # Step 6 — Property prefill
    # ================================================================
    async def step6_get_property(self, address_obj: dict) -> dict:
        return await self._post(
            f"{BASE_URL}/domain/question/api/question/api/1.x.x"
            f"/reference/property/{self.quote_id}",
            payload={
                "address": {
                    "descriptive_address_line1": address_obj.get(
                        "descriptive_address_line1",
                        address_obj.get("display_address_line1", ""),
                    ),
                    "state": address_obj.get("state", ""),
                    "locality_name": address_obj.get("locality_name", ""),
                    "postcode": address_obj.get("postcode", ""),
                }
            },
            referer=f"{BASE_URL}/get-cover/home/3/risk-address",
            surpass=True,
        )

    # ================================================================
    # Step 7 — Minimal details (seed the quote)
    # ================================================================
    async def step7_fill_minimal_details(
        self, address_obj: dict, profile: Profile
    ) -> dict:
        cover_start = profile.policy_effective_date  # validated at load time
        json_data = {
            "quoteType": "QUICK",
            "answers": {
                "BillingFrequency": "MONTHLY",
                "PayDirectDebit": ["true"],
                "LevelOfCover": "STANDARD",
                "HowToSendPolicyDocuments": "email_address",
                "BuildingOptionalExtrasAccidentalDamage": ["false"],
                "BuildingOptionalExtrasElectricalBurnOut": ["false"],
                "ContentsOptionalExtrasAccidentalDamage": ["false"],
                "ContentsOptionalExtrasElectricalBurnOut": ["false"],
                "TermsConditionAgreement": True,
                "SubProduct": "HOME_AND_CONTENTS",
                "CoverStartDate": cover_start,
                "PropertyAddressLookup": address_obj,
                "PropertyManualAddress": None,
            },
        }
        return await self._post(
            f"{BASE_URL}/domain/question/api/question/api/2.x.x/home/quote/{self.quote_id}",
            payload=json_data,
            referer=f"{BASE_URL}/get-cover/home/3/risk-address",
        )

    # ================================================================
    # Shared: build full answers dict (steps 8b, 9, 11, price)
    # ================================================================
    def _build_answers(
        self,
        profile: Profile,
        address_obj: dict,
        asset_id: str,
        building_id: str,
        content_id: str,
        billing_freq: str = "MONTHLY",
        level_of_cover: str = "STANDARD",
        excess: Optional[int] = None,
        accidental_damage: bool = False,
        fusion: bool = False,
        party_id: str = "",
        include_parties: bool = True,
    ) -> dict:

        cover_start = profile.policy_effective_date  # validated at load time

        dob_str = (
            f"{profile.dob_yyyy}-"
            f"{str(profile.dob_mm).zfill(2)}-"
            f"{str(profile.dob_dd).zfill(2)}"
        )

        prior_claims = []
        if profile.claims_5yrs and profile.claim_type:
            prior_claims.append(
                {
                    "Event": profile.claim_type,
                    "InvolveThisProperty": True,
                    "OwnerPartyId": party_id,
                    "YearOccurred": profile.claim_year,
                }
            )

        answers: dict = {
            "BillingFrequency": billing_freq,
            "PayDirectDebit": ["true"],
            "LevelOfCover": level_of_cover,
            "HowToSendPolicyDocuments": "email_address",
            "BuildingOptionalExtrasAccidentalDamage": [str(accidental_damage).lower()],
            "BuildingOptionalExtrasElectricalBurnOut": [str(fusion).lower()],
            "ContentsOptionalExtrasAccidentalDamage": [str(accidental_damage).lower()],
            "ContentsOptionalExtrasElectricalBurnOut": [str(fusion).lower()],
            "TermsConditionAgreement": True,
            "SubProduct": "HOME_AND_CONTENTS",
            "CoverStartDate": cover_start,
            "PropertyAddressLookup": address_obj,
            "PropertyManualAddress": None,
            "SmallBedrooms": profile.small_bedrooms,
            "MediumBedrooms": profile.medium_bedrooms,
            "LargeBedrooms": profile.large_bedrooms,
            "Bathrooms": profile.bathrooms,
            "ParkingSpacesCarport": profile.carport,
            "ParkingSpacesGarage": profile.garage,
            "PropertyType": profile.building_type,
            "YearConstructed": profile.building_year,
            "RoofMaterial": profile.roof_type,
            "WallMaterial": profile.construction_material,
            "LandSlope": profile.slope,
            "Storeys": profile.storeys,
            "BuiltInHeatingOrCooling": profile.ducted_air_conditioning,
            "InGroundPool": profile.pool,
            "TennisCourt": profile.tennis_court,
            "SolarPanels": profile.solar_panels,
            "QualityOfFixtures": profile.fixture_quality,
            "Verandahs": profile.verandahs,
            "BalconiesAndDecks": profile.balconies,
            "SmallSheds": profile.small_shed,
            "LargeSheds": profile.large_shed,
            "FloodingRisk": False,
            "JettyWharfLandingPier": profile.jetty,
            "BodyCorporateOrStrata": profile.strata,
            "SumInsuredHomeCustomAmount": str(profile.buildings_sum_insured),
            "SumInsuredContentCustomAmount": str(profile.contents_sum_insured),
            "LivingSpaces": profile.living_rooms,
            "DiningRooms": profile.dining_rooms,
            "ElectricalWiringReplaced": profile.electrical_wiring_replaced,
            "Alarm": profile.alarm,
            "PropertyOccupancyType": profile.occupancy_type,
            "AdultOccupants": profile.adults,
            "ChildOccupants": profile.children,
            "UsageArrangement": profile.usage_arrangement,
            "AssetPublicId": asset_id,
            "BuildingPublicId": building_id,
        }

        if profile.who_do_you_rent_with:
            answers["WhoDoYouRentWith"] = profile.who_do_you_rent_with

        answers.update(self.si_overrides or {})

        if include_parties:
            party: dict = {
                "IsCustomer": False,
                "PartyGender": profile.gender,
                "PartyDOB": dob_str,
                "PartyType": "Individual",
                "BackendPartyRole": "PRIMARY_POLICY_HOLDER",
            }
            if party_id:
                party["PartyId"] = party_id
                party["HowManyPriorClaims"] = str(len(prior_claims))
            answers["Parties"] = [party]

        if party_id:
            answers["PriorClaims"] = prior_claims
            answers["ClaimsMade"] = profile.claims_5yrs

        # Combined product: only BuildingExcess is exercised on /price; the
        # contents excess tracks the API default.
        if excess is not None:
            answers["BuildingExcess"] = str(excess)

        return answers

    # ================================================================
    # Shared: occupancy/house-details answers (steps 8a + 8b)
    #
    # This is the property/occupancy payload WITHOUT owner-party or claims
    # fields — those only belong on the owner step (9) onward. Step 8a omits
    # the sum-insured amounts; step 8b adds them (include_si=True).
    # ================================================================
    def _occupancy_answers(
        self,
        profile: Profile,
        address_obj: dict,
        asset_id: str,
        building_id: str,
        content_id: str,
        include_si: bool = False,
    ) -> dict:
        cover_start = profile.policy_effective_date  # validated at load time

        answers = {
            "BillingFrequency": "MONTHLY",
            "PayDirectDebit": ["true"],
            "LevelOfCover": "STANDARD",
            "HowToSendPolicyDocuments": "email_address",
            "BuildingOptionalExtrasAccidentalDamage": ["false"],
            "BuildingOptionalExtrasElectricalBurnOut": ["false"],
            "ContentsOptionalExtrasAccidentalDamage": ["false"],
            "ContentsOptionalExtrasElectricalBurnOut": ["false"],
            "TermsConditionAgreement": True,
            "SubProduct": "HOME_AND_CONTENTS",
            "CoverStartDate": cover_start,
            "PropertyAddressLookup": address_obj,
            "PropertyManualAddress": None,
            "SmallBedrooms": profile.small_bedrooms,
            "MediumBedrooms": profile.medium_bedrooms,
            "LargeBedrooms": profile.large_bedrooms,
            "Bathrooms": profile.bathrooms,
            "ParkingSpacesCarport": profile.carport,
            "ParkingSpacesGarage": profile.garage,
            "PropertyType": profile.building_type,
            "YearConstructed": profile.building_year,
            "RoofMaterial": profile.roof_type,
            "WallMaterial": profile.construction_material,
            "LandSlope": profile.slope,
            "Storeys": profile.storeys,
            "BuiltInHeatingOrCooling": profile.ducted_air_conditioning,
            "InGroundPool": profile.pool,
            "TennisCourt": profile.tennis_court,
            "SolarPanels": profile.solar_panels,
            "QualityOfFixtures": profile.fixture_quality,
            "Verandahs": profile.verandahs,
            "BalconiesAndDecks": profile.balconies,
            "SmallSheds": profile.small_shed,
            "LargeSheds": profile.large_shed,
            "FloodingRisk": False,
            "JettyWharfLandingPier": profile.jetty,
            "BodyCorporateOrStrata": profile.strata,
            "AssetPublicId": asset_id,
            "BuildingPublicId": building_id,
            "ContentPublicId": content_id,
            "LivingSpaces": profile.living_rooms,
            "DiningRooms": profile.dining_rooms,
            "ElectricalWiringReplaced": profile.electrical_wiring_replaced,
            "Alarm": profile.alarm,
            "PropertyOccupancyType": profile.occupancy_type,
            "AdultOccupants": profile.adults,
            "ChildOccupants": profile.children,
            "UsageArrangement": profile.usage_arrangement,
        }
        if include_si:
            answers["SumInsuredHomeCustomAmount"] = str(profile.buildings_sum_insured)
            answers["SumInsuredContentCustomAmount"] = str(profile.contents_sum_insured)
        if profile.who_do_you_rent_with:
            answers["WhoDoYouRentWith"] = profile.who_do_you_rent_with

        answers.update(self.si_overrides or {})
        return answers

    # ================================================================
    # Step 8a — Occupancy / rooms (NO sum-insured amounts yet)
    # ================================================================
    async def step_8_fill_house_details(
        self,
        profile: Profile,
        address_obj: dict,
        asset_id: str,
        building_id: str,
        content_id: str,
    ) -> dict:
        answers = self._occupancy_answers(
            profile, address_obj, asset_id, building_id, content_id, include_si=False
        )
        return await self._post(
            f"{BASE_URL}/domain/question/api/question/api/2.x.x"
            f"/home/quote/{self.quote_id}",
            payload={"quoteType": "QUICK", "answers": answers},
            referer=f"{BASE_URL}/get-cover/home/3/occupancy",
        )

    # ================================================================
    # Step 8b — Sum-insured confirmation (both amounts)
    # ================================================================
    async def step_8b_set_sum_insured(
        self,
        profile: Profile,
        address_obj: dict,
        asset_id: str,
        building_id: str,
        content_id: str,
    ) -> dict:
        answers = self._occupancy_answers(
            profile, address_obj, asset_id, building_id, content_id, include_si=True
        )
        return await self._post(
            f"{BASE_URL}/domain/question/api/question/api/2.x.x"
            f"/home/quote/{self.quote_id}",
            payload={"quoteType": "QUICK", "answers": answers},
            referer=f"{BASE_URL}/get-cover/home/3/sum-insured",
        )

    # ================================================================
    # Step 9 — Owner details
    # ================================================================
    async def step9_set_owner(
        self,
        profile: Profile,
        address_obj: dict,
        asset_id: str,
        building_id: str,
        content_id: str,
        party_id: str,
    ) -> dict:
        return await self._post(
            f"{BASE_URL}/domain/question/api/question/api/2.x.x"
            f"/home/quote/{self.quote_id}",
            payload={
                "quoteType": "QUICK",
                "answers": self._build_answers(
                    profile,
                    address_obj,
                    asset_id,
                    building_id,
                    content_id,
                    party_id=party_id,
                ),
            },
            referer=f"{BASE_URL}/get-cover/home/3/owner-details",
        )

    # ================================================================
    # Step 10 — Refresh anon quote token
    # ================================================================
    async def step10_refresh_token(self):
        status, hdrs, body, exp_cookies = await self.zyte.request(
            f"{BASE_URL}/domain/question/api/question/api/1.x.x"
            f"/token/refreshAnonQuoteToken",
            method="POST",
            headers=self._base_headers(referer=f"{BASE_URL}/get-cover/home/3/prefill"),
            cookies=self.cookies,
        )
        self._update_all_cookies(hdrs, exp_cookies)
        log.debug("[%s] Step10 token refreshed", self.session_id[:8])

    # ================================================================
    # Step 11 — POST /offer
    # ================================================================
    async def step11_submit_offer(
        self,
        profile: Profile,
        address_obj: dict,
        asset_id: str,
        building_id: str,
        content_id: str,
        party_id: str,
        accidental_damage: bool = False,
        fusion: bool = False,
        excess: Optional[int] = None,
        referer_page: str = "claims-history",
    ) -> dict:

        answers = self._build_answers(
            profile,
            address_obj,
            asset_id,
            building_id,
            content_id,
            party_id=party_id,
            accidental_damage=accidental_damage,
            fusion=fusion,
            excess=excess,
        )
        answers["EmailAddressConsent"] = ""

        return await self._post(
            f"{BASE_URL}/domain/question/api/question/api/2.x.x"
            f"/home/quote/{self.quote_id}/offer",
            payload={"answers": answers, "quoteType": "QUICK"},
            referer=f"{BASE_URL}/get-cover/home/3/{referer_page}",
        )

    # ================================================================
    # Step 12+ — PUT /price
    # ================================================================
    async def get_price(
        self,
        profile: Profile,
        address_obj: dict,
        asset_id: str,
        building_id: str,
        content_id: str,
        party_id: str,
        level_of_cover: str = "STANDARD",
        billing_freq: str = "ANNUALLY",
        excess: Optional[int] = None,
    ) -> dict:

        answers = self._build_answers(
            profile,
            address_obj,
            asset_id,
            building_id,
            content_id,
            billing_freq=billing_freq,
            level_of_cover=level_of_cover,
            party_id=party_id,
            excess=excess,
        )
        return await self._put(
            f"{BASE_URL}/domain/question/api/question/api/2.x.x"
            f"/home/quote/{self.quote_id}/price",
            payload={"quoteType": "QUICK", "answers": answers},
            referer=f"{BASE_URL}/get-cover/home/3/quote-consent",
        )


# ---------------------------------------------------------------------------
# Price / excess extraction
# ---------------------------------------------------------------------------
_FREQ_MAP = {
    "ANNUALLY": "ANNUAL",
    "ANNUAL": "ANNUAL",
    "FORTNIGHTLY": "FORTNIGHTLY",
    "QUARTERLY": "QUARTERLY",
    "MONTHLY": "MONTHLY",
}


def _extract_price(resp: dict, level: str = "STANDARD", freq: str = "ANNUALLY") -> str:
    api_freq = _FREQ_MAP.get(freq.upper(), freq.upper())
    try:
        val = resp.get("quoteData", {}).get("billingFrequencyPrices", {}).get(api_freq)
        if val is not None:
            return str(round(float(val), 2))
    except Exception:
        pass
    return ""


def _extract_aggregate_premium(resp: dict) -> Optional[dict]:
    """Base premium / GST / stamp duty / ESL breakdown from a price response."""
    plans = resp.get("quoteData", {}).get("payments", {}).get("billing_plan_options", [])
    for plan in plans:
        agg = plan.get("aggregate_premium_details")
        if agg:
            return agg
    return None


def _extract_default_excess(resp: dict, level: str = "STANDARD") -> str:
    """Building default excess from the offer response."""
    qd = resp.get("quoteData", {})
    bced = qd.get("buildingAndContentsExcessDetails", {}).get("building", {})
    if "standard_excess" in bced:
        return str(bced["standard_excess"])
    exc = qd.get("excesses", {})
    if "building" in exc:
        return str(exc["building"])
    return ""


def _extract_default_contents_excess(resp: dict, level: str = "STANDARD") -> str:
    """Contents default excess from the offer response."""
    qd = resp.get("quoteData", {})
    bced = qd.get("buildingAndContentsExcessDetails", {}).get("contents", {})
    if "standard_excess" in bced:
        return str(bced["standard_excess"])
    exc = qd.get("excesses", {})
    if "contents" in exc:
        return str(exc["contents"])
    return ""


def _extract_party_id(resp: dict) -> str:
    try:
        return resp.get("quoteData", {}).get("parties", [])[0].get("party_id", "")
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Output sanity checks
# ---------------------------------------------------------------------------
_VALID_EXCESS_TIERS = set(range(0, 5001, 50)) | {10000}


def _check_output_integrity(result: "QuoteResult", profile: "Profile") -> None:
    """General row-level sanity checks, run for every completed quote.

    Catches a row silently ending up half-success/half-failure, a Plus quote
    coming back cheaper than Standard, or a DefaultExcess value that isn't
    one of the tiers we ever request (all signs of a mismatched/misparsed
    API response). Mirrors the equivalent checks in building.py/contents.py.
    """
    yearly = (getattr(result, "nrma_yearly", "") or "").strip()
    plus_yearly = (getattr(result, "nrma_plus_yearly", "") or "").strip()
    failure_reason = (getattr(result, "failure_reason", "") or "").strip()

    if yearly and failure_reason:
        log.warning(
            "profile=%s INTEGRITY CHECK FAILED: row has both NRMA_YearlyPayment=%s "
            "and Reason_for_quote_failure=%r — should be mutually exclusive",
            profile.sample_number, yearly, failure_reason,
        )
    elif not yearly and not failure_reason:
        log.warning(
            "profile=%s INTEGRITY CHECK FAILED: row has neither NRMA_YearlyPayment "
            "nor Reason_for_quote_failure — quote outcome is unclear",
            profile.sample_number,
        )

    if yearly and plus_yearly:
        try:
            if float(plus_yearly) < float(yearly) - 0.01:
                log.warning(
                    "profile=%s INTEGRITY CHECK FAILED: NRMAPlus_YearlyPayment=%s is "
                    "cheaper than NRMA_YearlyPayment=%s — Plus should never undercut Standard",
                    profile.sample_number, plus_yearly, yearly,
                )
        except ValueError:
            pass

    for label, attr in [
        ("NRMA_DefaultExcess_Building", "nrma_default_excess"),
        ("NRMA_DefaultExcess_Content", "nrma_default_contents_excess"),
        ("NRMAPlus_DefaultExcess_Building", "nrma_plus_default_excess"),
        ("NRMAPlus_DefaultExcess_Content", "nrma_plus_default_contents_excess"),
    ]:
        val = (getattr(result, attr, "") or "").strip()
        if not val:
            continue
        try:
            tier = int(float(val))
        except ValueError:
            log.warning(
                "profile=%s INTEGRITY CHECK FAILED: %s=%r is not numeric",
                profile.sample_number, label, val,
            )
            continue
        if tier not in _VALID_EXCESS_TIERS:
            log.warning(
                "profile=%s INTEGRITY CHECK FAILED: %s=%d is not one of the "
                "standard excess tiers we request — possible parsing error",
                profile.sample_number, label, tier,
            )


def _validate_both_sum_insured(profile: Profile, resp: dict):
    """Check both building and contents SI against the API's calculated ranges.

    Building is checked first; if it raises, contents is not checked this pass.
    The retry loop adjusts rooms (building) or rooms+occupants (contents) and
    re-runs, so the contents check naturally re-runs on the next attempt.
    """
    calc = resp.get("quoteData", {}).get("calculatedSumInsured", {})

    # ── Building range ──
    bld_max = calc.get("homeSumInsuredMax") or 0
    bld_min = calc.get("homeSumInsuredMin") or 0
    building_sum = profile.buildings_sum_insured
    log.info(
        f"│  Building: ${building_sum:,} (range: ${bld_min:,} — ${bld_max:,})"
    )
    if bld_min and building_sum < bld_min:
        raise SumInsuredOutOfRange("building", building_sum, bld_min, bld_max, "low")
    if bld_max and building_sum > bld_max:
        raise SumInsuredOutOfRange("building", building_sum, bld_min, bld_max, "high")

    # ── Contents range ──
    cnt_max = calc.get("contentsSumInsuredMax") or 0
    cnt_min = calc.get("contentsSumInsuredMin") or 0
    contents_sum = profile.contents_sum_insured
    log.info(
        f"│  Contents: ${contents_sum:,} (range: ${cnt_min:,} — ${cnt_max:,})"
    )
    if cnt_min and contents_sum < cnt_min:
        raise SumInsuredOutOfRange("contents", contents_sum, cnt_min, cnt_max, "low")
    if cnt_max and contents_sum > cnt_max:
        raise SumInsuredOutOfRange("contents", contents_sum, cnt_min, cnt_max, "high")


# ---------------------------------------------------------------------------
# Address matching — score lookup candidates instead of blindly taking [0]
# ---------------------------------------------------------------------------
# GNAF lists a block's car spaces and storage cages as sub-addresses under the
# same street number, and the lookup returns them alongside the real dwellings.
# They are not homes: quoting one comes back with a sum-insured range of $0—$0,
# no excesses and a 500 from /price, which is how 10 RAWSON ST ROCKDALE ended up
# quoted as "CARS 10" and left uncollectable.
NON_DWELLING_UNIT_TYPES = {"CARS", "CARP", "CARL", "GRGE", "LOCK", "STOR"}


def _find_matching_address(results, address, suburb, state, postcode):
    """Pick the candidate that best matches the requested address."""
    if not results:
        return None

    def norm(v):
        return str(v).strip().upper() if v else ""

    # Rank on the match score first, then prefer a verified GNAF record over an
    # unverified one — every sub-address of a block scores the same on
    # postcode/state/suburb, so without a tiebreak the arbitrary first entry won.
    best = None
    for entry in results:
        street = entry.get("street_address") or {}
        if norm(street.get("flat_unit_type")) in NON_DWELLING_UNIT_TYPES:
            continue
        sc = 0
        if norm(entry.get("postcode", "")) == norm(postcode):
            sc += 3
        if norm(entry.get("state", "")) == norm(state):
            sc += 2
        if norm(entry.get("locality_name", "")) == norm(suburb):
            sc += 2
        if norm(entry.get("display_address_line1", "")) == norm(address):
            sc += 2
        rank = (
            sc,
            (entry.get("geoLocation") or {}).get("confidence") or 0,
            bool(entry.get("dpid")),
        )
        if best is None or rank > best[0]:
            best = (rank, entry)
    return best[1] if best and best[0][0] >= 7 else None


# ---------------------------------------------------------------------------
# Profile runner
# ---------------------------------------------------------------------------
async def _run_quote_attempt(
    profile: Profile,
    session,
    result: QuoteResult,
    overrides: Optional[dict],
):
    """Run one full combined quote pass.

    Raises SumInsuredOutOfRange when either SI is out of range so the caller can
    adjust rooms and retry; other failures propagate as-is.
    """
    client = NRMAClient(session)
    client.si_overrides = overrides

    await client.step1_bootstrap(state=profile.state or "NSW")
    await client.step2_validate_entitlements()
    await client.step3_create_quote()
    await client.step4_exchange_token()

    qid = client.quote_id or ""
    result.quote_number = qid
    result.quote_link = f"{BASE_URL}/get-cover/home/3?quoteId={qid}"

    candidates = await client.step5_lookup_address(
        profile.address, profile.suburb, profile.postcode, profile.state
    )
    if not candidates:
        raise RuntimeError(
            f"No address found: {profile.address}, "
            f"{profile.suburb} {profile.state} {profile.postcode}"
        )

    matched = _find_matching_address(
        candidates, profile.address, profile.suburb, profile.state, profile.postcode
    )
    if matched:
        address_obj = matched
        log.info("│  ✓ Address matched: %s", matched.get("full_address"))
    else:
        address_obj = candidates[0]
        log.warning(
            "│  ⚠ No confident address match — falling back to %s",
            candidates[0].get("full_address"),
        )

    await client.step6_get_property(address_obj)
    prefill = await client.step7_fill_minimal_details(address_obj, profile)
    asset_id = prefill.get("quoteData", {}).get("assetPublicId")
    building_id = prefill.get("quoteData", {}).get("buildingPublicId")
    content_id = prefill.get("quoteData", {}).get("contentPublicId")

    # Step 8a (occupancy/rooms, no SI) then 8b (sum insured) — confirmed by HAR.
    await client.step_8_fill_house_details(
        profile, address_obj, asset_id, building_id, content_id
    )
    resp8b = await client.step_8b_set_sum_insured(
        profile, address_obj, asset_id, building_id, content_id
    )
    _validate_both_sum_insured(profile, resp8b)  # raises SumInsuredOutOfRange

    # The owner POST submits the party WITHOUT a PartyId; the response is where
    # the backend assigns party_id, which /offer and /price then reference.
    resp_owner = await client.step9_set_owner(
        profile, address_obj, asset_id, building_id, content_id, ""
    )
    party_id = _extract_party_id(resp_owner)

    await client.step10_refresh_token()

    offer_resp = await client.step11_submit_offer(
        profile, address_obj, asset_id, building_id, content_id, party_id
    )

    await _collect_main(
        client,
        profile,
        address_obj,
        asset_id,
        building_id,
        content_id,
        party_id,
        offer_resp,
        result,
    )


def _new_result(profile: Profile) -> QuoteResult:
    """A blank result stamped with the profile's identity — shared so a session
    that dies before any quote work still writes a row the resume pass retries."""
    return QuoteResult(
        sample_number=profile.sample_number,
        substate=profile.substate,
        dob_dd=str(profile.dob_dd),
        dob_mm=str(profile.dob_mm),
        dob_yyyy=str(profile.dob_yyyy),
        postcode=profile.postcode,
        quote_date=datetime.now().strftime("%d/%m/%Y"),
        timestamp=datetime.now().strftime("%H:%M"),
    )


async def run_profile(
    profile: Profile,
    session,
) -> QuoteResult:

    result = _new_result(profile)

    log.info(
        "▶ profile=%s | %s, %s %s %s | type=%s building_si=$%s contents_si=$%s | "
        "beds(s/m/l)=%d/%d/%d baths=%d adults=%d children=%d occupancy=%s",
        profile.sample_number,
        profile.address,
        profile.suburb,
        profile.state,
        profile.postcode,
        profile.building_type,
        f"{profile.buildings_sum_insured:,}",
        f"{profile.contents_sum_insured:,}",
        profile.small_bedrooms,
        profile.medium_bedrooms,
        profile.large_bedrooms,
        profile.bathrooms,
        profile.adults,
        profile.children,
        profile.occupancy_type,
    )

    # Attempt 1 = CSV values; out of range, si_steps re-quotes Suncorp's way
    # (building SI checked first).
    try:
        await quote_si(
            lambda overrides: _run_quote_attempt(
                profile, session, result, overrides
            ),
            profile,
            result,
            SI_FIELDS,
        )
        log.info(
            "✅ profile=%s done (rerun %s) | "
            "NRMA yearly=%s (bldg excess %s / cont excess %s) | "
            "NRMAPlus yearly=%s",
            profile.sample_number,
            result.rerun or "-",
            result.nrma_yearly or "-",
            result.nrma_default_excess or "-",
            result.nrma_default_contents_excess or "-",
            result.nrma_plus_yearly or "-",
        )
    except SumInsuredOutOfRange as exc:  # the range for the home as described
        result.failure_reason = str(exc)[:500]
        log.error("❌ profile=%s SI out of range: %s", profile.sample_number, exc)
    except Exception as exc:
        result.failure_reason = str(exc)[:500]
        log.error("❌ profile=%s FAILED: %s", profile.sample_number, exc)

    _check_output_integrity(result, profile)
    return result


# ---------------------------------------------------------------------------
# Price collector (main mode — single bundled price + both default excesses)
# ---------------------------------------------------------------------------
async def _collect_main(
    client: NRMAClient,
    profile: Profile,
    address_obj: dict,
    asset_id: str,
    building_id: str,
    content_id: str,
    party_id: str,
    offer_resp: dict,
    result: QuoteResult,
):
    result.nrma_default_excess = _extract_default_excess(offer_resp, "STANDARD")
    result.nrma_plus_default_excess = _extract_default_excess(offer_resp, "PLUS")
    result.nrma_default_contents_excess = _extract_default_contents_excess(
        offer_resp, "STANDARD"
    )
    result.nrma_plus_default_contents_excess = _extract_default_contents_excess(
        offer_resp, "PLUS"
    )

    r_std = await client.get_price(
        profile,
        address_obj,
        asset_id,
        building_id,
        content_id,
        party_id,
        level_of_cover="STANDARD",
        billing_freq="ANNUALLY",
    )
    result.nrma_yearly = _extract_price(r_std, "STANDARD", "ANNUALLY")
    agg = _extract_aggregate_premium(r_std)
    if agg:
        result.nrma_base_prem = str(agg.get("base_premium", ""))
        result.nrma_gst = str(agg.get("gst", ""))
        result.nrma_stamp_duty = str(agg.get("stamp_duty", ""))
        result.nrma_esl = str(agg.get("esl", ""))

    r_plus = await client.get_price(
        profile,
        address_obj,
        asset_id,
        building_id,
        content_id,
        party_id,
        level_of_cover="PLUS",
        billing_freq="ANNUALLY",
    )
    result.nrma_plus_yearly = _extract_price(r_plus, "PLUS", "ANNUALLY")


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
# Column names and order are the client's delivery template (Suncorp, 2026-10-22),
# plus a Rerun_ column per lookup Yes field after the template's three (Suncorp,
# 2026-10-29). test_template_columns.py pins both to the period's files.
MAIN_FIELDS = [
    "Sample_Number",
    "DOB_DD",
    "DOB_MM",
    "DOB_YYYY",
    "Postcode",
    "QuoteDate",
    "Timestamp (in hh:mm)",
    "QuoteLink",
    "QuoteNumber",
    "NRMA_DefaultExcess_Building",
    "NRMA_DefaultExcess_Content",
    "NRMA_YearlyPayment",
    "NRMAPlus_DefaultExcess_Building",
    "NRMAPlus_DefaultExcess_Content",
    "NRMAPlus_YearlyPayment",
    "NRMA_Base_Prem",
    "NRMA_GST",
    "NRMA_Stamp_Duty",
    "NRMA_ESL",
    "Reason_for_quote_failure",
] + [f"Rerun_{f}" for f in SI_FIELDS]


def to_main_row(r: QuoteResult) -> dict:
    return {
        "Sample_Number": r.sample_number,
        "DOB_DD": r.dob_dd,
        "DOB_MM": r.dob_mm,
        "DOB_YYYY": r.dob_yyyy,
        "Postcode": r.postcode,
        "QuoteDate": r.quote_date,
        "Timestamp (in hh:mm)": r.timestamp,
        "QuoteLink": r.quote_link,
        "QuoteNumber": r.quote_number,
        "NRMA_DefaultExcess_Building": r.nrma_default_excess,
        "NRMA_DefaultExcess_Content": r.nrma_default_contents_excess,
        "NRMA_YearlyPayment": r.nrma_yearly,
        "NRMAPlus_DefaultExcess_Building": r.nrma_plus_default_excess,
        "NRMAPlus_DefaultExcess_Content": r.nrma_plus_default_contents_excess,
        "NRMAPlus_YearlyPayment": r.nrma_plus_yearly,
        "NRMA_Base_Prem": r.nrma_base_prem,
        "NRMA_GST": r.nrma_gst,
        "NRMA_Stamp_Duty": r.nrma_stamp_duty,
        "NRMA_ESL": r.nrma_esl,
        "Reason_for_quote_failure": r.failure_reason,
        **{f"Rerun_{f}": r.rerun.get(f, "") for f in SI_FIELDS},
    }


# ---------------------------------------------------------------------------
# Idempotency / resume support
# ---------------------------------------------------------------------------
def _row_is_complete(row: dict) -> bool:
    """A row counts as 'properly collected' when the quote succeeded.

    SumInsured out-of-range and 'No address found' failures are terminal and
    treated as collected; any other failure leaves the row incomplete so it is
    re-run on resume.
    """
    reason = (row.get("Reason_for_quote_failure") or "").strip()
    if reason:
        # One definition of a real answer, shared with the breaker and delivery.
        return reason.startswith(TERMINAL_REASON_PREFIXES)
    return bool((row.get("NRMA_YearlyPayment") or "").strip())


def load_completed(path: str) -> tuple[set[str], list[dict]]:
    """Read an existing output CSV and split it for a safe re-run."""
    if not os.path.exists(path):
        return set(), []
    done: set[str] = set()
    keep: list[dict] = []
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            sample = (row.get("Sample_Number") or "").strip()
            if not sample or sample in done:
                continue
            if _row_is_complete(row):
                done.add(sample)
                keep.append(row)
    return done, keep


class CSVWriter:
    def __init__(
        self, path: str, fields: list[str], preserve_rows: list[dict] | None = None
    ):
        self.path = path
        self.fields = fields
        self._lock = asyncio.Lock()
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            for row in preserve_rows or []:
                writer.writerow(row)

    async def write(self, row: dict):
        async with self._lock:
            with open(self.path, "a", newline="", encoding="utf-8") as fh:
                csv.DictWriter(
                    fh, fieldnames=self.fields, extrasaction="ignore"
                ).writerow(row)


# ---------------------------------------------------------------------------
# Worker + main
# ---------------------------------------------------------------------------
# Session handling below is contents.py's, verbatim in behaviour — the reasoning
# (and the measurements behind the 403 wait and the three-way breaker) lives
# there and in Decisions.md 2026-09-09/10. Keep the two in step.
BAN_RETRY_ATTEMPTS = int(os.getenv("BAN_RETRY_ATTEMPTS", "3"))
RATE_LIMIT_BACKOFF = (
    float(os.getenv("RATE_LIMIT_BACKOFF_MIN", "30")),
    float(os.getenv("RATE_LIMIT_BACKOFF_MAX", "75")),
)
START_JITTER_SECONDS = float(os.getenv("START_JITTER_SECONDS", "20"))
PROFILE_DELAY = float(os.getenv("NRMA_PROFILE_DELAY", "0"))
# Refusals that need time to decay, not just a new exit IP (403 included —
# measured, not reasoned: contents.py 2026-09-10).
_RATE_LIMIT_MARKERS = ("→ 429", "DynaCop", "→ 403", "Access Denied")
# Refusals of the session or exit IP, worth a fresh browser on a fresh IP.
_RETRYABLE_MARKERS = (
    "Access Denied", "→ 403", "→ 401", "→ 429", "AkamaiNotValidated",
    "BrowserSessionFailed", "NetworkError", "NS_ERROR_", "has been closed",
    "Connection closed", "Timeout",
)


def _is_rate_limited(result: QuoteResult) -> bool:
    reason = result.failure_reason or ""
    return any(m in reason for m in _RATE_LIMIT_MARKERS)


def _should_retry_on_new_session(result: QuoteResult) -> bool:
    """True when the row failed because of the session, not on the merits."""
    reason = result.failure_reason or ""
    return bool(reason) and any(m in reason for m in _RETRYABLE_MARKERS)


async def _run_one_session(profile: Profile) -> QuoteResult:
    """Run a profile on one fresh Akamai-validated browser session."""
    try:
        async with quote_session(profile.state or "NSW") as session:
            return await run_profile(profile, session)
    except AkamaiNotValidated as exc:
        result = _new_result(profile)
        result.failure_reason = str(exc)[:500]
        log.error("❌ profile=%s %s", profile.sample_number, exc)
        return result
    except Exception as exc:  # browser launch / navigation failure
        result = _new_result(profile)
        result.failure_reason = f"BrowserSessionFailed: {redact(exc)}"[:500]
        log.error("❌ profile=%s browser session failed: %s", profile.sample_number, redact(exc))
        return result


# Consecutive profiles lost to an Akamai 403. Three-way on purpose: a 403
# counts up, only a real answer resets, transport noise leaves it alone.
_block_streak = 0
_halt = asyncio.Event()


def _note_outcome(result: QuoteResult) -> None:
    global _block_streak
    reason = result.failure_reason or ""
    if is_bot_block(reason):
        _block_streak += 1
    elif not reason or reason.startswith(TERMINAL_REASON_PREFIXES):
        _block_streak = 0
        return
    else:
        return
    if _block_streak >= BLOCK_STREAK_LIMIT and not _halt.is_set():
        hours = start_cooldown()
        _halt.set()
        log.error(
            "⛔ %d profiles bot-blocked with no quote getting through in "
            "between — halting this run and backing off %.0fmin. Nothing is "
            "lost: every one of them is still pending for the next pass. "
            "Delete %s to resume sooner.",
            _block_streak, hours * 60, COOLDOWN_FILE,
        )


async def worker(
    profile: Profile,
    writer: CSVWriter,
    sem: asyncio.Semaphore,
    progress: dict,
):
    async with sem:
        # Breaker tripped here or in a sibling scraper: leave the rest pending.
        if _halt.is_set() or cooldown_hours_left():
            return
        if CONCURRENCY > 1 and START_JITTER_SECONDS:
            await asyncio.sleep(random.uniform(0, START_JITTER_SECONDS))
        if PROFILE_DELAY:
            await asyncio.sleep(PROFILE_DELAY)

        # One validated session per profile; a refusal takes a whole new
        # browser and exit IP.
        for attempt in range(1, BAN_RETRY_ATTEMPTS + 1):
            result = await _run_one_session(profile)
            if not _should_retry_on_new_session(result):
                break
            if attempt < BAN_RETRY_ATTEMPTS:
                delay = (
                    random.uniform(*RATE_LIMIT_BACKOFF)
                    if _is_rate_limited(result)
                    else random.uniform(1, 4)
                )
                log.warning(
                    "⚠ profile=%s session refused on attempt %d/%d — "
                    "waiting %.0fs then retrying on a fresh IP | %s",
                    profile.sample_number, attempt, BAN_RETRY_ATTEMPTS, delay,
                    result.failure_reason[:100],
                )
                await asyncio.sleep(delay)
        if _should_retry_on_new_session(result):
            log.error(
                "❌ profile=%s gave up after %d attempts — left for the next "
                "resume pass | %s",
                profile.sample_number, BAN_RETRY_ATTEMPTS, result.failure_reason[:120],
            )
        _note_outcome(result)
        await writer.write(to_main_row(result))
        progress["done"] += 1
        log.info(
            "Progress [combined]: %d/%d (%.1f%%)",
            progress["done"],
            progress["total"],
            100 * progress["done"] / progress["total"],
        )


async def main():
    log.info("=== NRMA Combined Scraper | CONCURRENCY=%d ===", CONCURRENCY)

    # Same gate as the other two scrapers: the cooldown is shared because the
    # reputation is, and exiting is what makes the breaker stick against cron.
    resting = cooldown_hours_left()
    if resting:
        log.error(
            "⛔ account resting after a bot-block streak — %.1fh left. "
            "Delete %s to override.",
            resting,
            COOLDOWN_FILE,
        )
        return

    if not INPUT_MAIN_CSV:
        log.error(
            "no Combined input for period %s (%s) — drop the CSV into "
            "%s and check with `python period.py`",
            period.PERIOD or "?", period.PERIOD_SOURCE, period.data_dir("NRMA"),
        )
        return

    sem = asyncio.Semaphore(CONCURRENCY)
    main_profiles = load_profiles(INPUT_MAIN_CSV)

    # ── Idempotent resume: skip samples already collected properly ──────
    if FORCE_RERUN:
        done, keep = set(), []
        log.info("FORCE_RERUN set — ignoring existing output, scraping all")
    else:
        done, keep = load_completed(OUTPUT_MAIN_CSV)
    pending = [p for p in main_profiles if p.sample_number not in done]
    # Input order puts the samples that failed last pass at the head of every
    # resume; ten of those in a row trip the breaker before a fresh sample is
    # tried (the 2026-10-16 stall). Shuffled, they are spread through the run.
    random.shuffle(pending)
    log.info(
        "MAIN resume: %d already collected, %d pending of %d total",
        len(done),
        len(pending),
        len(main_profiles),
    )

    main_writer = CSVWriter(OUTPUT_MAIN_CSV, MAIN_FIELDS, preserve_rows=keep)

    if pending:
        prog_main = {"done": 0, "total": len(pending)}
        await asyncio.gather(
            *[worker(p, main_writer, sem, prog_main) for p in pending]
        )
    log.info("MAIN done → %s", OUTPUT_MAIN_CSV)


if __name__ == "__main__":
    asyncio.run(main())
