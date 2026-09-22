# contents.py
# NRMA Contents Insurance Quote Scraper — COMPLETE WITH AUTH TOKEN EXCHANGE
# Mirror of building.py, adapted for the CONTENTS sub-product.
#
# Differences vs building.py (same bootstrap / token-exchange ideology):
#   • Bootstrap product           → home-contents-standard
#   • SubProduct                  → "CONTENTS"
#   • Sum insured field           → "SumInsuredContentCustomAmount"
#   • Excess field                → "ContentsExcess"
#   • Optional extras toggled     → ContentsOptionalExtras* (building ones stay false)
#   • Default excess extraction   → buildingAndContentsExcessDetails.contents
#   • SI validation               → contentsSumInsuredMin/Max  (~$13k / large bedroom)
#   • No BuildingPublicId in payload (contents has only AssetPublicId)
#   • Adds occupancy / rent / alarm / occupant-count answers
#   • Building characteristics (roof/wall/year/slope/fixtures) are NOT in the
#     contents CSV — they are carried through from the property prefill response
#     (with sensible defaults) so the QUICK contents payload matches the website.

import asyncio
import base64
import csv
import json
import logging
import math
import os
import random
import re
import sys
import uuid
from datetime import datetime
from typing import Optional
import httpx
from dataclasses import dataclass, field

try:  # package import (python -m NRMA.home.contents)
    from .nrma_browser import (
        AkamaiNotValidated,
        BLOCK_STREAK_LIMIT,
        cooldown_hours_left,
        COOLDOWN_FILE,
        cover_start_date,
        is_bot_block,
        quote_session,
        secret,
        short_failure_reason,
        start_cooldown,
        TERMINAL_REASON_PREFIXES,
    )
except ImportError:  # direct script run
    from nrma_browser import (
        AkamaiNotValidated,
        BLOCK_STREAK_LIMIT,
        cooldown_hours_left,
        COOLDOWN_FILE,
        cover_start_date,
        is_bot_block,
        quote_session,
        secret,
        short_failure_reason,
        start_cooldown,
        TERMINAL_REASON_PREFIXES,
    )

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
# Paths come from the repo-root period module so a new run is a data drop, not
# an edit here: it resolves the period and reads the cadence word out of the
# dropped filename. Env vars still win, which is how a one-off (a single-sample
# rerun, a re-opened month) is driven without touching the file.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import period  # noqa: E402

CONCURRENCY = int(os.getenv("CONCURRENCY", "1"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "90"))
INPUT_MAIN_CSV = os.getenv("INPUT_MAIN_CSV") or period.input_csv("NRMA", "Contents") or ""
OUTPUT_MAIN_CSV = os.getenv("OUTPUT_MAIN_CSV") or period.output_csv("NRMA", "Contents") or ""
# A toggle file only exists in a quarterly drop. Absent means skip the stage —
# never fall back to another period's file, which is how the 202608 toggle
# output kept being rewritten by every 202609 resume pass.
INPUT_TOGGLE_CSV = os.getenv("INPUT_TOGGLE_CSV") or period.input_csv("NRMA", "Contents", "toggle") or ""
OUTPUT_TOGGLE_CSV = os.getenv("OUTPUT_TOGGLE_CSV") or period.output_csv("NRMA", "Contents", "toggle") or ""
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
# Sum-insured retry tuning (mirrors NrmaHomeUnifiedExtractor in home.py)
# Contents: +1 large bedroom ≈ +$13,000 to the upper bound.
# ---------------------------------------------------------------------------
SI_PER_LARGE_BEDROOM_CONTENTS = 13_000
MAX_LARGE_BEDROOM_ADDITIONS = 10
MAX_SI_RETRIES = 1  # extra attempts after the first pass

ROOM_OVERRIDE_KEYS = (
    "SmallBedrooms",
    "MediumBedrooms",
    "LargeBedrooms",
    "Bathrooms",
    "LivingSpaces",
    "DiningRooms",
)
OUTDOOR_OVERRIDE_KEYS = (
    "ParkingSpacesCarport",
    "ParkingSpacesGarage",
    "InGroundPool",
)
OCCUPANT_OVERRIDE_KEYS = (
    "AdultOccupants",
    "ChildOccupants",
)


def _apply_storeys_or_level(answers: dict, profile: "Profile") -> dict:
    """Send `PropertyLevel` instead of `Storeys` for an apartment, in place.

    The journey asks a unit which level of the building it is on, and a house how
    many storeys it has — never both. The SPA does exactly this swap:
    `[..., ...(isContents && isApartment ? ["PropertyLevel"] : ["Storeys"]), ...]`.
    Sending `Storeys` for an APARTMENT leaves `PropertyLevel` unanswered, and the
    backend then refuses to rate: /offer and /price come back 200 with
    validations=[{severity: error, rule_id: VRPP0100}] (LevelWithBuildingRequired)
    and no `billingFrequencyPrices`, which is how every apartment row ended up
    with a default excess but no premium.
    """
    if profile.building_type == "APARTMENT":
        answers.pop("Storeys", None)
        answers["PropertyLevel"] = profile.level_if_unit or "GROUND"
    return answers


def _apply_room_outdoor_overrides(
    answers: dict,
    room_overrides: Optional[dict] = None,
    outdoor_overrides: Optional[dict] = None,
    occupant_overrides: Optional[dict] = None,
) -> dict:
    """Overlay SI-retry room/outdoor/occupant overrides onto an answers dict in place."""
    if room_overrides:
        for k in ROOM_OVERRIDE_KEYS:
            if k in room_overrides:
                answers[k] = room_overrides[k]
    if outdoor_overrides:
        for k in OUTDOOR_OVERRIDE_KEYS:
            if k in outdoor_overrides:
                answers[k] = outdoor_overrides[k]
    if occupant_overrides:
        for k in OCCUPANT_OVERRIDE_KEYS:
            if k in occupant_overrides:
                answers[k] = occupant_overrides[k]
    return answers


# ---------------------------------------------------------------------------
# All excess levels per delivery template — $0 to $5000 in $50 steps + $10000
# ---------------------------------------------------------------------------
ALL_EXCESS_LEVELS = list(range(0, 5001, 50)) + [10000]  # 0, 50, 100, ... 5000

# ---------------------------------------------------------------------------
# Field Mappings
# ---------------------------------------------------------------------------
WALL_MATERIAL_MAP = {
    "AAC panel":"AAC_PANEL",
    "Blockwork":"BLOCKWORK",
    "Brick veneer":"BRICK_VENEER",
    "Double brick":"DOUBLE_BRICK",
    "Panel wall system":"PANEL_WALL_SYSTEM",
    "Rendered masonry":"RENDERED_MASONRY",
    "Sheet cladding":"SHEET_CLADDING",
    "Stonework":"STONEWORK",
    "Stonework veneer":"STONEWORK_VENEER",
    "Stonework solid":"STONEWORK_VENEER",
    "Weatherboard - timber":"WEATHERBOARD_TIMBER",
    "Weatherboard - artificial":"WEATHERBOARD_ARTIFICIAL",
    "Other":"OTHER",
}

ROOF_MATERIAL_MAP = {
    "Concrete tile":"CONCRETE_TILE",
    "Fibre cement covering":"FIBRECEMENT_COVERING",
    "Metal covering":"METAL_COVERING",
    "Slate":"SLATE",
    "Terracotta":"TERRACOTTA",
    "Timber shingles":"TIMBER_SHINGLES",
    "Other":"OTHER",
}

SLOPE_MAP = {
    "Flat or gentle": "FLAT_GENTLE",
    "Moderate": "MODERATE",
    "Steep": "STEEP",
}

# Which level of the building the unit is on — the CONTENTS journey's
# `PropertyLevel` answer, asked instead of `Storeys` when the property type is
# APARTMENT. "9-May" is Excel having eaten the "5-9" bucket as a date.
LEVEL_MAP = {
    "Ground": "GROUND",
    "1": "1",
    "2": "2",
    "3": "3",
    "4": "4",
    "5-9": "5_TO_9",
    "9-May": "5_TO_9",
    "May-09": "5_TO_9",
    "10 or higher": "10+",
    "10+": "10+",
}

