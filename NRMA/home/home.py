"""
NRMA Home Insurance — Unified Scraper
======================================
Single class handling all 3 products:
  1. Contents Insurance       → coverType: CONTENTS
  2. Home Insurance (Buildings) → coverType: HOME
  3. Home and Contents Insurance → coverType: HOME_AND_CONTENTS

Product routing, step branching, toggle support, and cyclone handling
are all driven by the Cover column in the input CSV.

Usage:
    run_all_quotes("input.csv", "output.csv", toggle=False)
"""

from datetime import date, datetime

try:  # package import (python -m NRMA.home.home)
    from .nrma_browser import cover_start_date
except ImportError:  # direct script run
    from nrma_browser import cover_start_date
import logging
import time
import traceback
import uuid

import pandas as pd
import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(levelname)-7s │ %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("NrmaHomeUnified")

# ═════════════════════════════════════════════════════════════════════
# Module-level: SI retry state persistence
# ═════════════════════════════════════════════════════════════════════

import json
from pathlib import Path

SI_RETRY_STATE_DIR = Path("/home/wmc/WMC/DataManipulation/Suncorp/NRMA")
SI_RETRY_STATE_FILE = SI_RETRY_STATE_DIR / "si_retry_state.json"

# Ensure directory exists at import time
SI_RETRY_STATE_DIR.mkdir(parents=True, exist_ok=True)


def _load_si_retry_state() -> dict:
    try:
        if SI_RETRY_STATE_FILE.exists():
            with open(SI_RETRY_STATE_FILE, "r") as f:
                data = json.load(f)
                log.debug(f"  SI state loaded: {len(data)} entries")
                return data
    except Exception as e:
        log.warning(f"Could not load SI retry state: {e}")
    return {}