STOREY_MAP = {
    "1": "1",
    "2": "2",
    "3": "3",
    "4": "4+",
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

BUILDING_TYPE = {
    "Freestanding house":"FREESTANDING_HOME",
    "Apartment or unit":"APARTMENT",
    "Townhouse, duplex or terrace":"TOWNHOUSE_TERRACE_HOUSE_OR_DUPLEX",
    "Relocatable or tiny home":"RELOCATABLE_OR_TINY_HOME",
    "Home on a farm":"HOME_ON_A_FARM",
    "Hobby farm":"HOME_HOBBY_FARM_OR_LIFESTYLE_BLOCK",
    "Unit in retirement village":"RETIREMENT_VILLAGE_UNIT",
}

# Property types the journey asks "is your home under a strata scheme or owners
# corporation?" about (its `BodyCorporateOrStrata` answer, which building.py and
# combined.py already send and contents.py did not). The contents CSV has no
# Strata column, so honour one if it ever grows one and otherwise default from
# the property type: townhouses rate fine without it and the buildings/combined
# inputs say Strata=No for them, so only the types that are strata by definition
# are defaulted True here.
STRATA_BUILDING_TYPES = {"APARTMENT", "RETIREMENT_VILLAGE_UNIT"}

# Who the renter shares with (NRMA "WhoDoYouRentWith")
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
    # property layout (contents CSV)
    storeys: str = "1"
    level_if_unit: str = ""
    strata: bool = False
    alarm: str = "NO_ALARM"
    small_bedrooms: int = 1
    medium_bedrooms: int = 2
    large_bedrooms: int = 1
    bathrooms: int = 2
    living_rooms: int = 1
    dining_rooms: int = 1
    carport: int = 0
    garage: int = 1
    small_shed: int = 0
    large_shed: int = 0
    balconies: int = 0
    verandahs: int = 0
    elevated: bool = False
    pool: bool = False
    adults: int = 1
    children: int = 0
    contents_sum_insured: int = 100000
    # specified contents items (not part of the QUICK offer payload, kept for ref)
    jewellery: bool = False
    artwork: bool = False
    collectibles: bool = False
    portable_electronics: bool = False
    sporting_items: bool = False
    mobility_scooters: bool = False
    medical_items: bool = False
    clothing: bool = False
    storage_facility: bool = False
    # owner
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
    # claims
    claims_5yrs: bool = False
    claim_type: str = ""
    claim_month: int = 0
    claim_year: int = 0
    claim_for_address: bool = False
    claim_owner: str = ""
    fraud: bool = False
    insurance_refused: bool = False
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
    nrma_default_excess: str = ""
    nrma_fortnightly: str = ""
    nrma_quarterly: str = ""
    nrma_monthly: str = ""
    nrma_yearly: str = ""
    nrma_accidental_damage: str = ""
    nrma_fusion: str = ""
    nrma_plus_default_excess: str = ""
    nrma_plus_fortnightly: str = ""
    nrma_plus_quarterly: str = ""
    nrma_plus_monthly: str = ""
    nrma_plus_yearly: str = ""
    nrma_plus_accidental_damage: str = ""
    nrma_plus_fusion: str = ""
    excess_prices: dict = field(default_factory=dict)
    excess_plus_prices: dict = field(default_factory=dict)
    failure_reason: str = ""
    rerun_small_bedrooms: str = ""
    rerun_large_bedrooms: str = ""
    rerun_adults: str = ""
    rerun_children: str = ""


# ---------------------------------------------------------------------------
# Occupancy derivation
# ---------------------------------------------------------------------------
def _derive_occupancy(
    owns: bool,
    holiday_home: bool,
    live_in: bool,
    rent_out: bool,
    flatmates: bool,
    live_with_enum: str,
) -> tuple[str, str, str]:
    """Return (PropertyOccupancyType, UsageArrangement, WhoDoYouRentWith).

    Mirrors the contents web flow. The flatmates-rental path is the one
    verified against a captured request; the other branches follow the same
    naming convention and may need tuning if NRMA rejects an enum value.
    """
    # Owner who lives in the home
    if owns and live_in and not holiday_home:
        return "OWN_AND_LIVE_IN_HOME", "OWN_AND_LIVE_IN_HOME", ""

    # Owner, holiday home
    if owns and holiday_home:
        return "HOLIDAY_HOME", "HOLIDAY_HOME", ""

    # Owner who rents the property out to others
    if owns and rent_out:
        return "RENTED_TO_OTHERS", "OWN_AND_RENT_TO_OTHERS", ""

    # Renter (does not own)
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


def _norm_type_key(value: str) -> str:
    """Collapse case and whitespace so CSV spellings match the map's keys."""
    return "".join(str(value).split()).casefold()


_BUILDING_TYPE_NORM = {_norm_type_key(k): v for k, v in BUILDING_TYPE.items()}


def _building_type(raw: str) -> str:
    """Map a CSV Building_Type, warning rather than defaulting in silence.

    The old lookup lower-cased the input against Title-case keys, so it always
    missed and every property became FREESTANDING_HOME. The delivered inputs also
    spell it 'Free standing house', with a space the map does not have.
    """
    key = _norm_type_key(raw)
    if key in _BUILDING_TYPE_NORM:
        return _BUILDING_TYPE_NORM[key]
    if key:
        log.warning(
            "Unmapped Building_Type %r — defaulting to FREESTANDING_HOME", raw
        )
    return "FREESTANDING_HOME"


def _parse_row(row: dict) -> Profile:
    building_type = _building_type(row.get("Building_Type", ""))

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
        storeys=STOREY_MAP.get(str(row.get("Storeys_If_House", "1")).strip(), "1"),
        level_if_unit=LEVEL_MAP.get(
            str(row.get("Level_If_Unit", "")).strip(), "GROUND"
        ),
        strata=(
            _bool(row["Strata"])
            if row.get("Strata")
            else building_type in STRATA_BUILDING_TYPES
        ),
        alarm=ALARM_MAP.get(row.get("Alarm", "No alarm"), "NO_ALARM"),
        small_bedrooms=_int(row.get("Small_Bedrooms", "1"), 1),
        medium_bedrooms=_int(row.get("Medium_Bedrooms", "2"), 2),
        large_bedrooms=_int(row.get("Large_Bedrooms", "1"), 1),
        bathrooms=_int(row.get("Bathrooms", "2"), 2),
        living_rooms=_int(row.get("Living_Rooms", "1"), 1),
        dining_rooms=_int(row.get("Dining_Rooms", "1"), 1),
        carport=_int(row.get("Carport", "0"), 0),
        garage=_int(row.get("Garage", "1"), 1),
        small_shed=_int(row.get("Small_Shed", "0"), 0),
        large_shed=_int(row.get("Large_Shed", "0"), 0),
        balconies=_int(row.get("Balconies", "0"), 0),
        verandahs=_int(row.get("Verandahs", "0"), 0),
        elevated=_bool(row.get("Elevated", "")),
        pool=_bool(row.get("Pool", "")),
        adults=_int(row.get("Adults", "1"), 1),
        children=_int(row.get("Children", "0"), 0),
        contents_sum_insured=_int(row.get("Contents_Sum_Insured", "100000"), 100000),
        jewellery=_bool(row.get("Jewellery", "")),
        artwork=_bool(row.get("Artwork", "")),
        collectibles=_bool(row.get("Collectibles", "")),
        portable_electronics=_bool(row.get("Portable_Electronic_Devices", "")),
        sporting_items=_bool(row.get("Sporting_Items", "")),
        mobility_scooters=_bool(row.get("Mobility_Scooters", "")),
        medical_items=_bool(row.get("Medical_Items", "")),
        clothing=_bool(row.get("Clothing", "")),
        storage_facility=_bool(row.get("Storage_Facility", "")),
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
        cookies: dict | None = None,  # ← dedicated cookie param
    ) -> tuple[int, list[tuple[str, str]], str, list[dict]]:
        """
        Execute a request via Zyte API.

        COOKIE HANDLING:
        - Cookies are passed via experimental.requestCookies (correct way)
        - Do NOT put cookies in customHttpRequestHeaders — Zyte ignores them
        - Response cookies come back in experimental.responseCookies

        Returns: (status_code, response_headers_as_list_of_tuples, body_text,
                  experimental_response_cookies)
        """
        payload: dict = {
            "url": url,
            "httpResponseBody": True,
            "httpResponseHeaders": True,
            "httpRequestMethod": method,
            # Enable experimental features for cookie handling
            "experimental": {
                "responseCookies": True,  # get cookies back from response
            },
        }

        # ── Send cookies via dedicated Zyte field ─────────────────────
        if cookies:
            payload["experimental"]["requestCookies"] = [
                {"name": k, "value": v, "domain": _cookie_domain(url)}
                for k, v in cookies.items()
                if v  # skip empty values
            ]

        # ── Send headers (WITHOUT cookie header — handled above) ──────
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

                # ── Preserve ALL headers as list of tuples ────────────
                # dict would silently drop duplicate set-cookie headers
                resp_headers: list[tuple[str, str]] = [
                    (h["name"].lower(), h["value"])
                    for h in data.get("httpResponseHeaders", [])
                ]

                # ── Also return cookies from experimental block ────────
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
    return parsed.netloc  # e.g. "insurance.nrma.com.au"


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
        self.room_overrides: Optional[dict] = None  # set during SI retry
        self.outdoor_overrides: Optional[dict] = None  # set during SI retry
        self.occupant_overrides: Optional[dict] = None  # set during SI retry
        # Property characteristics prefilled from the property reference API.
        self.prefill_attrs: dict = {}

    # ── Cookie management ─────────────────────────────────────────────

    def _update_cookies_from_headers(self, resp_headers: list[tuple[str, str]]):
        """
        Parse set-cookie from response header list of tuples.
        Handles multiple set-cookie headers correctly.
        """
        for name, value in resp_headers:
            if name != "set-cookie":
                continue
            # Only take name=value, ignore Path/Domain/HttpOnly attributes
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
        """
        Parse cookies from Zyte experimental.responseCookies block.
        This is the PRIMARY source — more reliable than set-cookie headers.
        """
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
        """
        Build request headers — NO cookie header here.
        Cookies are passed separately to ZyteSession.request(cookies=...)
        """
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
            cookies=self.cookies,  # ← cookies sent correctly
        )
        self._update_all_cookies(hdrs, exp_cookies)  # ← updated after every request
        if status >= 400:
            raise RuntimeError(f"GET {url} → {status}: {body[:300]}")
        try:
            json.loads(body)
        except:
            return {}
        return json.loads(body) if body.strip() else {}

    async def _post(
        self,
        url: str,
        payload: dict,
        referer: str = "",
        override_cookies: dict | None = None,
        surpass=False
    ) -> dict:
        status, hdrs, body, exp_cookies = await self.zyte.request(
            url,
            method="POST",
            headers=self._json_headers(referer),
            body=json.dumps(payload),
            cookies=override_cookies or self.cookies,  # ← cookies sent correctly
        )
        self._update_all_cookies(hdrs, exp_cookies)  # ← updated after every request
        if status >= 400 and not surpass:
            raise RuntimeError(f"POST {url} → {status}: {body[:300]}")
        return json.loads(body) if body.strip() else {}

    async def _put(self, url: str, payload: dict, referer: str = "") -> dict:
        status, hdrs, body, exp_cookies = await self.zyte.request(
            url,
            method="PUT",
            headers=self._json_headers(referer),
            body=json.dumps(payload),
            cookies=self.cookies,  # ← cookies sent correctly
        )
        self._update_all_cookies(hdrs, exp_cookies)  # ← updated after every request
        if status >= 400:
            raise RuntimeError(f"PUT {url} → {status}: {body[:300]}")
        return json.loads(body) if body.strip() else {}

    # ================================================================
    # Step 1 — Bootstrap: land on site, get Akamai cookies
    # ================================================================
    async def step1_bootstrap(self, state: str = "NSW"):
        url = (
            f"{BASE_URL}/get-cover/home?"
            f"serenityproduct=home-contents-standard"
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
        """The app's own first API call. Restored 2026-09-09.

        Not needed for the data, which is why it was dropped — but the `.har`
        capture of a real journey calls it six times, and skipping it made our
        request sequence start with the quote creation, a shape no real client
        produces. A 404/401 here says nothing about the property, so a failure
        must not lose the row.
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
    # POST https://connect.nrma.com.au/cb/anonymous/quote/nrma
    # ================================================================
    async def step4_exchange_token(self):
        payload = {
            "payload": {
                "quoteId": self.quote_id,
                "expiry": self.signature_expiry,
            },
            "signature": self.quote_signature,
        }

        # connect.nrma.com.au is a different domain — use same-site headers
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

        # Only send insurance.nrma.com.au cookies to connect subdomain
        # (filter to avoid sending unrelated cookies cross-domain)
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

        # Store and inject into cookie jar for all future requests
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
        resp = await self._post(
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
            surpass=True
        )
        # Capture prefilled building characteristics (contents CSV lacks these).
        self.prefill_attrs = _prefill_property_attrs(resp)
        return resp

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
                "SubProduct": "CONTENTS",
                "CoverStartDate": cover_start,
                "PropertyAddressLookup": address_obj,
                "PropertyManualAddress": None,
            },
        }
        resp = await self._post(
            f"{BASE_URL}/domain/question/api/question/api/2.x.x/home/quote/{self.quote_id}",
            payload=json_data,
            referer=f"{BASE_URL}/get-cover/home/3/risk-address",
        )
        # Property reference attrs may only appear after the quote is seeded.
        attrs = _prefill_property_attrs(resp)
        if attrs:
            self.prefill_attrs = {**self.prefill_attrs, **attrs}
        return resp

    # ================================================================
    # Shared: build answers dict
    # ================================================================
    def _build_answers(
        self,
        profile: Profile,
        address_obj: dict,
        asset_id: str,
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

        attrs = self.prefill_attrs or {}

        answers: dict = {
            "BillingFrequency": billing_freq,
            "PayDirectDebit": ["true"],
            "LevelOfCover": level_of_cover,
            "HowToSendPolicyDocuments": "email_address",
            "BuildingOptionalExtrasAccidentalDamage": ["false"],
            "BuildingOptionalExtrasElectricalBurnOut": ["false"],
            "ContentsOptionalExtrasAccidentalDamage": [str(accidental_damage).lower()],
            "ContentsOptionalExtrasElectricalBurnOut": [str(fusion).lower()],
            "TermsConditionAgreement": True,
            "SubProduct": "CONTENTS",
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
            "RoofMaterial": attrs.get("RoofMaterial", "CONCRETE_TILE"),
            "WallMaterial": attrs.get("WallMaterial", "BRICK_VENEER"),
            "LandSlope": attrs.get("LandSlope", "FLAT_GENTLE"),
            # The contents journey never asks how many storeys the home has, so the
            # SPA submits whatever NRMA's property data (SVX/ARTURO) prefilled and
            # the CSV's Storeys_If_House is not part of this product's question set.
            # Sending it anyway priced 120 BRISBANE CORSO as single-storey: 5859.24
            # against the website's 5627.38. The CSV stays as the fallback for an
            # address NRMA has no storey_count for, since a non-apartment must
            # always answer Storeys.
            "Storeys": attrs.get("Storeys", profile.storeys),
            "BuiltInHeatingOrCooling": False,
            "InGroundPool": profile.pool,
            "TennisCourt": False,
            "SolarPanels": False,
            "QualityOfFixtures": attrs.get("QualityOfFixtures", "STANDARD"),
            "Verandahs": profile.verandahs,
            "BalconiesAndDecks": profile.balconies,
            "SmallSheds": profile.small_shed,
            "LargeSheds": profile.large_shed,
            "FloodingRisk": False,
            "JettyWharfLandingPier": False,
            "BodyCorporateOrStrata": profile.strata,
            "SumInsuredContentCustomAmount": str(profile.contents_sum_insured),
            "AssetPublicId": asset_id,
            "LivingSpaces": profile.living_rooms,
            "DiningRooms": profile.dining_rooms,
            "Alarm": profile.alarm,
            "PropertyOccupancyType": profile.occupancy_type,
            "AdultOccupants": profile.adults,
            "ChildOccupants": profile.children,
            "UsageArrangement": profile.usage_arrangement,
        }

        if profile.who_do_you_rent_with:
            answers["WhoDoYouRentWith"] = profile.who_do_you_rent_with

        _apply_room_outdoor_overrides(
            answers,
            self.room_overrides,
            self.outdoor_overrides,
            self.occupant_overrides,
        )
        _apply_storeys_or_level(answers, profile)

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

        # PriorClaims / ClaimsMade must only be sent once a PartyId exists
        # (i.e. on /offer and /price). Sending them on the earlier occupancy /
        # sum-insured / owner POSTs makes the backend return 500 "Error
        # updating quote".
        if party_id:
            answers["PriorClaims"] = prior_claims
            answers["ClaimsMade"] = profile.claims_5yrs

        if excess is not None:
            answers["ContentsExcess"] = str(excess)

        return answers

    # ================================================================
    # Step 8 — fill property + occupancy details
    # ================================================================
    async def step_8_fill_house_details(
        self, profile: Profile, address_obj: dict, asset_id: str
    ) -> dict:

        cover_start = profile.policy_effective_date  # validated at load time

        attrs = self.prefill_attrs or {}

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
            "SubProduct": "CONTENTS",
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
            "RoofMaterial": attrs.get("RoofMaterial", "CONCRETE_TILE"),
            "WallMaterial": attrs.get("WallMaterial", "BRICK_VENEER"),
            "LandSlope": attrs.get("LandSlope", "FLAT_GENTLE"),
            # The contents journey never asks how many storeys the home has, so the
            # SPA submits whatever NRMA's property data (SVX/ARTURO) prefilled and
            # the CSV's Storeys_If_House is not part of this product's question set.
            # Sending it anyway priced 120 BRISBANE CORSO as single-storey: 5859.24
            # against the website's 5627.38. The CSV stays as the fallback for an
            # address NRMA has no storey_count for, since a non-apartment must
            # always answer Storeys.
            "Storeys": attrs.get("Storeys", profile.storeys),
            "BuiltInHeatingOrCooling": False,
            "InGroundPool": profile.pool,
            "TennisCourt": False,
            "SolarPanels": False,
            "QualityOfFixtures": attrs.get("QualityOfFixtures", "STANDARD"),
            "Verandahs": profile.verandahs,
            "BalconiesAndDecks": profile.balconies,
            "SmallSheds": profile.small_shed,
            "LargeSheds": profile.large_shed,
            "FloodingRisk": False,
            "JettyWharfLandingPier": False,
            "BodyCorporateOrStrata": profile.strata,
            "AssetPublicId": asset_id,
            "LivingSpaces": profile.living_rooms,
            "DiningRooms": profile.dining_rooms,
            "Alarm": profile.alarm,
            "PropertyOccupancyType": profile.occupancy_type,
            "AdultOccupants": profile.adults,
            "ChildOccupants": profile.children,
            "UsageArrangement": profile.usage_arrangement,
        }
        if profile.who_do_you_rent_with:
            answers["WhoDoYouRentWith"] = profile.who_do_you_rent_with

        _apply_room_outdoor_overrides(
            answers, self.room_overrides, self.outdoor_overrides, self.occupant_overrides
        )
        _apply_storeys_or_level(answers, profile)

        return await self._post(
            f"{BASE_URL}/domain/question/api/question/api/2.x.x"
            f"/home/quote/{self.quote_id}",
            payload={"quoteType": "QUICK", "answers": answers},
            referer=f"{BASE_URL}/get-cover/home/3/occupancy",
        )

    # ================================================================
    # Step 8b — set sum insured (sum-insured page)
    # ================================================================
    async def step_8b_set_sum_insured(
        self, profile: Profile, address_obj: dict, asset_id: str
    ) -> dict:
        answers = self._build_answers(
            profile, address_obj, asset_id, include_parties=False
        )
        return await self._post(
            f"{BASE_URL}/domain/question/api/question/api/2.x.x"
            f"/home/quote/{self.quote_id}",
            payload={"quoteType": "QUICK", "answers": answers},
            referer=f"{BASE_URL}/get-cover/home/3/sum-insured",
        )

    async def step9_set_owner(
        self,
        profile: Profile,
        address_obj: dict,
        asset_id: str,
        party_id: str,
    ) -> dict:
        return await self._post(
            f"{BASE_URL}/domain/question/api/question/api/2.x.x"
            f"/home/quote/{self.quote_id}",
            payload={
                "quoteType": "QUICK",
                "answers": self._build_answers(
                    profile, address_obj, asset_id, party_id=party_id
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
    # Step 12/13/16 — PUT /price
    # ================================================================
    async def get_price(
        self,
        profile: Profile,
        address_obj: dict,
        asset_id: str,
        party_id: str,
        level_of_cover: str = "STANDARD",
        billing_freq: str = "ANNUALLY",
        excess: Optional[int] = None,
        accidental_damage: bool = False,
        fusion: bool = False,
    ) -> dict:

        answers = self._build_answers(
            profile,
            address_obj,
            asset_id,
            billing_freq=billing_freq,
            level_of_cover=level_of_cover,
            party_id=party_id,
            excess=excess,
            accidental_damage=accidental_damage,
            fusion=fusion,
        )
        return await self._put(
            f"{BASE_URL}/domain/question/api/question/api/2.x.x"
            f"/home/quote/{self.quote_id}/price",
            payload={"quoteType": "QUICK", "answers": answers},
            referer=f"{BASE_URL}/get-cover/home/3/quote-consent",
        )


# ---------------------------------------------------------------------------
# Prefill property attribute extraction
# ---------------------------------------------------------------------------
def _whole_storeys(val) -> str | None:
    """NRMA's property prefill (SVX/ARTURO) returns `storey_count: 1.5` for
    split-level homes. The journey's answer set is 1/2/3/4+, and the backend
    rejects "1.5" with an anonymous `severity: error` validation (no rule_id)
    that wipes the party — /offer and /price then return no excess and no
    premium, which the scraper reported as `QuoteNotPriced|no validations`.
    Every unpriced 202609 Contents row was this (verified live 2026-09-22:
    forcing the CSV's "2" returned the same range the UI gives). None means
    "use the CSV's Storeys", exactly as when the prefill has no storey_count.
    """
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    return str(int(f)) if f.is_integer() and f >= 1 else None


def _prefill_property_attrs(resp: dict) -> dict:
    """Pull building characteristics from a property-reference / quote response.

    The contents CSV does not carry roof / wall / slope / year / fixtures, but
    the QUICK contents payload echoes them, so we reuse whatever the property
    reference API prefilled. Missing keys fall back to caller defaults.
    """
    if not isinstance(resp, dict):
        return {}
    qd = resp.get("quoteData", resp)
    ans = qd.get("answers", {}) if isinstance(qd.get("answers"), dict) else {}

    def pick(key):
        for src in (qd, ans):
            if isinstance(src, dict) and src.get(key):
                return src.get(key)
        return None

    out: dict = {}
    for k, key in (
        ("RoofMaterial", "roof_material"),
        ("WallMaterial", "wall_material"),
        ("LandSlope", "slope"),
        ("QualityOfFixtures", "quality_of_home"),
        ("Storeys", "storey_count"),
    ):
        val = pick(key)
        if key == "storey_count":
            val = _whole_storeys(val)  # 1.5 -> None (CSV fallback), 2.0 -> "2"
        if val:
            out[k] = str(val)
    # The contents CSV carries no construction characteristics and the journey
    # never asks for them, so anything the prefill withholds is quoted on the
    # built-in defaults instead — and for Storeys, on the CSV's buildings-survey
    # column, which is not this product's answer. Silent substitutes are
    # indistinguishable from real data in the output, so name them. Reported per
    # response: only the property-reference call carries these keys, so a bare
    # quote response listing all five is expected and harmless.
    missing = [
        k for k in ("RoofMaterial", "WallMaterial", "LandSlope",
                    "QualityOfFixtures", "Storeys")
        if k not in out
    ]
    if missing:
        log.warning(
            "│  prefill supplied no %s — falling back to defaults",
            ", ".join(missing),
        )
    return out


# ---------------------------------------------------------------------------
# Price extraction
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


def _validation_errors(resp: dict) -> str:
    """Error-severity rule ids the backend attached to a response, as "A|B".

    The question API answers 200 even when it refuses to rate, putting the
    reason in `validations` — e.g. an APARTMENT with no PropertyLevel answer
    comes back with rule VRPP0100 and no `billingFrequencyPrices`. The
    scraper used to drop that, so the row landed with an excess, no premium and
    no Reason_for_quote_failure at all.
    """
    if not isinstance(resp, dict):
        return ""
    # An error with no rule_id is still a refusal — name it, or the row reads
    # "no validations" and gets chased as a soft block (202609 Contents).
    rules = [
        str(v.get("rule_id") or "error-without-rule_id").strip()
        for v in resp.get("validations") or []
        if isinstance(v, dict) and str(v.get("severity", "")).lower() == "error"
    ]
    return "|".join(r for r in rules if r)


def _extract_default_excess(resp: dict, level: str = "STANDARD") -> str:
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
# Real billing-frequency loading (admin fees) is a few percent at most, so a
# gap this large means something came back wrong (e.g. the 202608 CBDDISC
# batch, where Monthly/Fortnightly came back exactly 2x the correct value
# while Quarterly/Yearly were fine). These checks never alter or drop data —
# they only log so a bad row is visible in scraper.log instead of shipping
# to the client silently.
_FREQUENCY_SANITY_TOLERANCE = 0.25


def _check_frequency_consistency(result: "QuoteResult", profile: "Profile") -> None:
    """Warn if Quarterly/Monthly/Fortnightly aren't ~proportional to Yearly."""
    groups = [
        ("nrma_yearly", [
            ("NRMA_QuarterlyPayment", "nrma_quarterly", 4),
            ("NRMA_MonthlyPayment", "nrma_monthly", 12),
            ("NRMA_FortnightlyPayment", "nrma_fortnightly", 26),
        ]),
        ("nrma_plus_yearly", [
            ("NRMAPlus_QuarterlyPayment", "nrma_plus_quarterly", 4),
            ("NRMAPlus_MonthlyPayment", "nrma_plus_monthly", 12),
            ("NRMAPlus_FortnightlyPayment", "nrma_plus_fortnightly", 26),
        ]),
    ]
    for yearly_attr, checks in groups:
        try:
            yearly_val = float(getattr(result, yearly_attr, "") or "")
        except ValueError:
            continue
        if yearly_val <= 0:
            continue
        for label, attr, divisor in checks:
            actual_str = getattr(result, attr, "")
            if not actual_str:
                continue
            try:
                actual = float(actual_str)
            except ValueError:
                continue
            expected = yearly_val / divisor
            if expected <= 0:
                continue
            ratio = actual / expected
            if abs(ratio - 1.0) > _FREQUENCY_SANITY_TOLERANCE:
                log.warning(
                    "profile=%s FREQUENCY SANITY CHECK FAILED: %s=%.2f is %.2fx "
                    "the expected value (%.2f, derived from %s=%.2f/%d) — "
                    "possible pricing/extraction anomaly, consider re-fetching this row",
                    profile.sample_number, label, actual, ratio, expected,
                    yearly_attr, yearly_val, divisor,
                )


def _check_optional_cover_pricing(result: "QuoteResult", profile: "Profile") -> None:
    """Warn if STANDARD AD/Fusion price equals the base Yearly price.

    Guards against a regression of a prior bug where the optional-cover
    toggle was silently dropped before pricing, making AD/Fusion identical
    to the base premium. (NRMAPlus AD/Fusion legitimately equals its Yearly
    price — Plus already includes both as standard — so only STANDARD/NRMA
    is checked here.)
    """
    try:
        yearly_val = float(getattr(result, "nrma_yearly", "") or "")
    except ValueError:
        return
    if yearly_val <= 0:
        return
    for label, attr in [
        ("NRMA_AccidentalDamage", "nrma_accidental_damage"),
        ("NRMA_Fusion", "nrma_fusion"),
    ]:
        actual_str = getattr(result, attr, "")
        if not actual_str:
            continue
        try:
            actual = float(actual_str)
        except ValueError:
            continue
        if actual <= yearly_val:
            log.warning(
                "profile=%s OPTIONAL COVER SANITY CHECK FAILED: %s=%.2f is not "
                "greater than NRMA_YearlyPayment=%.2f — optional cover toggle "
                "may not have been applied before pricing",
                profile.sample_number, label, actual, yearly_val,
            )


_VALID_EXCESS_TIERS = set(ALL_EXCESS_LEVELS)


def _check_output_integrity(result: "QuoteResult", profile: "Profile") -> None:
    """General row-level sanity checks, run for every completed quote.

    Covers classes of bug distinct from the frequency/optional-cover checks
    above: a row silently ending up half-success/half-failure, a Plus quote
    coming back cheaper than Standard, or a DefaultExcess value that isn't
    one of the tiers we ever request (all signs of a mismatched/misparsed
    API response).
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
        ("NRMA_DefaultExcess", "nrma_default_excess"),
        ("NRMAPlus_DefaultExcess", "nrma_plus_default_excess"),
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


def _check_excess_sweep_consistency(result: "QuoteResult", profile: "Profile") -> None:
    """Toggle-only: the excess-sweep price at the DefaultExcess tier should
    match the YearlyPayment collected independently via the frequency loop —
    both represent the same STANDARD/PLUS ANNUALLY price at the same excess.
    """
    for label, default_excess_attr, yearly_attr, prices_attr in [
        ("NRMA", "nrma_default_excess", "nrma_yearly", "excess_prices"),
        ("NRMAPlus", "nrma_plus_default_excess", "nrma_plus_yearly", "excess_plus_prices"),
    ]:
        default_excess = (getattr(result, default_excess_attr, "") or "").strip()
        yearly = (getattr(result, yearly_attr, "") or "").strip()
        if not default_excess or not yearly:
            continue
        try:
            tier = int(float(default_excess))
        except ValueError:
            continue
        prices = getattr(result, prices_attr, {})
        sweep_val = prices.get(tier)
        if sweep_val is None:
            continue
        try:
            if abs(float(sweep_val) - float(yearly)) > 1.0:
                log.warning(
                    "profile=%s EXCESS SWEEP CONSISTENCY CHECK FAILED: %s excess_%d=%s "
                    "does not match %s=%s — same tier, independently fetched, should agree",
                    profile.sample_number, label, tier, sweep_val, yearly_attr, yearly,
                )
        except ValueError:
            pass


class SumInsuredOutOfRange(ValueError):
    """Raised when the target sum insured is outside the API's accepted range."""

    def __init__(self, si_type, target, minimum, maximum, direction):
        self.si_type = si_type
        self.target = target
        self.minimum = minimum
        self.maximum = maximum
        self.direction = direction
        super().__init__(
            f"SumInsuredOutOfRange|{si_type}|{direction}|"
            f"target={target}|min={minimum}|max={maximum}"
        )


def _validate_sum_insured(profile: Profile, resp: dict):
    """Check the contents sum insured against the API's calculated range.

    Raises SumInsuredOutOfRange (direction 'low'/'high') when out of range so
    the caller can adjust rooms/occupants and retry.
    """
    calc = resp.get("quoteData", {}).get("calculatedSumInsured", {})
    sum_insured_max = calc.get("contentsSumInsuredMax") or 0
    sum_insured_min = calc.get("contentsSumInsuredMin") or 0
    contents_sum = profile.contents_sum_insured

    log.info(
        f"│  Contents: ${contents_sum:,} "
        f"(range: ${sum_insured_min:,} — ${sum_insured_max:,})"
    )

    if sum_insured_min and contents_sum < sum_insured_min:
        raise SumInsuredOutOfRange(
            "contents", contents_sum, sum_insured_min, sum_insured_max, "low"
        )
    if sum_insured_max and contents_sum > sum_insured_max:
        raise SumInsuredOutOfRange(
            "contents", contents_sum, sum_insured_min, sum_insured_max, "high"
        )


# ── Room/occupant adjustment strategies (mirror home.py contents logic) ─────


def _rooms_for_too_low(target_si: int) -> tuple[dict, dict, dict]:
    """Minimise rooms + occupants to push the contents range lower.

    Contents SI is driven by rooms AND the number of occupants, so we trim both.
    """
    log.info(
        "│  Strategy (contents too low): "
        "1 small bed, 1 bath, 1 living, rest=0, adults=1, children=0"
    )
    rooms = {
        "SmallBedrooms": "1",
        "MediumBedrooms": "0",
        "LargeBedrooms": "0",
        "Bathrooms": "1",
        "LivingSpaces": "1",
        "DiningRooms": "0",
    }
    outdoor = {
        "ParkingSpacesCarport": "0",
        "ParkingSpacesGarage": "0",
        "InGroundPool": False,
    }
    occupants = {
        "AdultOccupants": 1,
        "ChildOccupants": 0,
    }
    return rooms, outdoor, occupants


def _rooms_for_too_high(
    profile: Profile, target: int, maximum: int
) -> tuple[dict, dict, dict]:
    """Keep existing rooms, add large bedrooms to lift the upper bound.

    Contents: +1 large bedroom ≈ +$13,000 to the upper bound.
    """
    difference = max(target - maximum, 0)
    rate = SI_PER_LARGE_BEDROOM_CONTENTS
    additions = math.ceil(difference / rate) if rate > 0 else 1
    additions = max(1, min(additions, MAX_LARGE_BEDROOM_ADDITIONS))

    new_large = profile.large_bedrooms + additions
    log.info(
        f"│  Strategy (too high): difference=${difference:,}, "
        f"rate=${rate:,}/bedroom, adding {additions} large bedrooms "
        f"({profile.large_bedrooms} → {new_large})"
    )

    rooms = {
        "SmallBedrooms": str(profile.small_bedrooms),
        "MediumBedrooms": str(profile.medium_bedrooms),
        "LargeBedrooms": str(new_large),
        "Bathrooms": str(profile.bathrooms),
        "LivingSpaces": str(profile.living_rooms),
        "DiningRooms": str(profile.dining_rooms),
    }
    return rooms, {}, {}


def _calculate_si_adjustment(
    profile: Profile, exc: SumInsuredOutOfRange
) -> tuple[dict, dict, dict]:
    """Pick the room/outdoor/occupant override for an out-of-range error."""
    if exc.direction == "low":
        return _rooms_for_too_low(exc.target)
    return _rooms_for_too_high(profile, exc.target, exc.maximum)


def _populate_rerun_columns(
    result: "QuoteResult",
    room_overrides: Optional[dict],
    occupant_overrides: Optional[dict],
):
    """Record the adjusted room/occupant values in the rerun output columns."""
    if room_overrides:
        result.rerun_small_bedrooms = str(room_overrides.get("SmallBedrooms", ""))
        result.rerun_large_bedrooms = str(room_overrides.get("LargeBedrooms", ""))
    if occupant_overrides:
        result.rerun_adults = str(occupant_overrides.get("AdultOccupants", ""))
        result.rerun_children = str(occupant_overrides.get("ChildOccupants", ""))


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
    """Pick the candidate that best matches the requested address.

    Scores each lookup result on postcode/state/suburb and an exact
    display_address_line1 match, returning the highest-scoring entry or None
    if none clears the threshold.
    """
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
    mode: str,
    result: QuoteResult,
    room_overrides: Optional[dict],
    outdoor_overrides: Optional[dict],
    occupant_overrides: Optional[dict],
):
    """Run one full quote pass.

    Raises SumInsuredOutOfRange when the contents SI is out of range so the
    caller can adjust rooms/occupants and retry; other failures propagate as-is.
    """
    client = NRMAClient(session)
    client.room_overrides = room_overrides
    client.outdoor_overrides = outdoor_overrides
    client.occupant_overrides = occupant_overrides

    await client.step1_bootstrap(state=profile.state or "NSW")
    await client.step2_validate_entitlements()
    await client.step3_create_quote()
    await client.step4_exchange_token()

    # client.quote_id may be None; ensure we store an empty string instead
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

    await client.step_8_fill_house_details(profile, address_obj, asset_id)
    resp8b = await client.step_8b_set_sum_insured(profile, address_obj, asset_id)
    _validate_sum_insured(profile, resp8b)  # raises SumInsuredOutOfRange

    # The owner POST submits the party WITHOUT a PartyId; the response is where
    # the backend assigns party_id, which /offer and /price then reference.
    resp_owner = await client.step9_set_owner(profile, address_obj, asset_id, "")
    party_id = _extract_party_id(resp_owner)

    await client.step10_refresh_token()

    offer_resp = await client.step11_submit_offer(
        profile, address_obj, asset_id, party_id
    )

    if mode == "main":
        await _collect_main(
            client, profile, address_obj, asset_id, party_id, offer_resp, result
        )
    else:
        await _collect_toggle(
            client, profile, address_obj, asset_id, party_id, offer_resp, result
        )


def _new_result(profile: Profile) -> QuoteResult:
    """A blank result stamped with the profile's identity.

    Shared so a session that dies before any quote work still writes a row that
    can be matched back to its sample and retried on the next resume pass.
    """
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
    mode: str = "main",
) -> QuoteResult:

    result = _new_result(profile)

    log.info(
        "▶ profile=%s mode=%s | %s, %s %s %s | type=%s contents_si=$%s | "
        "beds(s/m/l)=%d/%d/%d baths=%d adults=%d children=%d occupancy=%s",
        profile.sample_number,
        mode,
        profile.address,
        profile.suburb,
        profile.state,
        profile.postcode,
        profile.building_type,
        f"{profile.contents_sum_insured:,}",
        profile.small_bedrooms,
        profile.medium_bedrooms,
        profile.large_bedrooms,
        profile.bathrooms,
        profile.adults,
        profile.children,
        profile.occupancy_type,
    )

    room_overrides: Optional[dict] = None
    outdoor_overrides: Optional[dict] = None
    occupant_overrides: Optional[dict] = None

    # Attempt 1 = CSV values; each SI-out-of-range failure adjusts rooms/occupants
    # and re-runs a fresh quote, up to MAX_SI_RETRIES extra attempts.
    for attempt in range(1, MAX_SI_RETRIES + 2):
        try:
            await _run_quote_attempt(
                profile,
                session,
                mode,
                result,
                room_overrides,
                outdoor_overrides,
                occupant_overrides,
            )
            _populate_rerun_columns(result, room_overrides, occupant_overrides)
            log.info(
                "✅ profile=%s mode=%s done (attempt %d) | "
                "NRMA yearly=%s monthly=%s (excess %s) | "
                "NRMAPlus yearly=%s monthly=%s (excess %s) | AD=%s Fusion=%s",
                profile.sample_number,
                mode,
                attempt,
                result.nrma_yearly or "-",
                result.nrma_monthly or "-",
                result.nrma_default_excess or "-",
                result.nrma_plus_yearly or "-",
                result.nrma_plus_monthly or "-",
                result.nrma_plus_default_excess or "-",
                result.nrma_accidental_damage or "-",
                result.nrma_fusion or "-",
            )
            _check_output_integrity(result, profile)
            return result

        except SumInsuredOutOfRange as exc:
            if attempt > MAX_SI_RETRIES:
                _populate_rerun_columns(result, room_overrides, occupant_overrides)
                result.failure_reason = str(exc)[:500]
                log.error(
                    "❌ profile=%s SI out of range after %d attempts: %s",
                    profile.sample_number,
                    attempt,
                    exc,
                )
                _check_output_integrity(result, profile)
                return result

            room_overrides, outdoor_overrides, occupant_overrides = (
                _calculate_si_adjustment(profile, exc)
            )
            log.info(
                "⚠ profile=%s SI %s — retrying with adjusted rooms/occupants",
                profile.sample_number,
                exc.direction,
            )

        except Exception as exc:
            result.failure_reason = str(exc)[:500]
            log.error("❌ profile=%s FAILED: %s", profile.sample_number, exc)
            _check_output_integrity(result, profile)
            return result

    _check_output_integrity(result, profile)
    return result


# ---------------------------------------------------------------------------
# Price collectors
# ---------------------------------------------------------------------------
async def _collect_main(
    client: NRMAClient,
    profile: Profile,
    address_obj: dict,
    asset_id: str,
    party_id: str,
    offer_resp: dict,
    result: QuoteResult,
):
    result.nrma_default_excess = _extract_default_excess(offer_resp, "STANDARD")
    result.nrma_plus_default_excess = _extract_default_excess(offer_resp, "PLUS")
    if not result.nrma_default_excess or not result.nrma_plus_default_excess:
        log.warning(
            "profile=%s missing default excess (STANDARD=%r PLUS=%r) — "
            "offer_resp.quoteData=%s",
            profile.sample_number, result.nrma_default_excess,
            result.nrma_plus_default_excess, offer_resp.get("quoteData"),
        )

    r_std = await client.get_price(
        profile,
        address_obj,
        asset_id,
        party_id,
        level_of_cover="STANDARD",
        billing_freq="ANNUALLY",
    )
    result.nrma_yearly = _extract_price(r_std, "STANDARD", "ANNUALLY")
    if not result.nrma_yearly:
        rules = _validation_errors(r_std)
        result.failure_reason = f"QuoteNotPriced|{rules or 'no validations'}"
        log.warning(
            "profile=%s missing NRMA yearly price — validation errors=%s — "
            "response.quoteData=%s",
            profile.sample_number, rules or "none", r_std.get("quoteData"),
        )

    r_plus = await client.get_price(
        profile,
        address_obj,
        asset_id,
        party_id,
        level_of_cover="PLUS",
        billing_freq="ANNUALLY",
    )
    result.nrma_plus_yearly = _extract_price(r_plus, "PLUS", "ANNUALLY")
    if not result.nrma_plus_yearly:
        rules = _validation_errors(r_plus)
        result.failure_reason = f"QuoteNotPriced|{rules or 'no validations'}"
        log.warning(
            "profile=%s missing NRMAPlus yearly price — validation errors=%s — "
            "response.quoteData=%s",
            profile.sample_number, rules or "none", r_plus.get("quoteData"),
        )