def _save_si_retry_state(state: dict):
    try:
        SI_RETRY_STATE_DIR.mkdir(parents=True, exist_ok=True)
        with open(SI_RETRY_STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
        log.debug(f"  SI state saved: {len(state)} entries → {SI_RETRY_STATE_FILE}")
    except Exception as e:
        log.warning(f"Could not save SI retry state: {e}")


def _record_si_adjustment(
    sample_number: str,
    phase: int,  # 2 or 3
    room_overrides: dict | None,
    outdoor_overrides: dict | None,
    occupant_overrides: dict | None,
    original_error: str = "",
    retry1_error: str = "",
    retry2_error: str = "",
):
    """Record SI adjustments with phase tracking and error history."""
    state = _load_si_retry_state()
    state[str(sample_number)] = {
        "phase": phase,
        "room_overrides": room_overrides,
        "outdoor_overrides": outdoor_overrides,
        "occupant_overrides": occupant_overrides,
        "original_error": original_error,
        "retry1_error": retry1_error,
        "retry2_error": retry2_error,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    _save_si_retry_state(state)
    log.info(
        f"  📝 SI adjustment stored: Sample {sample_number}, "
        f"Phase {phase}"
    )


def _get_si_adjustment(sample_number: str) -> dict | None:
    state = _load_si_retry_state()
    result = state.get(str(sample_number))
    if result:
        log.info(f"  📋 SI adjustment found for Sample {sample_number}")
    return result


def _clear_si_adjustment(sample_number: str):
    state = _load_si_retry_state()
    if str(sample_number) in state:
        del state[str(sample_number)]
        _save_si_retry_state(state)
        log.info(f"  🗑️ SI adjustment cleared for Sample {sample_number}")

class SumInsuredOutOfRange(ValueError):
    """Raised when target SI is outside the API's accepted range."""

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

class NrmaHomeUnifiedExtractor:
    """
    Unified NRMA Home Insurance scraper.
    Handles Contents, Buildings, and Combined products from a single class.
    """
    MAX_LARGE_BEDROOM_ADDITIONS = 10
    # ── SI Adjustment Constants (verified from client) ───────────────
    # Average SI upper bound increase per large bedroom added
    SI_PER_LARGE_BEDROOM_BUILDING = 300_000
    SI_PER_LARGE_BEDROOM_CONTENTS = 13_000
    BASE_URL = "https://insurance.nrma.com.au"
    API_V1 = "/domain/question/api/question/api/1.x.x"

    # ── Product Configuration ────────────────────────────────────────────

    PRODUCT_CONFIG = {
        "Contents Insurance": {
            "serenityProduct": "home-buildings-and-contents-standard",
            "coverType": "CONTENTS",
            "hasBuildings": False,
            "hasContents": True,
            "hasTailorContents": True,
            "hasToggle": True,
            "excessKey": "contents",        # coverOptions.STANDARD.excesses.{key}
            "excessField": "ContentsExcess", # Refresh payload field
            "extrasTarget": "Contents",      # OptionalExtras target
            "excessOptionsPath": "Contents", # excessOptions.{path}.BaseExcess
        },
        "Home Insurance": {
            "serenityProduct": "home-buildings-and-contents-standard",
            "coverType": "HOME",
            "hasBuildings": True,
            "hasContents": False,
            "hasTailorContents": False,
            "hasToggle": True,
            "excessKey": "building",
            "excessField": "BuildingExcess",
            "extrasTarget": "Home",
            "excessOptionsPath": "Building",
        },
        "Home and Contents Insurance": {
            "serenityProduct": "home-buildings-and-contents-standard",
            "coverType": "HOME_AND_CONTENTS",
            "hasBuildings": True,
            "hasContents": True,
            "hasTailorContents": True,
            "hasToggle": False,  # Combined = main only, no toggle [7]
            "excessKey": None,   # Combined uses dual excesses
            "excessField": None,
            "extrasTarget": None,
            "excessOptionsPath": None,
        },
    }

    # ── Enum Mappings (verified from API) ────────────────────────────────

    PROPERTY_TYPE_MAPPING = {
        "Free standing house": "FREESTANDING_HOME",
        "Apartment or unit": "APARTMENT",
        "Townhouse, terrace, duplex": "TOWNHOUSE_TERRACE_HOUSE_OR_DUPLEX",
        "Townhouse": "TOWNHOUSE_TERRACE_HOUSE_OR_DUPLEX",
        "Semi-detached": "TOWNHOUSE_TERRACE_HOUSE_OR_DUPLEX",
        "Relocatable or tiny home": "RELOCATABLE_OR_TINY_HOME",
        "Home on a farm": "HOME_ON_A_FARM",
        "Hobby farm": "HOME_HOBBY_FARM_OR_LIFESTYLE_BLOCK",
        "Unit in retirement village": "RETIREMENT_VILLAGE_UNIT",
    }

    ALARM_MAPPING = {
        "No alarm": "NO_ALARM",
        "Local alarm": "LOCAL_ALARM",
        "Monitored by an app": "MONITORED_ALARM_MOBILE_PHONE_APP",
        "Monitored by a company": "MONITORED_ALARM_SECURITY_COMPANY",
    }

    WALL_MATERIAL_MAPPING = {
        "Double brick": "DOUBLE_BRICK",
        "Brick veneer": "BRICK_VENEER",
        "Blockwork": "BLOCKWORK",
        "Concrete block": "BLOCKWORK",
        "Rendered masonry": "RENDERED_MASONRY",
        "Sheet cladding": "SHEET_CLADDING",
        "AAC panel": "AAC_PANEL",
        "Panel wall system": "PANEL_WALL_SYSTEM",
        "Stonework solid": "STONEWORK",
        "Stonework veneer": "STONEWORK_VENEER",
        "Weatherboard - timber": "WEATHERBOARD_TIMBER",
        "Weatherboard - artificial": "WEATHERBOARD_ARTIFICIAL",
        "Fibro or weatherboard": "WEATHERBOARD_TIMBER",
        "Weatherboard": "WEATHERBOARD_TIMBER",
    }

    ROOF_MATERIAL_MAPPING = {
        "Metal covering": "METAL_COVERING",
        "Concrete tile": "CONCRETE_TILE",
        "Conrete tile": "CONCRETE_TILE",
        "Terracotta": "TERRACOTTA",
        "Slate": "SLATE",
        "Fibre cement covering": "FIBRECEMENT_COVERING",
        "Timber shingles": "TIMBER_SHINGLES",
    }

    LAND_SLOPE_MAPPING = {
        "Flat": "FLAT_GENTLE",
        "Flat or gentle": "FLAT_GENTLE",
        "Moderate": "MODERATE",
        "Severe": "SEVERE",
        "Steep": "SEVERE",
    }

    QUALITY_OF_FIXTURES_MAPPING = {
        "Standard": "STANDARD",
        "Quality": "QUALITY",
        "Prestige": "PRESTIGE",
    }

    LIVE_WITH_MAPPING = {
        "Myself or family": "MYSELF_OR_FAMILY",
        "Flatmates": "FLATMATES",
    }

    HOME_CLAIM_TYPE_MAPPING = {
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
        "Lost item(s)": "LOST_ITEM",
        "Storm or lightning": "STORM_OR_LIGHTNING",
        "Storm surge": "STORM_SURGE",
        "Storm water run-off": "STORM_RUN_OFF",
        "Theft or burglary": "THEFT_OR_BURGLARY",
    }

    CYCLONE_FIELD_MAP = {
        "IsRoofReplaced": "Roof_Replacement",
        "IsExternalWindowsProtected": "Window_Protection",
        "IsGarageUpgraded": "Garage_Door_Upgrade",
        "IsExternalStructuresProtected": "Shed_Tied_down",
    }

    BRAND_CONFIG = {
        "brand": "NRMA",
        "features": {
            "includeEmailConsent": True,
            "enableEmailQuoteClaimsHistory": True,
            "isHiddenBuildingQuestionBefore1935": True,
            "yearConstructedValidationMaxYear": 70,
            "enablePaymentMfeChannelValueUpdate": True,
            "hideFortnightlyPayment_from_quote_creation_date": "2025-11-16T00:00:00.000Z",
        },
        "product": "home",
        "isLocal": False,
        "brandCode": "nrma",
        "version": "2026-03-16T14:43:44-b1e1a6951/latest",
        "config": {
            "env": "prdnoauth",
            "REACT_APP_ENABLED_WEBCHAT_BRANDS": "nrma",
            "REACT_APP_IS_WEBCHAT_ENABLED": "true",
            "REACT_APP_IS_IDENTITY_MFE_ENABLED": "true",
            "REACT_APP_API_BASE_PATH": "/domain/question/api/question/api/1.x.x",
            "REACT_APP_SITE_TRACKING_MODULE_NAME": "iag-common-quote-and-buy-mfe",
            "REACT_APP_SITE_TRACKING_APP_NAME": "quote-and-buy",
            "REACT_APP_SITE_TRACKING_ENVIRONMENT": "qa",
            "REACT_APP_WARN_BEFORE_EXPIRY_TIME_IN_MILLISECONDS": 300000,
            "REACT_APP_IS_PAYMENT_MOCKED": "false",
            "REACT_APP_NEW_RELIC_API_KEY": "367fd0c72b5a2d7870af22983ab44d9fFFFFNRAL",
            "REACT_APP_NEW_RELIC_ENTITY_GUID": "MzQ5Nzc2MHxCUk9XU0VSfEFQUExJQ0FUSU9OfDExMzQyNjc3MTg",
            "DYNAMIC_FORM_MFE_URL": "/domain/utility/mfe/dynamic-form-mfe/latest",
            "IDENTITY_MFE_URL": "/domain/identity/mfe/identity-mfe/3.x.x",
            "CUSTOMER_CONFIRMATION_MFE_URL": "/domain/customer/mfe/customer-comms-mfe/latest",
            "PUBLIC_URL": "/domain/buy/mfe/single-product-quote-and-buy-mfe/latest/",
            "REWARDS_MFE_URL": "/domain/identity/mfe/quick-loyalty-registration/public",
            "REACT_APP_ONLINE_ACCOUNT_URL": "https://onlineaccount.nrma.com.au",
            "REACT_APP_ENABLED_IDENTITY_BRANDS": "asb,anz,bnz,nrma,state,ami,qcb,move,horizon,qbk,scu,cap,tmb,bendigo,dfb,amb,msb,bou,bft,aant,pnb,bcu,ccu,pfb,thh,cgu,co-op,htb,onechoice,seniors",
            "REACT_APP_FEATURE_FLAG_ENABLE_BROWSER_BACK_OVERRIDE": "asb,anz,bnz,nrma,state,ami,qcb,move,horizon,qbk,scu,cap,tmb,bendigo,dfb,amb,msb,bou,bft,aant,pnb,bcu,ccu,pfb,thh,cgu,co-op,htb,onechoice,seniors",
            "REACT_APP_NRMA_REFRESH_ACCESS_TOKEN_HOST": "https://connect.nrma.com.au",
        },
        "pages": [],
        "contentOverrides": {},
        "division": "dia",
        "brandVariables": {
            "CONTACT_NUMBER": "132 132",
            "HOURS_VIA_WEBSITE": "8am\u20136pm Mon\u2013Fri, 9am\u20136pm Sat-Sun & public holidays",
            "URL": "https://www.nrma.com.au",
            "ONLINE_ACCOUNT_URL": "https://onlineaccount.nrma.com.au",
        },
    }

    RETRY_MAX_ATTEMPTS = 4
    RETRY_BACKOFF_BASE = 3
    RETRY_ON_STATUS = {429, 502, 503, 504}
    SESSION_REFRESH_ON_STATUS = {403, 500}
    MAX_SESSION_REFRESHES = 2

    ZYTE_API_URL = "https://api.zyte.com/v1/extract"
    ZYTE_API_KEY = "d5f27f9a318349a599774d39b68f0552"

    # ── Constructor ──────────────────────────────────────────────────────

    def __init__(self, input_data: dict, toggle: bool = False) -> None:
        self.input_data = input_data
        self.toggle = toggle

        # ── Resolve product config from CSV Cover column ──
        cover_raw = self._str("Cover", "Contents Insurance")
        self.product = None
        self.product_name = cover_raw
        for key, cfg in self.PRODUCT_CONFIG.items():
            if key.lower() == cover_raw.lower() or cover_raw.lower() in key.lower():
                self.product = cfg
                self.product_name = key
                break
        if not self.product:
            if "content" in cover_raw.lower() and "home" in cover_raw.lower():
                self.product = self.PRODUCT_CONFIG["Home and Contents Insurance"]
                self.product_name = "Home and Contents Insurance"
            elif "content" in cover_raw.lower():
                self.product = self.PRODUCT_CONFIG["Contents Insurance"]
                self.product_name = "Contents Insurance"
            else:
                self.product = self.PRODUCT_CONFIG["Home Insurance"]
                self.product_name = "Home Insurance"

        self.has_buildings = self.product["hasBuildings"]
        self.has_contents = self.product["hasContents"]
        self.is_combined = self.has_buildings and self.has_contents
        self.is_contents_only = self.has_contents and not self.has_buildings
        self.is_buildings_only = self.has_buildings and not self.has_contents

        # Override toggle for Combined (main only per spec [7])
        if self.is_combined:
            self.toggle = False

        # ── State ──
        self.quote_id = None
        self.access_token = None
        self.session_access_token = None
        self.matched_address = None
        self.party_id = None
        self.default_excess = 750 if self.is_contents_only else 1100
        self.pricing_response = None
        self.last_response = None
        self.cookies = {}
        self._request_count = 0
        self._total_request_time = 0.0

        # ── SI retry overrides ──
        self.room_overrides = None
        self.outdoor_overrides = None
        self.occupant_overrides = None

        # ── Request resilience ──
        self._cookie_refresh_count = 0
        self._proxy_mode = False
        self._proxy_escalation_count = 0

        state = self._str("State", "NSW")
        serenity = self.product["serenityProduct"]
        self.BRAND_CONFIG["initial-url"] = (
            f"https://insurance.nrma.com.au/get-cover/home"
            f"?serenityproduct={serenity}"
            f"&serenitydefaultstate={state}"
            f"&serenityselectedstate={state}"
        )

        # ── Build output field order ──
        self.fields_order = self._build_fields_order()
        self.results: dict[str, object] = {k: "" for k in self.fields_order}
        self.results.update({
            "Sample_Number": input_data.get("Sample_Number"),
            "DOB_DD": input_data.get("Owner_DOB_DD_Dummy"),
            "DOB_MM": input_data.get("Owner_DOB_MM_Dummy"),
            "DOB_YYYY": input_data.get("Owner_DOB_YYYY_Dummy"),
            "Postcode": input_data.get("Postcode"),
            "QuoteDate": time.strftime("%d-%b-%y"),
            "Timestamp (in hh:mm)": time.strftime("%H:%M"),
        })

        sample = input_data.get("Sample_Number", "?")
        log.info(f"{'=' * 80}")
        log.info(
            f"INIT — Sample {sample} | {self.product_name} | "
            f"Toggle={'ON' if self.toggle else 'OFF'} | "
            f"{input_data.get('Address')} {input_data.get('Postcode')}"
        )

    # ── Output field builders ────────────────────────────────────────────

    def _build_fields_order(self) -> list[str]:
        if self.is_combined:
            return self._fields_combined()
        elif self.toggle:
            return self._fields_toggle()
        else:
            return self._fields_main()

    # ═════════════════════════════════════════════════════════════════
    # Update all three field order builders — add rerun columns
    # ═════════════════════════════════════════════════════════════════

    def _fields_main(self) -> list[str]:
        return [
            "Sample_Number", "DOB_DD", "DOB_MM", "DOB_YYYY", "Postcode",
            "QuoteDate", "Timestamp (in hh:mm)", "QuoteLink", "QuoteNumber",
            "NRMA_DefaultExcess", "NRMA_YearlyPayment",
            "NRMAPlus_DefaultExcess", "NRMAPlus_YearlyPayment",
            "Rerun_Small_Bedrooms", "Rerun_Large_Bedrooms",
            "Rerun_Carport", "Rerun_Adults", "Rerun_Children",
            "Reason_for_quote_failure",
        ]

    def _fields_toggle(self) -> list[str]:
        cols = [
            "Sample_Number", "DOB_DD", "DOB_MM", "DOB_YYYY", "Postcode",
            "QuoteDate", "Timestamp (in hh:mm)", "QuoteLink", "QuoteNumber",
            "NRMA_DefaultExcess", "NRMA_FortnightlyPayment",
            "NRMA_QuarterlyPayment", "NRMA_MonthlyPayment",
            "NRMA_YearlyPayment", "NRMA_AccidentalDamage", "NRMA_Fusion",
        ]
        for i in range(0, 2250, 50):
            cols.append(f"NRMA_excess_{i}")
        cols.extend([
            "NRMAPlus_DefaultExcess", "NRMAPlus_FortnightlyPayment",
            "NRMAPlus_QuarterlyPayment", "NRMAPlus_MonthlyPayment",
            "NRMAPlus_YearlyPayment", "NRMAPlus_AccidentalDamage",
            "NRMAPlus_Fusion",
        ])
        for i in range(0, 2250, 50):
            cols.append(f"NRMAPlus_excess_{i}")
        cols.extend([
            "Rerun_Small_Bedrooms", "Rerun_Large_Bedrooms",
            "Rerun_Carport", "Rerun_Adults", "Rerun_Children",
            "Reason_for_quote_failure",
        ])
        return cols

    def _fields_combined(self) -> list[str]:
        return [
            "Sample_Number", "DOB_DD", "DOB_MM", "DOB_YYYY", "Postcode",
            "QuoteDate", "Timestamp (in hh:mm)", "QuoteLink", "QuoteNumber",
            "NRMA_DefaultExcess_Building", "NRMA_DefaultExcess_Content",
            "NRMA_YearlyPayment",
            "NRMAPlus_DefaultExcess_Building", "NRMAPlus_DefaultExcess_Content",
            "NRMAPlus_YearlyPayment",
            "NRMA_Base_Prem", "NRMA_GST", "NRMA_Stamp_Duty", "NRMA_ESL",
            "Rerun_Small_Bedrooms", "Rerun_Large_Bedrooms",
            "Rerun_Carport", "Rerun_Adults", "Rerun_Children",
            "Reason_for_quote_failure",
        ]
    
    # ── Input helpers (CSV column names) ─────────────────────────────────

    def _bool(self, key, default="No"):
        return self.input_data.get(key, default) == "Yes"

    def _int(self, key, default=0):
        val = self.input_data.get(key, default)
        try:
            return (
                int(float(val))
                if val and str(val) not in ("", "nan", "None")
                else default
            )
        except (ValueError, TypeError):
            return default

    def _str(self, key, default=""):
        val = self.input_data.get(key, default)
        return str(val) if val and str(val) not in ("nan", "None") else default

    def _parse_gst_credit(self) -> int:
        raw = self._str("GST_Credit", "0")
        return int(float(raw.replace("%", "").strip() or "0"))

    # ── Headers & Cookies (one-time Zyte) ────────────────────────────────

    @staticmethod
    def _base_headers():
        return {
            "accept": "application/json, text/plain, */*",
            "accept-language": "en-US,en;q=0.9",
            "cache-control": "no-cache",
            "content-type": "application/json",
            "origin": "https://insurance.nrma.com.au",
            "pragma": "no-cache",
            "priority": "u=1, i",
            "sec-ch-ua": '"Not)A;Brand";v="8", "Chromium";v="138", "Google Chrome";v="138"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Linux"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "user-agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
        }


    def _build_cookies(self):
        """Fetch browser cookies via Zyte — called on init + 403 refresh."""
        if not self.product:
            log.warning("│  No product config available to build cookies")
            return {}
        try:
            serenity = self.product["serenityProduct"]
            resp = requests.post(
                "https://api.zyte.com/v1/extract",
                auth=("d5f27f9a318349a599774d39b68f0552", ""),
                json={
                    "url": (
                        f"https://insurance.nrma.com.au/get-cover/home"
                        f"?serenityproduct={serenity}"
                        f"&serenitydefaultstate=NSW"
                        f"&serenityselectedstate=NSW"
                    ),
                    "browserHtml": True,
                    "responseCookies": True,
                },
                timeout=120,
            )
            if resp.status_code == 200:
                log.info("│  Cookies built via Zyte")
            else:
                log.warning(f"│  Zyte returned {resp.status_code}")
                return {}
            cookies = {}
            for item in resp.json().get("responseCookies", []):
                name, value = item.get("name"), item.get("value")
                if name and value:
                    cookies[name] = value
            return cookies
        except Exception as e:
            log.warning(f"│  Zyte cookie fetch failed: {e}")
            return {}

    def _refresh_session(self):
        """Get fresh browser cookies from Zyte."""
        self._cookie_refresh_count += 1
        log.warning(
            f"  🔄 Session refresh #{self._cookie_refresh_count}"
        )
        new_cookies = self._build_cookies()
        if new_cookies:
            self.cookies.update(new_cookies)
        if self.access_token:
            self.cookies["x-iag-customer"] = self.access_token
        if self.session_access_token:
            self.cookies["x-iag-auth-session"] = self.session_access_token
        time.sleep(8)

    def _refresh_cookies(self):
        """
        Rebuild cookies via Zyte when we get 403 (bot detection).
        Re-injects auth tokens into the new cookie jar.
        """
        self._cookie_refresh_count += 1
        log.warning(
            f"  🔄 Cookie refresh #{self._cookie_refresh_count} "
            f"(403 bot detection)"
        )
        self.cookies = self._build_cookies()
        if self.access_token:
            self.cookies["x-iag-customer"] = self.access_token
        if self.session_access_token:
            self.cookies["x-iag-auth-session"] = self.session_access_token
        # Small delay after refresh to avoid immediate re-flag
        time.sleep(5)


    def _request(self, url, method, payload, headers=None, cookies=None):
        hdrs = headers or self._home_headers()
        ckies = cookies if cookies is not None else self._full_cookies()
        self._request_count += 1
        req_num = self._request_count
        short = (
            url.split("/api/")[-1] if "/api/" in url
            else url.split(".au/")[-1]
        )

        for attempt in range(1, self.RETRY_MAX_ATTEMPTS + 1):
            start = time.time()
            try:
                # ── Choose request method based on mode ──
                if self._proxy_mode:
                    resp = self._zyte_proxy_request(url, method, payload, hdrs)
                else:
                    resp = requests.request(
                        method=method, url=url, headers=hdrs,
                        cookies=ckies, json=payload, timeout=60,
                    )

                self._total_request_time += time.time() - start

                # Capture response cookies (direct mode only)
                if not self._proxy_mode:
                    for cn, cv in resp.cookies.items():
                        self.cookies[cn] = cv
                    new_sess = resp.cookies.get("x-iag-auth-session")
                    if new_sess:
                        self.session_access_token = new_sess

                # ── 403/500: Escalation ladder ──
                if resp.status_code in self.SESSION_REFRESH_ON_STATUS:

                    # Tier 2: Session refresh (first attempt)
                    if (
                        not self._proxy_mode
                        and self._cookie_refresh_count < self.MAX_SESSION_REFRESHES
                    ):
                        log.warning(
                            f"  [REQ #{req_num}] HTTP {resp.status_code} "
                            f"— Tier 2: session refresh "
                            f"({attempt}/{self.RETRY_MAX_ATTEMPTS}) "
                            f"— {short}"
                        )
                        self._refresh_session()
                        ckies = self._full_cookies()
                        hdrs = self._home_headers()
                        continue

                    # Tier 3: Escalate to Zyte proxy
                    if not self._proxy_mode:
                        self._proxy_mode = True
                        self._proxy_escalation_count += 1
                        log.warning(
                            f"  [REQ #{req_num}] HTTP {resp.status_code} "
                            f"— Tier 3: escalating to Zyte proxy "
                            f"(escalation #{self._proxy_escalation_count}) "
                            f"— {short}"
                        )
                        time.sleep(5)
                        continue

                    # Already in proxy mode — backoff
                    wait = self.RETRY_BACKOFF_BASE ** attempt
                    log.warning(
                        f"  [REQ #{req_num}] HTTP {resp.status_code} "
                        f"in proxy mode — backoff {wait}s — {short}"
                    )
                    if attempt < self.RETRY_MAX_ATTEMPTS:
                        time.sleep(wait)
                        continue
                    resp.raise_for_status()

                # ── 429/502/503/504: Normal backoff ──
                if resp.status_code in self.RETRY_ON_STATUS:
                    wait = self.RETRY_BACKOFF_BASE ** attempt
                    log.warning(
                        f"  [REQ #{req_num}] HTTP {resp.status_code} "
                        f"({attempt}/{self.RETRY_MAX_ATTEMPTS}) "
                        f"— retry in {wait}s — {short}"
                    )
                    if attempt < self.RETRY_MAX_ATTEMPTS:
                        time.sleep(wait)
                        continue
                    resp.raise_for_status()

                # ── Other 4xx errors ──
                if resp.status_code >= 400:
                    log.warning(
                        f"  [REQ #{req_num}] HTTP {resp.status_code} "
                        f"— {short}"
                    )
                    log.warning(
                        f"  [REQ #{req_num}] {resp.text[:500]}"
                    )

                resp.raise_for_status()

                # ── Success in proxy mode — stay in proxy ──
                if self._proxy_mode:
                    log.debug(
                        f"  [REQ #{req_num}] ✓ via proxy — {short}"
                    )

                return resp.json()

            except (
                requests.exceptions.ConnectionError,
                requests.exceptions.Timeout,
            ) as e:
                wait = self.RETRY_BACKOFF_BASE ** attempt
                log.warning(
                    f"  [REQ #{req_num}] {type(e).__name__} "
                    f"({attempt}/{self.RETRY_MAX_ATTEMPTS}) "
                    f"— retry in {wait}s"
                )
                if attempt < self.RETRY_MAX_ATTEMPTS:
                    # Also try proxy on connection errors
                    if attempt >= 2 and not self._proxy_mode:
                        self._proxy_mode = True
                        self._proxy_escalation_count += 1
                        log.warning(
                            f"  [REQ #{req_num}] Escalating to proxy "
                            f"after connection error"
                        )
                    time.sleep(wait)
                    continue
                raise
            except requests.exceptions.HTTPError:
                raise
            except Exception as e:
                log.error(
                    f"  [REQ #{req_num}] {type(e).__name__}: {e}"
                )
                raise

        raise RuntimeError(
            f"Request failed after {self.RETRY_MAX_ATTEMPTS} attempts"
        )

    def _zyte_proxy_request(self, url, method, payload, headers):
        """
        Route request through Zyte httpResponseBody API.
        """
        import base64
        import json as json_mod

        log.info(f"  [PROXY] {method} {url.split('/')[-1]}")

        # Build cookie header
        cookie_str = self._cookies_to_string()

        # Build custom headers list
        custom_headers = []
        for k, v in headers.items():
            if k.lower() not in (
                "content-length", "host", "connection",
                "accept-encoding", "cookie"
            ):
                custom_headers.append({"name": k, "value": v})

        # Add cookies
        if cookie_str:
            custom_headers.append(
                {"name": "Cookie", "value": cookie_str}
            )

        # Zyte payload — use customHttpRequestHeaders (NOT httpRequestHeaders)
        zyte_payload = {
            "url": url,
            "httpResponseBody": True,
            "httpResponseHeaders": True,
            "httpRequestMethod": method,
            "customHttpRequestHeaders": custom_headers,  # ← FIXED
        }

        # Add request body
        if payload:
            body_bytes = json_mod.dumps(payload).encode("utf-8")
            zyte_payload["httpRequestBody"] = base64.b64encode(
                body_bytes
            ).decode("ascii")

        # Make Zyte API call
        try:
            zyte_resp = requests.post(
                self.ZYTE_API_URL,
                auth=(self.ZYTE_API_KEY, ""),
                json=zyte_payload,
                timeout=120,
            )
        except Exception as e:
            log.error(f"  [PROXY] Zyte API call failed: {e}")
            return self._make_response(502, "{}")

        if zyte_resp.status_code != 200:
            log.error(
                f"  [PROXY] Zyte API error: {zyte_resp.status_code} "
                f"— {zyte_resp.text[:300]}"
            )
            return self._make_response(502, "{}")

        zyte_data = zyte_resp.json()

        # Decode response body
        body_b64 = zyte_data.get("httpResponseBody", "")
        try:
            body_text = (
                base64.b64decode(body_b64).decode("utf-8")
                if body_b64 else "{}"
            )
        except Exception:
            body_text = "{}"

        status_code = zyte_data.get("statusCode", 200)

        # Parse response headers
        resp_headers = {}
        for h in zyte_data.get("httpResponseHeaders", []):
            resp_headers[h["name"].lower()] = h["value"]

        # Build response object
        response = self._make_response(
            status_code, body_text, resp_headers
        )

        # Extract cookies from Set-Cookie headers
        for h in zyte_data.get("httpResponseHeaders", []):
            if h["name"].lower() == "set-cookie":
                self._parse_set_cookie(h["value"], response)

        log.info(
            f"  [PROXY] ← {status_code} "
            f"({len(body_text)} bytes)"
        )

        return response

    def _cookies_to_string(self) -> str:
        """Convert cookie dict to Cookie header string."""
        return "; ".join(
            f"{k}={v}" for k, v in self.cookies.items()
            if k and v
        )

    def _make_response(self, status_code, body_text, headers=None):
        """Create a requests.Response-like object."""
        resp = requests.models.Response()
        resp.status_code = status_code
        resp._content = body_text.encode("utf-8")
        resp.headers = requests.structures.CaseInsensitiveDict(
            headers or {}
        )
        resp.cookies = requests.cookies.RequestsCookieJar()
        resp.encoding = "utf-8"
        return resp

    def _fake_response(self, status_code, text, headers=None):
        """Create a requests.Response-like object from Zyte proxy data."""
        resp = requests.models.Response()
        resp.status_code = status_code
        resp._content = text.encode("utf-8")
        resp.headers = headers or {}
        resp.cookies = requests.cookies.RequestsCookieJar()

        # Parse Set-Cookie headers if present
        if headers:
            for key, val in headers.items():
                if key.lower() == "set-cookie":
                    try:
                        parts = val.split(";")[0]
                        if "=" in parts:
                            cname, cval = parts.split("=", 1)
                            resp.cookies.set(cname.strip(), cval.strip())
                    except Exception:
                        pass
        return resp

    def _parse_set_cookie(self, set_cookie_value, response):
        """Parse Set-Cookie header and add to response + self.cookies."""
        try:
            # Parse "name=value; path=/; ..."
            parts = set_cookie_value.split(";")
            if parts and "=" in parts[0]:
                name, value = parts[0].split("=", 1)
                name = name.strip()
                value = value.strip()
                if name and value:
                    response.cookies.set(name, value)
                    self.cookies[name] = value

                    # Track auth session specifically
                    if name == "x-iag-auth-session":
                        self.session_access_token = value
                        log.debug(
                            f"  [PROXY] Auth session captured"
                        )
        except Exception:
            pass

    def _home_headers(self):
        h = self._base_headers()
        h["referer"] = "https://insurance.nrma.com.au/get-cover/home/"
        h["idempotency-key"] = f"{uuid.uuid4()}-{uuid.uuid4()}"
        return h

    def _token_headers(self):
        h = self._base_headers()
        h["referer"] = "https://insurance.nrma.com.au/"
        h["sec-fetch-site"] = "same-site"
        return h

    def _full_cookies(self) -> dict:
        if self.access_token:
            self.cookies["x-iag-customer"] = self.access_token
        if self.session_access_token:
            self.cookies["x-iag-auth-session"] = self.session_access_token
        return self.cookies

    # ── Core request with retry + cookie capture ─────────────────────────

    # ── API helpers ──────────────────────────────────────────────────────

    def _next(self, answers=None, cover_type=None):
        payload = {"answers": answers or {}, "brandConfig": self.BRAND_CONFIG}
        if cover_type:
            payload["coverType"] = cover_type
        return self._request(
            f"{self.BASE_URL}{self.API_V1}/nrma/home/{self.quote_id}//Next",
            "POST", payload,
        )

    def _refresh(self, answers):
        payload = {"answers": answers, "brandConfig": self.BRAND_CONFIG}
        return self._request(
            f"{self.BASE_URL}{self.API_V1}/nrma/home/{self.quote_id}//Refresh",
            "POST", payload,
        )

    def _refresh_no_scroll(self, answers):
        payload = {"answers": answers, "brandConfig": self.BRAND_CONFIG}
        return self._request(
            f"{self.BASE_URL}{self.API_V1}/nrma/home/{self.quote_id}//RefreshNoScroll",
            "POST", payload,
        )

    # ── Address matching ─────────────────────────────────────────────────

    @staticmethod
    def _find_matching_address(results, address, suburb, state, postcode):
        if not results:
            return None
        def norm(v):
            return str(v).strip().upper() if v else ""
        best_match, best_score = None, 0
        for entry in results:
            sc = 0
            if norm(entry.get("postcode", "")) == norm(postcode): sc += 3
            if norm(entry.get("state", "")) == norm(state): sc += 2
            if norm(entry.get("locality_name", "")) == norm(suburb): sc += 2
            street = entry.get("street_address", {})
            if norm(street.get("street_name", "")) and norm(street.get("street_name", "")) in norm(address): sc += 2
            if norm(street.get("house_number", "")) and norm(street.get("house_number", "")) in norm(address): sc += 1
            if sc > best_score:
                best_score, best_match = sc, entry
        return best_match if best_score >= 7 else None

    @staticmethod
    def _find_aggregate_premium(tier_data: dict) -> dict | None:
        for plan in tier_data.get("payments", {}).get("billing_plan_options", []):
            agg = plan.get("aggregate_premium_details")
            if agg:
                return agg
        return None

    # ═════════════════════════════════════════════════════════════════════
    # STEP 0: Create quote + token
    # ═════════════════════════════════════════════════════════════════════

    def init_session(self):
        log.info("┌─ STEP 0: Create quote & token")
        state = self._str("State", "NSW")
        if self.product is None:
            raise ValueError("Product configuration not resolved; ensure Cover input is valid")
        serenity = self.product["serenityProduct"]

        data = self._request(
            f"{self.BASE_URL}{self.API_V1}/nrma/home//Next", "POST",
            {
                "answers": {},
                "serenityProduct": serenity,
                "serenitySelectedState": state,
                "brandConfig": self.BRAND_CONFIG,
            },
            cookies={},
        )
        self.quote_id = data["quoteId"]
        novus = data.get("novusContext", {})
        qid_sig = novus.get("quoteIdSignature")
        qid_exp = novus.get("quoteIdSignatureExpiry")
        log.info(f"│  Quote ID: {self.quote_id}")

        token_data = self._request(
            "https://connect.nrma.com.au/cb/anonymous/quote/nrma", "POST",
            {"payload": {"quoteId": self.quote_id, "expiry": qid_exp}, "signature": qid_sig},
            headers=self._token_headers(), cookies={},
        )
        self.access_token = token_data.get("accessToken")
        if not self.access_token:
            raise ValueError("Failed to get access token")

        self.cookies = self._build_cookies()
        self.cookies["x-iag-customer"] = self.access_token
        if self.session_access_token:
            self.cookies["x-iag-auth-session"] = self.session_access_token
        log.info(f"└─ Token acquired, {len(self.cookies)} cookies cached")

    # ═════════════════════════════════════════════════════════════════════
    # STEP 1: Revalidate
    # ═════════════════════════════════════════════════════════════════════

    def step_revalidate(self):
        log.info("┌─ STEP 1: Revalidate")
        self._next()
        log.info("└─ Done")

    # ═════════════════════════════════════════════════════════════════════
    # STEP 2: Pre-qualification
    # ═════════════════════════════════════════════════════════════════════

    def step_pre_qualification(self):
        log.info("┌─ STEP 2: Pre-qualification")
        if self.product is None:
            raise ValueError("Product configuration not resolved; ensure Cover input is valid")
        prop_type = self.PROPERTY_TYPE_MAPPING.get(
            self._str("Building_Type"), "FREESTANDING_HOME"
        )
        is_owner = self._bool("Ownership")
        answers = {
            "PropertyType": prop_type,
            "IsOwner": is_owner,
            "IsHolidayHome": self._bool("Holiday_Home"),
            "IsOwnerOccupying": self._bool("Live_In_Property"),
            "HasFlatmates": self._bool("Flatmates"),
        }
        if not is_owner:
            answers["WhoDoYouRentWith"] = self.LIVE_WITH_MAPPING.get(
                self._str("Live_With", "Myself or family"), "MYSELF_OR_FAMILY"
            )
        cover_type = self.product["coverType"]
        log.info(f"│  {prop_type} | Owner={is_owner} | CoverType={cover_type}")
        self._next(answers, cover_type=cover_type)
        log.info("└─ Done")

    # ═════════════════════════════════════════════════════════════════════
    # STEP 3: Address
    # ═════════════════════════════════════════════════════════════════════

    def step_address(self):
        log.info("┌─ STEP 3: Address + Start date")
        address = self._str("Address").strip()
        suburb = self._str("Suburb").strip()
        state = self._str("State").strip()
        postcode = str(self.input_data.get("Postcode", "")).zfill(4)
        full_query = f"{address}, {suburb} {state} {postcode}"

        results = self._request(
            f"{self.BASE_URL}{self.API_V1}/reference/address/lookup", "POST",
            {"excludePostalAddresses": True, "address": full_query, "brand": "nrma"},
        )
        matched = self._find_matching_address(results, address, suburb, state, postcode)
        if not matched and address:
            results = self._request(
                f"{self.BASE_URL}{self.API_V1}/reference/address/lookup", "POST",
                {"excludePostalAddresses": True, "address": address, "brand": "nrma"},
            )
            matched = self._find_matching_address(results, address, suburb, state, postcode)

        if matched:
            self.matched_address = matched
            log.info(f"│  ✓ {matched.get('full_address')}")
        elif results:
            self.matched_address = results[0]
            log.warning(f"│  ⚠ Fallback: {results[0].get('full_address')}")
        else:
            raise ValueError("No address results")

        cover_start = cover_start_date(self._str("Policy_Effective_Date"))
        self._next({
            "PropertyAddressLookup": self.matched_address,
            "AddressToggle": 0,
            "CoverStartDate": cover_start,
        })
        log.info("└─ Done")

    # ═════════════════════════════════════════════════════════════════════
    # STEP 4: Home details (branching for buildings)
    # ═════════════════════════════════════════════════════════════════════

    def step_home_details(self):
        log.info("┌─ STEP 4: Home details")

        # Use occupant overrides if set (from SI retry)
        if self.occupant_overrides:
            adults = self.occupant_overrides.get("adults", str(self._int("Adults", 1)))
            children = self.occupant_overrides.get("children", str(self._int("Children", 0)))
            log.info(f"│  ⚠ Using adjusted occupants: Adults={adults}, Children={children}")
        else:
            adults = str(self._int("Adults", 1))
            children = str(self._int("Children", 0))

        answers = {
            "StructurallySound": self._bool("Good_Building_Condition"),
            "AdultOccupants": adults,
            "ChildOccupants": children,
        }

        if self.has_buildings:
            business_use = self._bool("Business_Use")
            answers.update({
                "RenovationOrAlteration": self._bool("Renovations"),
                "BodyCorporateOrStrata": self._bool("Strata"),
                "BusinessUse": business_use,
                "IncompleteOrNonStandard": self._bool("Property_Special_Conditions"),
            })
            if business_use:
                answers["RestrictedBusiness"] = self._bool("Business_Involve_Activities")
                answers["AreAnyOwnersRegisteredForGST"] = self._bool("Registered_GST")
                if answers["AreAnyOwnersRegisteredForGST"]:
                    answers["InputTaxCreditPercentage"] = self._parse_gst_credit()

        self._next(answers)
        log.info("└─ Done")
    # ═════════════════════════════════════════════════════════════════════
    # STEP 5: Your land (branching for buildings)
    # ═════════════════════════════════════════════════════════════════════

    def step_your_land(self):
        log.info("┌─ STEP 5: Your land")
        storeys = self._str("Storeys_If_House", "1")
        try:
            if storeys and int(float(storeys)) >= 4:
                storeys = "4PLUS"
        except (ValueError, TypeError):
            storeys = "1"

        answers = {
            "FloodingRisk": self._bool("Elevated"),
            "Storeys": storeys,
            "Alarm": self.ALARM_MAPPING.get(self._str("Alarm", "No alarm"), "NO_ALARM"),
        }

        if self.has_buildings:
            year_constructed = self._int("Building_Year", 2000)
            answers.update({
                "LandSlope": self.LAND_SLOPE_MAPPING.get(
                    self._str("Slope", "Flat or gentle"), "FLAT_GENTLE"
                ),
                "WallMaterial": self.WALL_MATERIAL_MAPPING.get(
                    self._str("Construction_Material", "Brick veneer"), "BRICK_VENEER"
                ),
                "RoofMaterial": self.ROOF_MATERIAL_MAPPING.get(
                    self._str("Roof_Type", "Concrete tile"), "CONCRETE_TILE"
                ),
                "HeritageListed": self._bool("Heritage_Listed"),
                "YearConstructed": str(year_constructed),
            })
            if year_constructed <= 1956:
                answers["ElectricalWiringReplaced"] = self._bool("Electrical_Wiring_Replaced")

        self._next(answers)
        log.info("└─ Done")

    # ═════════════════════════════════════════════════════════════════════
    # STEP 6: Rooms (branching for buildings)
    # ═════════════════════════════════════════════════════════════════════

    def step_rooms(self):
        log.info("┌─ STEP 6: Rooms")

        if self.room_overrides:
            answers = dict(self.room_overrides)
            log.info(f"│  ⚠ Using adjusted rooms: {answers}")
        else:
            answers = {
                "SmallBedrooms": str(self._int("Small_Bedrooms")),
                "MediumBedrooms": str(self._int("Medium_Bedrooms")),
                "LargeBedrooms": str(self._int("Large_Bedrooms")),
                "Bathrooms": str(self._int("Bathrooms")),
                "LivingSpaces": str(self._int("Living_Rooms", 1)),
                "DiningRooms": str(self._int("Dining_Rooms", 1)),
            }

        if self.has_buildings:
            answers["QualityOfFixtures"] = (
                self.QUALITY_OF_FIXTURES_MAPPING.get(
                    self._str("Fixture_Quality", "Standard"), "STANDARD"
                )
            )

        self._next(answers)
        log.info("└─ Done")

    # ═════════════════════════════════════════════════════════════════════
    # STEP 7: Outdoor features + conditional cyclone
    # ═════════════════════════════════════════════════════════════════════

    def step_outdoor(self):
        log.info("┌─ STEP 7: Outdoor features")
        answers = {
            "ParkingSpacesCarport": str(self._int("Carport")),
            "ParkingSpacesGarage": str(self._int("Garage")),
            "SmallSheds": str(self._int("Small_Shed")),
            "LargeSheds": str(self._int("Large_Shed")),
            "BalconiesAndDecks": str(self._int("Balconies")),
            "Verandahs": str(self._int("Verandahs")),
            "InGroundPool": self._bool("Pool"),
        }
        if self.has_buildings:
            answers.update({
                "BuiltInHeatingOrCooling": self._bool("Ducted_Air_Conditioning"),
                "SolarPanels": self._bool("Solar_Panels"),
                "TennisCourt": self._bool("Tennis_Court"),
                "JettyWharfLandingPier": self._bool("Jetty"),
            })

        # Apply outdoor overrides from SI adjustment (garage/carport=0)
        if hasattr(self, "outdoor_overrides") and self.outdoor_overrides:
            answers.update(self.outdoor_overrides)
            log.info(f"│  ⚠ Outdoor overrides: {self.outdoor_overrides}")

        self.last_response = self._next(answers)

        next_step = self.last_response.get("target_step", {}).get("stepId", "")
        if next_step == "cyclone-upgrades":
            self.step_cyclone_upgrades(self.last_response)

        log.info("└─ Done")

    def step_cyclone_upgrades(self, outdoor_response: dict):
        log.info("┌─ STEP 7b: Cyclone upgrades (conditional)")
        answers = {}
        questions_found = []
        for section in outdoor_response.get("target_step", {}).get("sections", []):
            for question in section.get("questions", []):
                qid = question.get("questionId")
                if not qid:
                    continue
                questions_found.append(qid)
                csv_field = self.CYCLONE_FIELD_MAP.get(qid)
                if csv_field:
                    answers[qid] = "true" if self._bool(csv_field) else "false"
                else:
                    answers[qid] = "false"

        log.info(f"│  Questions ({len(questions_found)}): {questions_found}")
        self.last_response = self._next(answers)
        log.info("└─ Done")

    # ═════════════════════════════════════════════════════════════════════
    # STEP 8: Sum insured (product-specific)
    # ═════════════════════════════════════════════════════════════════════

    def step_sum_insured(self):
        log.info("┌─ STEP 8: Sum insured")
        calc = (
            self.last_response.get("novusContext", {})
            .get("calculatedSumInsured", {})
        ) if self.last_response else {}

        answers = {}

        if self.has_buildings:
            building_sum = self._int("Buildings_Sum_Insured", 0)
            if not building_sum:
                building_sum = calc.get("homeSumInsured", 0)
            if not building_sum:
                raise ValueError("No building sum insured available")

            bld_min = calc.get("homeSumInsuredMin", 0)
            bld_max = calc.get("homeSumInsuredMax", 0)

            log.info(
                f"│  Building: ${building_sum:,} "
                f"(range: ${bld_min:,} — ${bld_max:,})"
            )

            if bld_min and building_sum < bld_min:
                raise SumInsuredOutOfRange(
                    "building", building_sum, bld_min, bld_max, "low"
                )
            if bld_max and building_sum > bld_max:
                raise SumInsuredOutOfRange(
                    "building", building_sum, bld_min, bld_max, "high"
                )

            answers["SumInsuredHomeCustomAmount"] = str(building_sum)
            answers["BuildingOptionalExtrasAccidentalDamage"] = ["false"]
            answers["BuildingOptionalExtrasElectricalBurnOut"] = ["false"]

        if self.has_contents:
            contents_sum = self._int("Contents_Sum_Insured", 0)
            if not contents_sum:
                contents_sum = calc.get("contentsSumInsured", 0)
            if not contents_sum:
                raise ValueError("No contents sum insured available")

            cnt_min = calc.get("contentsSumInsuredMin", 0)
            cnt_max = calc.get("contentsSumInsuredMax", 0)

            log.info(
                f"│  Contents: ${contents_sum:,} "
                f"(range: ${cnt_min:,} — ${cnt_max:,})"
            )

            if cnt_min and contents_sum < cnt_min:
                raise SumInsuredOutOfRange(
                    "contents", contents_sum, cnt_min, cnt_max, "low"
                )
            if cnt_max and contents_sum > cnt_max:
                raise SumInsuredOutOfRange(
                    "contents", contents_sum, cnt_min, cnt_max, "high"
                )

            answers["SumInsuredContentCustomAmount"] = str(contents_sum)
            answers["ContentsOptionalExtrasAccidentalDamage"] = ["false"]
            answers["ContentsOptionalExtrasElectricalBurnOut"] = ["false"]

        self._next(answers)
        log.info("└─ Done")

    # ═════════════════════════════════════════════════════════════════════
    # STEP 9: Tailor contents (skip for buildings-only)
    # ═════════════════════════════════════════════════════════════════════

    def step_tailor_contents(self):
        if self.product is None:
            raise ValueError("Product configuration not resolved; ensure Cover input is valid")
        if not self.product["hasTailorContents"]:
            log.info("┌─ STEP 9: Tailor contents — SKIPPED (buildings only)")
            log.info("└─")
            return

        log.info("┌─ STEP 9: Tailor contents")
        self._refresh_no_scroll({"ValuablesJewelleryIncreaseLimit": False})
        self._refresh_no_scroll({
            "ValuablesJewelleryIncreaseLimit": False,
            "ValuablesArtworkIncreaseLimit": False,
            "ValuablesCollectiblesIncreaseLimit": False,
            "PortablesJewelleryIncreaseLimit": False,
        })
        self._next({
            "ValuablesJewelleryIncreaseLimit": False,
            "ValuablesArtworkIncreaseLimit": False,
            "ValuablesCollectiblesIncreaseLimit": False,
            "PortablesJewelleryIncreaseLimit": False,
            "PortablesElectronicsIncreaseLimit": False,
            "PortableSportingIncreaseLimit": False,
            "PortableMobilityScootersIncreaseLimit": False,
            "PortableMedicalIncreaseLimit": False,
            "PortableClothingAndLuggageIncreaseLimit": False,
            "ContentsInStorage": False,
        })
        log.info("└─ Done")

    # ═════════════════════════════════════════════════════════════════════
    # STEP 10: Owner details
    # ═════════════════════════════════════════════════════════════════════

    def step_owner(self):
        log.info("┌─ STEP 10: Owner details")
        owner_type = "Individual" if self._str("Owner_Type", "Person") == "Person" else "Business"
        membership = "true" if self._bool("Membership") else "false"
        dob = date(
            self._int("Owner_DOB_YYYY_Dummy", 1980),
            self._int("Owner_DOB_MM_Dummy", 1),
            self._int("Owner_DOB_DD_Dummy", 1),
        ).strftime("%Y-%m-%d")
        resp = self._next({
            "Parties.0.PartyType": owner_type,
            "Parties.0.AreYouAnExistingCustomer": membership,
            "Parties.0.PartyDOB": dob,
        })
        self.party_id = resp.get("novusContext", {}).get("answers", {}).get("Parties.0.PartyId")
        log.info(f"│  Party ID: {self.party_id}")
        log.info("└─ Done")

    # ═════════════════════════════════════════════════════════════════════
    # STEP 11: Owner dashboard
    # ═════════════════════════════════════════════════════════════════════

    def step_owner_dashboard(self):
        log.info("┌─ STEP 11: Owner dashboard")
        self._next()
        log.info("└─ Done")

    # ═════════════════════════════════════════════════════════════════════
    # STEP 12: Claims history → PRICING
    # ═════════════════════════════════════════════════════════════════════

    def step_claims_and_pricing(self):
        log.info("┌─ STEP 12: Claims + Pricing")
        has_claims = self._bool("Claims_5yrs")
        answers = {"ClaimsMade": has_claims}

        if has_claims:
            claim_event = self.HOME_CLAIM_TYPE_MAPPING.get(
                self._str("Type_Of_Claim_Home", ""), "OTHER"
            )
            claim_year = self._str("Claim_Year", "")
            claim_year_str = ""
            try:
                if claim_year and str(claim_year) not in ("", "nan", "None"):
                    claim_year_str = str(int(float(claim_year)))
            except (ValueError, TypeError):
                pass
            if claim_year_str:
                answers.update({
                    "PriorClaims.0.Event": claim_event,
                    "PriorClaims.0.YearOccurred": claim_year_str,
                    "PriorClaims.0.InvolveThisProperty": self._bool("Claim_For_Address"),
                    "PriorClaims.0.ClaimAddress": "propertyAddress",
                    "PriorClaims.0.OwnerPartyId": self.party_id,
                })

        self.pricing_response = self._next(answers)
        cover_opts = self.pricing_response.get("novusContext", {}).get("coverOptions", {})

        if not cover_opts:
            log.warning("│  ⚠ No coverOptions in response")
            return

        if self.is_combined:
            self._extract_combined_pricing(cover_opts)
        else:
            self._extract_single_pricing(cover_opts)

        log.info("└─ Done")

    def _extract_combined_pricing(self, cover_opts: dict):
        std = cover_opts.get("STANDARD", {})
        plus = cover_opts.get("PLUS", {})
        std_exc = std.get("excesses", {})
        plus_exc = plus.get("excesses", {})

        self.results["NRMA_DefaultExcess_Building"] = std_exc.get("building")
        self.results["NRMA_DefaultExcess_Content"] = std_exc.get("contents")
        self.results["NRMA_YearlyPayment"] = std.get("prices", {}).get("ANNUAL")

        self.results["NRMAPlus_DefaultExcess_Building"] = plus_exc.get("building")
        self.results["NRMAPlus_DefaultExcess_Content"] = plus_exc.get("contents")
        self.results["NRMAPlus_YearlyPayment"] = plus.get("prices", {}).get("ANNUAL")

        std_agg = self._find_aggregate_premium(std)
        if std_agg:
            self.results["NRMA_Base_Prem"] = std_agg.get("base_premium")
            self.results["NRMA_GST"] = std_agg.get("gst")
            self.results["NRMA_Stamp_Duty"] = std_agg.get("stamp_duty")
            self.results["NRMA_ESL"] = std_agg.get("esl")

        log.info(f"│  ✓ STD: ${self.results['NRMA_YearlyPayment']}/yr")
        log.info(f"│  ✓ PLUS: ${self.results['NRMAPlus_YearlyPayment']}/yr")

    def _extract_single_pricing(self, cover_opts: dict):
        if self.product is None:
            raise ValueError("Product configuration not resolved; ensure Cover input is valid")
        std = cover_opts.get("STANDARD", {})
        plus = cover_opts.get("PLUS", {})
        std_prices = std.get("prices", {})
        plus_prices = plus.get("prices", {})
        excess_key = self.product["excessKey"]

        self.results["NRMA_DefaultExcess"] = std.get("excesses", {}).get(excess_key)
        self.results["NRMA_YearlyPayment"] = std_prices.get("ANNUAL")
        self.results["NRMAPlus_DefaultExcess"] = plus.get("excesses", {}).get(excess_key)
        self.results["NRMAPlus_YearlyPayment"] = plus_prices.get("ANNUAL")
        self.default_excess = std.get("excesses", {}).get(excess_key, self.default_excess)

        log.info(f"│  ✓ STD: ${std_prices.get('ANNUAL')}/yr, Excess=${self.results['NRMA_DefaultExcess']}")
        log.info(f"│  ✓ PLUS: ${plus_prices.get('ANNUAL')}/yr")

        if self.toggle:
            self.results["NRMA_MonthlyPayment"] = std_prices.get("MONTHLY")
            self.results["NRMA_QuarterlyPayment"] = std_prices.get("QUARTERLY")
            self.results["NRMA_FortnightlyPayment"] = std_prices.get("FORTNIGHTLY")
            self.results["NRMAPlus_MonthlyPayment"] = plus_prices.get("MONTHLY")
            self.results["NRMAPlus_QuarterlyPayment"] = plus_prices.get("QUARTERLY")
            self.results["NRMAPlus_FortnightlyPayment"] = plus_prices.get("FORTNIGHTLY")
            self.results[f"NRMA_excess_{self.default_excess}"] = std_prices.get("ANNUAL")
            self.results[f"NRMAPlus_excess_{self.default_excess}"] = plus_prices.get("ANNUAL")

    # ═════════════════════════════════════════════════════════════════════
    # STEP 13: Toggle — excess matrix + optional extras
    # ═════════════════════════════════════════════════════════════════════

    def step_toggle_collection(self):
        if self.product is None:
            raise ValueError("Product configuration not resolved; ensure Cover input is valid")
        if not self.toggle or not self.product["hasToggle"]:
            return
        if not self.pricing_response:
            return

        log.info("┌─ STEP 13: Toggle collection")

        excess_field = self.product["excessField"]
        excess_path = self.product["excessOptionsPath"]
        extras_target = self.product["extrasTarget"]

        novus = self.pricing_response.get("novusContext", {})
        cover_opts = novus.get("coverOptions", {})

        available_excesses = (
            cover_opts.get("STANDARD", {})
            .get("excessOptions", {})
            .get(excess_path, {})
            .get("BaseExcess", [])
        )
        if not available_excesses:
            available_excesses = (
                novus.get("conditions", {})
                .get(excess_path, {})
                .get("BaseExcess", [])
            )

        log.info(f"│  Excesses: {available_excesses} via {excess_field}")

        # Excess matrix
        for excess in available_excesses:
            if str(excess) == str(self.default_excess):
                continue
            try:
                resp = self._refresh({
                    "LevelOfCover": "STANDARD",
                    "BillingFrequency": "MONTHLY",
                    excess_field: str(excess),
                    "OptionalExtrasAccidentalDamage": [],
                    "OptionalExtrasElectricalBurnOut": [],
                })
                co = resp.get("novusContext", {}).get("coverOptions", {})
                std_annual = co.get("STANDARD", {}).get("prices", {}).get("ANNUAL")
                plus_annual = co.get("PLUS", {}).get("prices", {}).get("ANNUAL")
                self.results[f"NRMA_excess_{excess}"] = std_annual
                self.results[f"NRMAPlus_excess_{excess}"] = plus_annual
                log.info(f"│  ✓ ${excess}: Std={std_annual}, Plus={plus_annual}")
            except Exception as e:
                log.error(f"│  ✗ ${excess}: {e}")

        # Accidental Damage
        try:
            resp_ad = self._refresh({
                "LevelOfCover": "STANDARD",
                "BillingFrequency": "MONTHLY",
                excess_field: str(self.default_excess),
                "OptionalExtrasAccidentalDamage": [extras_target],
                "OptionalExtrasElectricalBurnOut": [],
            })
            co = resp_ad.get("novusContext", {}).get("coverOptions", {})
            self.results["NRMA_AccidentalDamage"] = co.get("STANDARD", {}).get("prices", {}).get("ANNUAL")
            self.results["NRMAPlus_AccidentalDamage"] = co.get("PLUS", {}).get("prices", {}).get("ANNUAL")
            log.info(f"│  ✓ AccidentalDamage: {self.results['NRMA_AccidentalDamage']}")
        except Exception as e:
            log.error(f"│  ✗ AccidentalDamage: {e}")

        # Fusion
        try:
            resp_fb = self._refresh({
                "LevelOfCover": "STANDARD",
                "BillingFrequency": "MONTHLY",
                excess_field: str(self.default_excess),
                "OptionalExtrasAccidentalDamage": [],
                "OptionalExtrasElectricalBurnOut": [extras_target],
            })
            co = resp_fb.get("novusContext", {}).get("coverOptions", {})
            self.results["NRMA_Fusion"] = co.get("STANDARD", {}).get("prices", {}).get("ANNUAL")
            self.results["NRMAPlus_Fusion"] = co.get("PLUS", {}).get("prices", {}).get("ANNUAL")
            log.info(f"│  ✓ Fusion: {self.results['NRMA_Fusion']}")
        except Exception as e:
            log.error(f"│  ✗ Fusion: {e}")

        log.info("└─ Toggle complete")

    def _calculate_room_adjustments(self, error_str: str) -> dict | None:
        try:
            parts = error_str.split("|")
            direction = parts[2] if len(parts) > 2 else ""
            params = {}
            for part in parts:
                if "=" in part:
                    key, val = part.split("=", 1)
                    params[key] = int(val)
            target = params.get("target", 0)
        except (IndexError, ValueError) as e:
            log.warning(f"│  Could not parse SI error: {e}")
            return None

        if direction == "low":
            return self._rooms_for_too_low(target)
        elif direction == "high":
            return self._rooms_for_too_high(error_str)  # Pass full error
        return None
    


    def _rooms_for_too_low(self, target_si: int) -> dict:
        """
        Minimize rooms + set garage/carport to 0 to push range lower.

        SI < $50k:  1 small bed, 1 bath, rest=0
        SI >= $50k: 1 small bed, 1 bath, 1 living, rest=0

        Also sets Carport=0, Garage=0 via outdoor overrides.
        """
        if target_si < 50000:
            log.info(
                "│  Strategy (SI < $50k, too low): "
                "1 small bed, 1 bath, rest=0, garage/carport=0"
            )
            rooms = {
                "SmallBedrooms": "1",
                "MediumBedrooms": "0",
                "LargeBedrooms": "0",
                "Bathrooms": "1",
                "LivingSpaces": "0",
                "DiningRooms": "0",
            }
        else:
            log.info(
                "│  Strategy (SI >= $50k, too low): "
                "1 small bed, 1 bath, 1 living, rest=0, garage/carport=0"
            )
            rooms = {
                "SmallBedrooms": "1",
                "MediumBedrooms": "0",
                "LargeBedrooms": "0",
                "Bathrooms": "1",
                "LivingSpaces": "1",
                "DiningRooms": "0",
            }

        # Also zero out garage/carport for the outdoor step
        self.outdoor_overrides = {
            "ParkingSpacesCarport": "0",
            "ParkingSpacesGarage": "0",
            "InGroundPool":False,
        }
        return rooms

    def _rooms_for_too_high(self, error_str: str) -> dict:
        """
        Keep existing rooms, calculate large bedrooms to add based on
        difference between target SI and upper bound.

        Building: +1 large bedroom ≈ +$300,000 to upper bound
        Contents: +1 large bedroom ≈ +$13,000 to upper bound
        """
        # Parse target and max from error string
        params = {}
        for part in error_str.split("|"):
            if "=" in part:
                key, val = part.split("=", 1)
                params[key] = int(val)

        si_type = error_str.split("|")[1] if "|" in error_str else ""
        target = params.get("target", 0)
        maximum = params.get("max", 0)

        # Calculate difference
        difference = max(target - maximum, 0)

        # Choose rate based on product type
        if si_type == "building":
            rate = self.SI_PER_LARGE_BEDROOM_BUILDING
        else:
            rate = self.SI_PER_LARGE_BEDROOM_CONTENTS

        # Round up
        import math
        additions = math.ceil(difference / rate) if rate > 0 else 1
        additions = max(1, min(additions, self.MAX_LARGE_BEDROOM_ADDITIONS))

        original_large = self._int("Large_Bedrooms")
        new_large = original_large + additions

        log.info(
            f"│  Strategy (too high): "
            f"difference=${difference:,}, "
            f"rate=${rate:,}/bedroom, "
            f"adding {additions} large bedrooms "
            f"({original_large} → {new_large})"
        )

        return {
            "SmallBedrooms": str(self._int("Small_Bedrooms")),
            "MediumBedrooms": str(self._int("Medium_Bedrooms")),
            "LargeBedrooms": str(new_large),
            "Bathrooms": str(self._int("Bathrooms")),
            "LivingSpaces": str(self._int("Living_Rooms", 1)),
            "DiningRooms": str(self._int("Dining_Rooms", 1)),
        }
    # ═════════════════════════════════════════════════════════════════
    # NEW: State reset for retry
    # ═════════════════════════════════════════════════════════════════

    def _reset_state(self):
        """Reset all state for a fresh quote attempt."""
        self.quote_id = None
        self.access_token = None
        self.session_access_token = None
        self.matched_address = None
        self.party_id = None
        self.pricing_response = None
        self.last_response = None
        self.cookies = {}
        self._cookie_refresh_count = 0
        self._proxy_mode = False
        self._proxy_escalation_count = 0
        log.info("│  State reset for retry")

    # ═════════════════════════════════════════════════════════════════
    # NEW: Single quote execution (extracted from run)
    # ═════════════════════════════════════════════════════════════════

    def _execute_quote(self) -> dict:
        """Execute full quote flow. Returns results dict."""
        try:
            self.init_session()
            self.step_revalidate()
            self.step_pre_qualification()
            self.step_address()
            self.step_home_details()
            self.step_your_land()
            self.step_rooms()
            self.step_outdoor()
            self.step_sum_insured()
            self.step_tailor_contents()
            self.step_owner()
            self.step_owner_dashboard()
            self.step_claims_and_pricing()
            self.step_toggle_collection()

            self.results["QuoteNumber"] = self.quote_id
            self.results["QuoteDate"] = datetime.now().strftime("%d/%m/%Y")
            self.results["Timestamp (in hh:mm)"] = (
                datetime.now().strftime("%H:%M")
            )
            self.results["QuoteLink"] = (
                f"{self.BASE_URL}/get-cover/home/"
                f"{self.quote_id}/quote-summary"
            )
            return self.results

        except SumInsuredOutOfRange as e:
            self.results["Reason_for_quote_failure"] = str(e)
            return self.results

        except Exception as e:
            self.results["Reason_for_quote_failure"] = (
                f"{type(e).__name__}: {e}"
            )
            return self.results


    # ═════════════════════════════════════════════════════════════════════
    # MAIN RUNNER
    # ═════════════════════════════════════════════════════════════════════

    def _reset_results(self):
        """Clean results dict for retry."""
        self.results = {k: "" for k in self.fields_order}
        self.results.update({
            "Sample_Number": self.input_data.get("Sample_Number"),
            "DOB_DD": self.input_data.get("Owner_DOB_DD_Dummy"),
            "DOB_MM": self.input_data.get("Owner_DOB_MM_Dummy"),
            "DOB_YYYY": self.input_data.get("Owner_DOB_YYYY_Dummy"),
            "Postcode": self.input_data.get("Postcode"),
            "QuoteDate": time.strftime("%d-%b-%y"),
            "Timestamp (in hh:mm)": time.strftime("%H:%M"),
        })

    def _populate_rerun_columns(self, result: dict):
        """Fill rerun columns if any adjustments were applied."""
        if self.room_overrides:
            result["Rerun_Small_Bedrooms"] = self.room_overrides.get(
                "SmallBedrooms", ""
            )
            result["Rerun_Large_Bedrooms"] = self.room_overrides.get(
                "LargeBedrooms", ""
            )
        if self.outdoor_overrides:
            result["Rerun_Carport"] = self.outdoor_overrides.get(
                "ParkingSpacesCarport", ""
            )
        if self.occupant_overrides:
            result["Rerun_Adults"] = self.occupant_overrides.get(
                "adults", ""
            )
            result["Rerun_Children"] = self.occupant_overrides.get(
                "children", ""
            )

    def _run_from_stored(
        self, sample_id: str, stored: dict, run_start: float,
    ) -> dict:
        stored_phase = stored.get("phase", 2)
        original_error = stored.get("original_error", "")
        retry1_error = stored.get("retry1_error", "")
        retry2_error = stored.get("retry2_error", "")

        log.info(f"│  📋 Stored Phase {stored_phase}")

        self.room_overrides = stored.get("room_overrides")
        self.outdoor_overrides = stored.get("outdoor_overrides")
        self.occupant_overrides = stored.get("occupant_overrides")

        result = self._execute_quote()
        self._populate_rerun_columns(result)
        failure = result.get("Reason_for_quote_failure", "")
        elapsed = time.time() - run_start

        # Success
        if not failure:
            _clear_si_adjustment(sample_id)
            log.info(f"✓ COMPLETE (stored Phase {stored_phase}) — {self.quote_id} | {elapsed:.1f}s")
            return result

        # Phase 2 SI still low + Contents → progress to Phase 3
        if (
            "SumInsuredOutOfRange" in failure
            and "|low|" in failure
            and stored_phase == 2
            and self.is_contents_only
        ):
            log.info(f"⚠ Stored Phase 2 SI still low — progressing to Phase 3")
            return self._attempt3_occupant_override(
                sample_id, original_error, failure, run_start,
            )

        # Transient error — keep stored, build full history
        if "SumInsuredOutOfRange" not in failure:
            # Update stored with current transient error
            if stored_phase == 2:
                _record_si_adjustment(
                    sample_id, phase=2,
                    room_overrides=self.room_overrides,
                    outdoor_overrides=self.outdoor_overrides,
                    occupant_overrides=self.occupant_overrides,
                    original_error=original_error,
                    retry1_error=failure,
                )
            elif stored_phase == 3:
                _record_si_adjustment(
                    sample_id, phase=3,
                    room_overrides=self.room_overrides,
                    outdoor_overrides=self.outdoor_overrides,
                    occupant_overrides=self.occupant_overrides,
                    original_error=original_error,
                    retry1_error=retry1_error,
                    retry2_error=failure,
                )

            result["Reason_for_quote_failure"] = self._build_failure_message(
                original_error=original_error,
                retry1_error=retry1_error if stored_phase >= 2 else "",
                retry2_error=retry2_error if stored_phase >= 3 else "",
                current_error=failure,
                phase=stored_phase,
            )
            log.error(f"✗ FAILED (stored Phase {stored_phase}, transient) — {elapsed:.1f}s")
            return result

        # Permanent SI failure — clear + full history
        _clear_si_adjustment(sample_id)
        result["Reason_for_quote_failure"] = self._build_failure_message(
            original_error=original_error,
            retry1_error=retry1_error if stored_phase >= 2 else failure,
            retry2_error=failure if stored_phase >= 3 else "",
            current_error=failure,
            phase=stored_phase,
        )
        log.error(f"✗ FAILED (stored Phase {stored_phase}, SI permanent) — {elapsed:.1f}s")
        return result

    
    def _run_si_retry_chain(
        self, sample_id: str, original_error: str, run_start: float,
    ) -> dict:
        log.info(f"{'─' * 60}")
        log.info(f"⚠ SI out of range — calculating adjustments")

        adjustments = self._calculate_room_adjustments(original_error)
        if not adjustments:
            self.results["Reason_for_quote_failure"] = self._build_failure_message(
                original_error=original_error,
                current_error="NO_ADJUSTMENT_POSSIBLE",
                phase=1,
            )
            elapsed = time.time() - run_start
            log.error(f"✗ FAILED — no adjustment possible — {elapsed:.1f}s")
            return self.results

        # ── Attempt 2 ──
        log.info(f"{'─' * 60}")
        log.info("┌─ Attempt 2: Adjusted rooms")
        self._reset_state()
        self._reset_results()
        self.room_overrides = adjustments

        _record_si_adjustment(
            sample_id, phase=2,
            room_overrides=self.room_overrides,
            outdoor_overrides=self.outdoor_overrides,
            occupant_overrides=self.occupant_overrides,
            original_error=original_error,
        )

        result = self._execute_quote()
        retry1_failure = result.get("Reason_for_quote_failure", "")

        if not retry1_failure:
            self._populate_rerun_columns(result)
            elapsed = time.time() - run_start
            log.info(f"✓ COMPLETE (Attempt 2) — {self.quote_id} | {elapsed:.1f}s")
            return result

        # Contents + SI still low → Attempt 3
        if (
            "SumInsuredOutOfRange" in retry1_failure
            and "|low|" in retry1_failure
            and self.is_contents_only
        ):
            return self._attempt3_occupant_override(
                sample_id, original_error, retry1_failure, run_start,
            )

        # Attempt 2 failed — not eligible for 3
        self._populate_rerun_columns(result)

        if "SumInsuredOutOfRange" in retry1_failure:
            _clear_si_adjustment(sample_id)
        else:
            _record_si_adjustment(
                sample_id, phase=2,
                room_overrides=self.room_overrides,
                outdoor_overrides=self.outdoor_overrides,
                occupant_overrides=self.occupant_overrides,
                original_error=original_error,
                retry1_error=retry1_failure,
            )

        result["Reason_for_quote_failure"] = self._build_failure_message(
            original_error=original_error,
            current_error=retry1_failure,
            phase=2,
        )
        elapsed = time.time() - run_start
        log.error(f"✗ ATTEMPT 2 FAILED — {elapsed:.1f}s")
        return result

    def run(self) -> dict:
        run_start = time.time()
        sample_id = str(self.input_data.get("Sample_Number", "?"))

        try:
            # ── Check for stored SI adjustments ──
            stored = _get_si_adjustment(sample_id)
            if stored:
                return self._run_from_stored(
                    sample_id, stored, run_start
                )

            # ── Attempt 1: Normal CSV values ──
            log.info("┌─ Attempt 1: Normal CSV values")
            self.room_overrides = None
            self.outdoor_overrides = None
            self.occupant_overrides = None

            result = self._execute_quote()
            failure = result.get("Reason_for_quote_failure", "")

            # No failure — clean success
            if not failure:
                _clear_si_adjustment(sample_id)
                elapsed = time.time() - run_start
                log.info(
                    f"✓ COMPLETE — {self.quote_id} | "
                    f"{self.product_name} | "
                    f"{elapsed:.1f}s ({self._request_count} reqs)"
                )
                return result

            # Non-SI failure — return as-is
            if "SumInsuredOutOfRange" not in failure:
                elapsed = time.time() - run_start
                log.error(f"✗ FAILED — {elapsed:.1f}s: {failure[:100]}")
                return result

            # ── SI out of range — start retry chain ──
            original_error = failure
            return self._run_si_retry_chain(
                sample_id, original_error, run_start
            )

        except Exception as e:
            elapsed = time.time() - run_start
            log.error(
                f"✗ UNEXPECTED — {self.quote_id or '?'} "
                f"in {elapsed:.1f}s: {type(e).__name__}: {e}"
            )
            log.debug(traceback.format_exc())
            self.results["Reason_for_quote_failure"] = (
                f"{type(e).__name__}: {e}"
            )
            return self.results

    def _attempt3_occupant_override(
        self, sample_id: str, original_error: str,
        retry1_failure: str, run_start: float,
    ) -> dict:
        log.info(f"{'─' * 60}")
        log.info("┌─ Attempt 3: Contents low SI — Adults=1, Children=0")

        self._reset_state()
        self._reset_results()
        self.occupant_overrides = {"adults": "1", "children": "0"}

        _record_si_adjustment(
            sample_id, phase=3,
            room_overrides=self.room_overrides,
            outdoor_overrides=self.outdoor_overrides,
            occupant_overrides=self.occupant_overrides,
            original_error=original_error,
            retry1_error=retry1_failure,
        )

        result = self._execute_quote()
        retry2_failure = result.get("Reason_for_quote_failure", "")
        self._populate_rerun_columns(result)
        elapsed = time.time() - run_start

        if not retry2_failure:
            log.info(f"✓ COMPLETE (Attempt 3) — {self.quote_id} | {elapsed:.1f}s")
            return result

        if "SumInsuredOutOfRange" in retry2_failure:
            _clear_si_adjustment(sample_id)
        else:
            _record_si_adjustment(
                sample_id, phase=3,
                room_overrides=self.room_overrides,
                outdoor_overrides=self.outdoor_overrides,
                occupant_overrides=self.occupant_overrides,
                original_error=original_error,
                retry1_error=retry1_failure,
                retry2_error=retry2_failure,
            )

        result["Reason_for_quote_failure"] = self._build_failure_message(
            original_error=original_error,
            retry1_error=retry1_failure,
            current_error=retry2_failure,
            phase=3,
        )
        log.error(f"✗ ALL 3 ATTEMPTS FAILED — {elapsed:.1f}s")
        return result

    def _build_failure_message(
        self,
        original_error: str = "",
        retry1_error: str = "",
        retry2_error: str = "",
        current_error: str = "",
        phase: int = 1,
    ) -> str:
        """
        Build complete failure message showing all attempts tried.
        Always includes all phases that were attempted.
        """
        parts = []

        if original_error:
            parts.append(f"ATTEMPT1: {original_error}")

        if retry1_error:
            parts.append(f"ATTEMPT2_ROOMS: {retry1_error}")
        elif phase >= 2 and current_error:
            parts.append(f"ATTEMPT2_ROOMS: {current_error}")
            return " | ".join(parts)

        if retry2_error:
            parts.append(f"ATTEMPT3_OCCUPANTS: {retry2_error}")
        elif phase >= 3 and current_error:
            parts.append(f"ATTEMPT3_OCCUPANTS: {current_error}")
            return " | ".join(parts)

        if current_error and not parts:
            return current_error

        return " | ".join(parts) if parts else current_error

# ═════════════════════════════════════════════════════════════════════════
# Idempotent batch runner
# ═════════════════════════════════════════════════════════════════════════

RETRYABLE_ERRORS = [
    "HTTPError", "ConnectionError", "Timeout", "ConnectionResetError",
    "RemoteDisconnected", "ChunkedEncodingError", "SSLError",
    "ProxyError", "ReadTimeout", "ConnectTimeout", "Request failed after",
    "Failed to get access token", "Zyte cookie fetch failed",
]

def _clean_retryable_from_output(output_path):
    """
    Remove rows with retryable (transient) failures from output CSV
    before processing, so they get fresh results on this run.
    """
    try:
        df = pd.read_csv(output_path, dtype=str)
        if df.empty:
            return

        original_count = len(df)
        keep_rows = []
        retry_rows = []

        for _, row in df.iterrows():
            sid = str(row.get("Sample_Number", "")).strip()
            reason = str(row.get("Reason_for_quote_failure", "")).strip()

            if _is_retryable(reason):
                retry_rows.append((sid, reason[:80]))
            else:
                keep_rows.append(row.to_dict())

        if retry_rows:
            log.info(
                f"  Cleaning {len(retry_rows)} retryable failures "
                f"from output"
            )
            for sid, reason in retry_rows:
                log.info(f"    → Removing Sample {sid}: {reason}")

            clean_df = pd.DataFrame(keep_rows)
            clean_df.to_csv(output_path, index=False)
            log.info(
                f"  Output: {original_count} → {len(keep_rows)} rows"
            )

    except (FileNotFoundError, pd.errors.EmptyDataError):
        pass

def _is_retryable(reason: str) -> bool:
    if not reason or str(reason).strip().lower() in ("", "nan", "none"):
        return False
    return any(p.lower() in str(reason).lower() for p in RETRYABLE_ERRORS)


def _load_completed(output_path):
    try:
        df = pd.read_csv(output_path, dtype=str)
        completed = set()
        retry_count = 0
        for _, row in df.iterrows():
            sid = str(row.get("Sample_Number", "")).strip()
            if not sid:
                continue
            reason = str(row.get("Reason_for_quote_failure", "")).strip()
            if not reason or reason.lower() in ("", "nan", "none"):
                completed.add(sid)  # Success — skip
            elif _is_retryable(reason):
                retry_count += 1     # Transient — will re-run
            else:
                completed.add(sid)  # Permanent failure — skip
        log.info(f"  {len(completed)} done, {retry_count} will retry")
        return completed
    except (FileNotFoundError, pd.errors.EmptyDataError):
        return set()


def _save(existing, new_results, fields_order, output_path):
    merged = {}
    for row in existing:
        sid = str(row.get("Sample_Number", "")).strip()
        if sid:
            merged[sid] = row
    for row in new_results:
        sid = str(row.get("Sample_Number", "")).strip()
        if sid:
            merged[sid] = row
    df = pd.DataFrame(list(merged.values()))
    for col in fields_order:
        if col not in df.columns:
            df[col] = ""
    df[fields_order].to_csv(output_path, index=False)


def run_all_quotes(csv_path, output_path, toggle=False):
    log.info(f"{'=' * 80}")
    log.info(f"NRMA Home Unified Scraper")
    log.info(f"Input: {csv_path}")
    log.info(f"Output: {output_path}")
    log.info(f"Toggle: {'ON' if toggle else 'OFF'}")
    log.info(f"{'=' * 80}")

    _clean_retryable_from_output(output_path)
    completed = _load_completed(output_path)

    existing = []
    try:
        existing = pd.read_csv(output_path, dtype=str).to_dict(
            orient="records"
        )
    except (FileNotFoundError, pd.errors.EmptyDataError):
        pass

    df = pd.read_csv(csv_path).convert_dtypes()
    rows = df.where(pd.notna(df), None).to_dict(orient="records")
    pending = [
        r for r in rows
        if str(r.get("Sample_Number", "")).strip() not in completed
    ]
    log.info(
        f"Total: {len(rows)}, Done: {len(completed)}, "
        f"Pending: {len(pending)}"
    )

    if not pending:
        log.info("Nothing to do!")
        return

    new_results, fields_order = [], None
    ok = fail = 0
    consecutive_failures = 0  # Track for adaptive delay

    for i, row in enumerate(pending):
        sid = row.get("Sample_Number", "?")
        cover = row.get("Cover", "?")
        log.info(
            f"{'─' * 60} {i + 1}/{len(pending)} "
            f"— Sample {sid} ({cover})"
        )

        try:
            ext = NrmaHomeUnifiedExtractor(row, toggle=toggle)
            result = ext.run()
            if not fields_order:
                fields_order = ext.fields_order
            result["Sample_Number"] = sid
            new_results.append(result)

            if result.get("Reason_for_quote_failure"):
                fail += 1
                consecutive_failures += 1
            else:
                ok += 1
                consecutive_failures = 0

        except Exception as e:
            fail += 1
            consecutive_failures += 1
            new_results.append({
                "Sample_Number": sid,
                "Reason_for_quote_failure": f"{type(e).__name__}: {e}",
            })

        # Incremental save
        if new_results and fields_order:
            _save(existing, new_results, fields_order, output_path)

        # ── Adaptive delay between quotes ──
        if i < len(pending) - 1:
            if consecutive_failures >= 3:
                # Multiple failures in a row — likely rate limited
                delay = 30
                log.warning(
                    f"  ⚠ {consecutive_failures} consecutive failures "
                    f"— cooling down {delay}s"
                )
            elif consecutive_failures >= 1:
                delay = 10
            else:
                delay = 3  # Normal pace
            time.sleep(delay)

    log.info(f"{'=' * 80}")
    log.info(f"Done — OK: {ok}, Failed: {fail}, Saved: {output_path}")
    log.info(f"{'=' * 80}")



if __name__ == "__main__":
    import argparse

    # parser = argparse.ArgumentParser(
    #     description="NRMA Home Insurance Unified Scraper"
    # )
    # parser.add_argument(
    #     "-i", "--input", required=True, help="Input CSV path"
    # )
    # parser.add_argument(
    #     "-o", "--output", required=True, help="Output CSV path"
    # )
    # parser.add_argument(
    #     "-t", "--toggle", action="store_true",
    #     help="Enable toggle mode (excess matrix + extras)"
    # )
    # args = parser.parse_args()

    run_all_quotes(
        csv_path="/home/wmc/WMC/DataManipulation/Suncorp/NRMA/inputs/202608/data/Combined_NRMA_quarterly_202608.csv",
        output_path="/home/wmc/WMC/DataManipulation/Suncorp/NRMA/outputs/202608/Combined_NRMA_quarterly_202608.csv",
        toggle=True,
    )






    # run_all_quotes(
    #     csv_path="/home/wmc/WMC/DataManipulation/Suncorp/NRMA/inputs/test/data/Combined_NRMA_quarterly_202605.csv",
    #     output_path="/home/wmc/WMC/DataManipulation/Suncorp/NRMA/outputs/test/Combined_NRMA_quarterly_202605_final_again.csv",
    #     toggle=False,
    # )