async def _collect_toggle(
    client: NRMAClient,
    profile: Profile,
    address_obj: dict,
    asset_id: str,
    party_id: str,
    offer_resp: dict,
    result: QuoteResult,
):
    result.nrma_default_excess = _extract_default_excess(offer_resp, "STANDARD")
    result.nrma_plus_default_excess = _extract_default_excess(offer_resp, "PLUS")

    # Frequency prices at default excess — STANDARD
    for attr, freq in [
        ("nrma_fortnightly", "FORTNIGHTLY"),
        ("nrma_quarterly", "QUARTERLY"),
        ("nrma_monthly", "MONTHLY"),
        ("nrma_yearly", "ANNUALLY"),
    ]:
        r = await client.get_price(
            profile,
            address_obj,
            asset_id,
            party_id,
            level_of_cover="STANDARD",
            billing_freq=freq,
        )
        setattr(result, attr, _extract_price(r, "STANDARD", freq))

    # Frequency prices at default excess — PLUS
    for attr, freq in [
        ("nrma_plus_fortnightly", "FORTNIGHTLY"),
        ("nrma_plus_quarterly", "QUARTERLY"),
        ("nrma_plus_monthly", "MONTHLY"),
        ("nrma_plus_yearly", "ANNUALLY"),
    ]:
        r = await client.get_price(
            profile,
            address_obj,
            asset_id,
            party_id,
            level_of_cover="PLUS",
            billing_freq=freq,
        )
        setattr(result, attr, _extract_price(r, "PLUS", freq))

    # AD prices — offer sets the option, then get_price fetches each level
    await client.step11_submit_offer(
        profile,
        address_obj,
        asset_id,
        party_id,
        accidental_damage=True,
        fusion=False,
        referer_page="optional-benefits",
    )
    r_ad_std = await client.get_price(
        profile, address_obj, asset_id, party_id,
        level_of_cover="STANDARD", billing_freq="ANNUALLY",
        accidental_damage=True,
    )
    r_ad_plus = await client.get_price(
        profile, address_obj, asset_id, party_id,
        level_of_cover="PLUS", billing_freq="ANNUALLY",
        accidental_damage=True,
    )
    result.nrma_accidental_damage = _extract_price(r_ad_std, "STANDARD", "ANNUALLY")
    result.nrma_plus_accidental_damage = _extract_price(r_ad_plus, "PLUS", "ANNUALLY")

    # Fusion prices
    await client.step11_submit_offer(
        profile,
        address_obj,
        asset_id,
        party_id,
        accidental_damage=False,
        fusion=True,
        referer_page="optional-benefits",
    )
    r_fu_std = await client.get_price(
        profile, address_obj, asset_id, party_id,
        level_of_cover="STANDARD", billing_freq="ANNUALLY",
        fusion=True,
    )
    r_fu_plus = await client.get_price(
        profile, address_obj, asset_id, party_id,
        level_of_cover="PLUS", billing_freq="ANNUALLY",
        fusion=True,
    )
    result.nrma_fusion = _extract_price(r_fu_std, "STANDARD", "ANNUALLY")
    result.nrma_plus_fusion = _extract_price(r_fu_plus, "PLUS", "ANNUALLY")

    # Reset offer to default (no AD/Fusion) before excess loop
    await client.step11_submit_offer(
        profile,
        address_obj,
        asset_id,
        party_id,
        accidental_damage=False,
        fusion=False,
        referer_page="optional-benefits",
    )

    # All excess levels — both NRMA and NRMAPlus at same time
    for excess_val in ALL_EXCESS_LEVELS:
        try:
            r_std = await client.get_price(
                profile, address_obj, asset_id, party_id,
                level_of_cover="STANDARD", billing_freq="ANNUALLY", excess=excess_val,
            )
            std_price = _extract_price(r_std, "STANDARD", "ANNUALLY")
            if std_price:
                result.excess_prices[excess_val] = std_price

            r_plus = await client.get_price(
                profile, address_obj, asset_id, party_id,
                level_of_cover="PLUS", billing_freq="ANNUALLY", excess=excess_val,
            )
            plus_price = _extract_price(r_plus, "PLUS", "ANNUALLY")
            if plus_price:
                result.excess_plus_prices[excess_val] = plus_price

        except Exception as exc:
            log.info(
                "Excess %s unavailable profile=%s: %s",
                excess_val,
                profile.sample_number,
                exc,
            )

    _check_frequency_consistency(result, profile)
    _check_optional_cover_pricing(result, profile)
    _check_excess_sweep_consistency(result, profile)


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
MAIN_FIELDS = [
    "Sample_Number",
    "Substate",
    "DOB_DD",
    "DOB_MM",
    "DOB_YYYY",
    "Postcode",
    "QuoteDate",
    "Timestamp",
    "QuoteLink",
    "QuoteNumber",
    "NRMA_DefaultExcess",
    "NRMA_YearlyPayment",
    "NRMAPlus_DefaultExcess",
    "NRMAPlus_YearlyPayment",
    "Reason_for_quote_failure",
    "Rerun_Small_Bedrooms",
    "Rerun_Large_Bedrooms",
    "Rerun_Adults",
    "Rerun_Children",
]

TOGGLE_FIELDS = (
    [
        "Sample_Number",
        "Substate",
        "DOB_DD",
        "DOB_MM",
        "DOB_YYYY",
        "Postcode",
        "QuoteDate",
        "Timestamp",
        "QuoteLink",
        "QuoteNumber",
        "NRMA_DefaultExcess",
        "NRMA_FortnightlyPayment",
        "NRMA_QuarterlyPayment",
        "NRMA_MonthlyPayment",
        "NRMA_YearlyPayment",
        "NRMA_AccidentalDamage",
        "NRMA_Fusion",
        "NRMAPlus_DefaultExcess",
        "NRMAPlus_FortnightlyPayment",
        "NRMAPlus_QuarterlyPayment",
        "NRMAPlus_MonthlyPayment",
        "NRMAPlus_YearlyPayment",
        "NRMAPlus_AccidentalDamage",
        "NRMAPlus_Fusion",
    ]
    + [f"NRMA_excess_{x}" for x in ALL_EXCESS_LEVELS]
    + [f"NRMAPlus_excess_{x}" for x in ALL_EXCESS_LEVELS]
    + [
        "Reason_for_quote_failure",
        "Rerun_Small_Bedrooms",
        "Rerun_Large_Bedrooms",
        "Rerun_Adults",
        "Rerun_Children",
    ]
)


def to_main_row(r: QuoteResult) -> dict:
    return {
        "Sample_Number": r.sample_number,
        "Substate": r.substate,
        "DOB_DD": r.dob_dd,
        "DOB_MM": r.dob_mm,
        "DOB_YYYY": r.dob_yyyy,
        "Postcode": r.postcode,
        "QuoteDate": r.quote_date,
        "Timestamp": r.timestamp,
        "QuoteLink": r.quote_link,
        "QuoteNumber": r.quote_number,
        "NRMA_DefaultExcess": r.nrma_default_excess,
        "NRMA_YearlyPayment": r.nrma_yearly,
        "NRMAPlus_DefaultExcess": r.nrma_plus_default_excess,
        "NRMAPlus_YearlyPayment": r.nrma_plus_yearly,
        "Reason_for_quote_failure": short_failure_reason(r.failure_reason),
        "Rerun_Small_Bedrooms": r.rerun_small_bedrooms,
        "Rerun_Large_Bedrooms": r.rerun_large_bedrooms,
        "Rerun_Adults": r.rerun_adults,
        "Rerun_Children": r.rerun_children,
    }


def to_toggle_row(r: QuoteResult) -> dict:
    row = {
        "Sample_Number": r.sample_number,
        "Substate": r.substate,
        "DOB_DD": r.dob_dd,
        "DOB_MM": r.dob_mm,
        "DOB_YYYY": r.dob_yyyy,
        "Postcode": r.postcode,
        "QuoteDate": r.quote_date,
        "Timestamp": r.timestamp,
        "QuoteLink": r.quote_link,
        "QuoteNumber": r.quote_number,
        "NRMA_DefaultExcess": r.nrma_default_excess,
        "NRMA_FortnightlyPayment": r.nrma_fortnightly,
        "NRMA_QuarterlyPayment": r.nrma_quarterly,
        "NRMA_MonthlyPayment": r.nrma_monthly,
        "NRMA_YearlyPayment": r.nrma_yearly,
        "NRMA_AccidentalDamage": r.nrma_accidental_damage,
        "NRMA_Fusion": r.nrma_fusion,
        "NRMAPlus_DefaultExcess": r.nrma_plus_default_excess,
        "NRMAPlus_FortnightlyPayment": r.nrma_plus_fortnightly,
        "NRMAPlus_QuarterlyPayment": r.nrma_plus_quarterly,
        "NRMAPlus_MonthlyPayment": r.nrma_plus_monthly,
        "NRMAPlus_YearlyPayment": r.nrma_plus_yearly,
        "NRMAPlus_AccidentalDamage": r.nrma_plus_accidental_damage,
        "NRMAPlus_Fusion": r.nrma_plus_fusion,
        "Reason_for_quote_failure": short_failure_reason(r.failure_reason),
        "Rerun_Small_Bedrooms": r.rerun_small_bedrooms,
        "Rerun_Large_Bedrooms": r.rerun_large_bedrooms,
        "Rerun_Adults": r.rerun_adults,
        "Rerun_Children": r.rerun_children,
    }
    for x in ALL_EXCESS_LEVELS:
        row[f"NRMA_excess_{x}"] = r.excess_prices.get(x, "")
        row[f"NRMAPlus_excess_{x}"] = r.excess_plus_prices.get(x, "")
    return row


# ---------------------------------------------------------------------------
# Idempotency / resume support
# ---------------------------------------------------------------------------
def _row_is_complete(row: dict) -> bool:
    """A row counts as 'properly collected' when the quote succeeded.

    Criteria: no recorded failure reason and the headline NRMA yearly price is
    present. Rows that errored out (address not found, Zyte failures, …) leave
    Reason_for_quote_failure populated and the price fields blank, so they are
    treated as NOT collected and will be re-run.

    Exception: SumInsured out-of-range failures are terminal — adjusting the
    rooms is the best we can do, so they are treated as collected and NOT
    re-run. Only quotes that failed for other reasons are retried.
    """
    reason = (row.get("Reason_for_quote_failure") or "").strip()
    if reason:
        return reason.startswith("SumInsuredOutOfRange") or reason.startswith("No address found:") or reason.startswith("Could not provide estimates for this property")  or reason.startswith("We don't cover properties in your area")
    return bool((row.get("NRMA_YearlyPayment") or "").strip())


def load_completed(path: str) -> tuple[set[str], list[dict]]:
    """Read an existing output CSV and split it for a safe re-run.

    Returns (done_sample_numbers, rows_to_keep) where done_sample_numbers are
    the samples already collected properly and rows_to_keep are the
    corresponding rows (deduped, first complete row wins). Incomplete/failed
    rows are dropped so re-running a sample does not leave a stale duplicate.
    """
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
    def __init__(self, path: str, fields: list[str], preserve_rows: list[dict] | None = None):
        self.path = path
        self.fields = fields
        self._lock = asyncio.Lock()
        # Rewrite the header plus any already-complete rows, then append new
        # results below. This preserves prior good data on a resume while
        # dropping incomplete rows so re-collected samples are not duplicated.
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
BAN_RETRY_ATTEMPTS = int(os.getenv("BAN_RETRY_ATTEMPTS", "3"))

# DynaCop (IAG's rate limiter) throttles quote creation per true-client-ip and
# per x-iag-user-access cookie. A new IP resets the bucket, but shared
# residential pools hand back recently-used IPs, so give it time to decay too.
RATE_LIMIT_BACKOFF = (
    float(os.getenv("RATE_LIMIT_BACKOFF_MIN", "30")),
    float(os.getenv("RATE_LIMIT_BACKOFF_MAX", "75")),
)
# Desynchronise worker starts so concurrent browsers don't all create a quote
# in the same second, which is what trips the per-IP limit in the first place.
START_JITTER_SECONDS = float(os.getenv("START_JITTER_SECONDS", "20"))
# Seconds to wait before each profile. See the throttle note in worker().
PROFILE_DELAY = float(os.getenv("NRMA_PROFILE_DELAY", "0"))

# Refusals that need *time* to clear rather than just a fresh exit IP. 429 and
# DynaCop are an explicit throttle; a 403 bot block earns the same wait.
#
# Making the 403 wait conditional on `_block_streak` looked obviously right — a
# lone 403 clears on retry, so why pay 30-75s? — and measuring it settled the
# argument the other way. Back-to-back 20-min samples, same night, same code
# otherwise:
#
#   always wait   33 launches -> 12 rows   36 rows/h   2.75 launches/row
#   wait if 3+    43 launches -> 10 rows   30 rows/h   4.30 launches/row
#
# The fast hop does not clear a 403: a fresh IP 2s later gets refused again,
# while ~50s of decay actually helps. So the conditional version spent more
# requests to collect fewer rows. Reverted 2026-09-10 — do not re-derive it from
# first principles, the measurement is in Decisions.md.
_RATE_LIMIT_MARKERS = ("→ 429", "DynaCop", "→ 403", "Access Denied")


def _is_rate_limited(result: QuoteResult) -> bool:
    """True when the refusal needs time to decay, not just a new IP."""
    reason = result.failure_reason or ""
    return any(m in reason for m in _RATE_LIMIT_MARKERS)


# Ways NRMA's edge refuses a session rather than answering the question:
#   403 — Akamai Bot Manager, an HTML "Access Denied" body, not a JSON error
#   401 — the anonymous quote token is rejected mid-flow
#   429 — DynaCop rate/WAF layer
# All of them are properties of the session or exit IP, so a fresh browser on a
# fresh IP is worth trying, as is a dead residential exit surfacing as a
# NetworkError out of page.evaluate or a Firefox NS_ERROR_*.
_RETRYABLE_MARKERS = (
    "Access Denied",
    "→ 403",
    "→ 401",
    "→ 429",
    "AkamaiNotValidated",
    "BrowserSessionFailed",
    "NetworkError",
    "NS_ERROR_",
    "has been closed",
    "Connection closed",
    "Timeout",
)


def _should_retry_on_new_session(result: QuoteResult) -> bool:
    """True when the row failed because of the session, not on the merits.

    An out-of-range sum insured or a missing address is a real answer about this
    property and must never be retried — retrying would just burn IPs and could
    overwrite a correct terminal result.
    """
    reason = result.failure_reason or ""
    return bool(reason) and any(m in reason for m in _RETRYABLE_MARKERS)


async def _run_one_session(profile: Profile, mode: str) -> QuoteResult:
    """Run a profile on one fresh Akamai-validated browser session."""
    try:
        async with quote_session(profile.state or "NSW") as session:
            return await run_profile(profile, session, mode=mode)
    except AkamaiNotValidated as exc:
        result = _new_result(profile)
        result.failure_reason = str(exc)[:500]
        log.error("❌ profile=%s %s", profile.sample_number, exc)
        return result
    except Exception as exc:  # browser launch / navigation failure
        result = _new_result(profile)
        result.failure_reason = f"BrowserSessionFailed: {exc}"[:500]
        log.error(
            "❌ profile=%s browser session failed: %s", profile.sample_number, exc
        )
        return result


# Consecutive profiles lost to an Akamai 403.
_block_streak = 0
_halt = asyncio.Event()


def _note_outcome(result: QuoteResult) -> None:
    """Track consecutive Akamai 403s; halt the run once they are a wall.

    Three-way on purpose, and the third way is the whole point. A 403 counts up.
    Only a *real answer* counts down — a price, or a terminal reason about the
    property — because only that proves the wall is down. A dropped residential
    exit (NS_ERROR_*, NetworkError, a bootstrap that never validated) is
    evidence of neither and must leave the streak untouched.

    Resetting on those is what stopped this breaker firing when it first ran
    (2026-09-09 20:08): 20 of 24 give-ups were the 403, but the 4 flaky exits
    scattered among them kept the counter under the limit indefinitely, so a
    total block swept on exactly as before.
    """
    global _block_streak
    reason = result.failure_reason or ""
    if is_bot_block(reason):
        _block_streak += 1
    elif not reason or reason.startswith(TERMINAL_REASON_PREFIXES):
        _block_streak = 0  # a quote got through, or the property was answered
        return
    else:
        return  # transport noise — says nothing about the block either way
    if _block_streak >= BLOCK_STREAK_LIMIT and not _halt.is_set():
        hours = start_cooldown()
        _halt.set()
        log.error(
            "⛔ %d profiles bot-blocked with no quote getting through in "
            "between — halting this run and backing off %.0fmin. Nothing is "
            "lost: every one of them is still pending for the next pass. "
            "Delete %s to resume sooner.",
            _block_streak,
            hours * 60,
            COOLDOWN_FILE,
        )


async def worker(
    profile: Profile,
    writer: CSVWriter,
    mode: str,
    sem: asyncio.Semaphore,
    progress: dict,
):
    async with sem:
        # The breaker tripped (here or in the sibling scraper) — the rest of the
        # sweep would only rediscover the same block. No row written, so the next
        # resume pass still owns these profiles.
        if _halt.is_set() or cooldown_hours_left():
            return

        # Stagger starts so N browsers don't all POST /home/quote in the same
        # second — DynaCop rate-limits quote creation per true-client-ip. At
        # CONCURRENCY=1 the semaphore already serialises us, so there is nothing
        # to desynchronise and the sleep is just ~10s of dead time per profile.
        if CONCURRENCY > 1 and START_JITTER_SECONDS:
            await asyncio.sleep(random.uniform(0, START_JITTER_SECONDS))

        # Deliberate serial throttle, distinct from the jitter above. On a
        # single egress IP (NRMA_PROXY=0) DynaCop's quote-creation bucket is
        # the binding constraint, not Akamai: measured 2026-09-11, 23 launches
        # in 25 min from one address returned 41x 429 and zero rows. Pacing
        # trades launches for rows and is the only lever that helps there.
        # Pointless when every quote gets its own exit IP, so it defaults off.
        if PROFILE_DELAY:
            await asyncio.sleep(PROFILE_DELAY)

        # One validated browser session per profile — Akamai flags the exit IP
        # after roughly one completed quote, so sessions are never reused. A ban
        # means the IP is burnt, so the retry takes a whole new browser and IP.
        for attempt in range(1, BAN_RETRY_ATTEMPTS + 1):
            result = await _run_one_session(profile, mode)
            if not _should_retry_on_new_session(result):
                break
            if attempt < BAN_RETRY_ATTEMPTS:
                # A rate limit needs time to decay; a ban just needs a new IP.
                delay = (
                    random.uniform(*RATE_LIMIT_BACKOFF)
                    if _is_rate_limited(result)
                    else random.uniform(1, 4)
                )
                log.warning(
                    "⚠ profile=%s session refused on attempt %d/%d — "
                    "waiting %.0fs then retrying on a fresh IP | %s",
                    profile.sample_number,
                    attempt,
                    BAN_RETRY_ATTEMPTS,
                    delay,
                    result.failure_reason[:100],
                )
                await asyncio.sleep(delay)
        refused = _should_retry_on_new_session(result)
        if refused:
            log.error(
                "❌ profile=%s gave up after %d attempts — left for the next "
                "resume pass | %s",
                profile.sample_number,
                BAN_RETRY_ATTEMPTS,
                result.failure_reason[:120],
            )
        _note_outcome(result)
        await writer.write(
            to_main_row(result) if mode == "main" else to_toggle_row(result)
        )
        progress["done"] += 1
        log.info(
            "Progress [%s]: %d/%d (%.1f%%)",
            mode,
            progress["done"],
            progress["total"],
            100 * progress["done"] / progress["total"],
        )


async def main():
    log.info("=== NRMA Contents Scraper | CONCURRENCY=%d ===", CONCURRENCY)

    # An Akamai block is account-wide and only clears with idle time, so a run
    # started during the cooldown would just re-burn it. Exiting here is also
    # what makes the breaker stick against the 2-hourly cron relaunch.
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
            "no Contents input for period %s (%s) — drop the CSV into "
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
    log.info(
        "MAIN resume: %d already collected, %d pending of %d total",
        len(done),
        len(pending),
        len(main_profiles),
    )

    # Rewrite output with prior good rows preserved; new results append.
    main_writer = CSVWriter(OUTPUT_MAIN_CSV, MAIN_FIELDS, preserve_rows=keep)

    if pending:
        prog_main = {"done": 0, "total": len(pending)}
        await asyncio.gather(*[
            worker(p, main_writer, "main", sem, prog_main)
            for p in pending
        ])
    log.info("MAIN done → %s", OUTPUT_MAIN_CSV)

    # Toggle files ship only with a quarterly drop. Nothing to do on a monthly
    # run — and silently reusing the previous period's file is worse than
    # nothing, because its output gets rewritten by every resume pass.
    if not INPUT_TOGGLE_CSV or not os.path.exists(INPUT_TOGGLE_CSV):
        log.info("No Contents toggle input for %s — skipping the toggle stage",
                 period.PERIOD or "?")
        return

    toggle_profiles = load_profiles(INPUT_TOGGLE_CSV)

    # ── Idempotent resume: skip samples already collected properly ──────
    if FORCE_RERUN:
        done, keep = set(), []
        log.info("FORCE_RERUN set — ignoring existing output, scraping all")
    else:
        done, keep = load_completed(OUTPUT_TOGGLE_CSV)
    pending = [p for p in toggle_profiles if p.sample_number not in done]
    log.info(
        "Toggle resume: %d already collected, %d pending of %d total",
        len(done),
        len(pending),
        len(toggle_profiles),
    )

    # Rewrite output with prior good rows preserved; new results append.
    main_writer = CSVWriter(OUTPUT_TOGGLE_CSV, TOGGLE_FIELDS, preserve_rows=keep)

    if pending:
        prog_main = {"done": 0, "total": len(pending)}
        await asyncio.gather(*[
            worker(p, main_writer, "toggle", sem, prog_main)
            for p in pending
        ])
    log.info("Toggle done → %s", OUTPUT_TOGGLE_CSV)


if __name__ == "__main__":
    asyncio.run(main())
