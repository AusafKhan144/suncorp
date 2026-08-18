from datetime import date, datetime
import base64
import json
import logging
import time
import traceback
import uuid
from urllib.parse import urlparse
import pandas as pd
import requests
import urllib3



urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(levelname)-7s │ %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("NrmaExtractor")


# ─────────────────────────────────────────────────────────────────────────────
# Zyte transport — every NRMA request is routed through the Zyte API so it
# carries a real browser fingerprint. Cookies are passed via
# experimental.requestCookies and returned via experimental.responseCookies,
# never through raw headers (Zyte ignores cookie headers).
# ─────────────────────────────────────────────────────────────────────────────
ZYTE_API_KEY = "d5f27f9a318349a599774d39b68f0552"
ZYTE_API_URL = "https://api.zyte.com/v1/extract"
ZYTE_RETRY_ATTEMPTS = 4
ZYTE_RETRY_DELAY = 5.0
# Zyte-API-level statuses worth retrying. 520 = /download/temporary-error
# (target returned an error / anti-bot reset the connection — usually transient),
# 521 = /download/internal-error, 429 = rate limit, 500/503 = Zyte internal.
ZYTE_RETRYABLE_STATUS = {429, 500, 503, 520, 521, 522}

# Akamai's Bot Manager only validates `_abck` (`-1` → `0`) AFTER a real click on
# the "continue / login" interstitial NRMA shows on first load — a passive wait
# isn't enough, the click is the interaction event the sensor needs. So the
# bootstrap render must wait for that button, click it, then let the sensor post
# its (encrypted, behavioural) sensor_data. We never build that blob ourselves —
# the real Zyte browser generates and submits it.
#
# XPath matches the "continue without logging in" guest button (case-insensitive),
# the distinctive "without logging in" text avoids matching any login-path
# "Continue" button. Retarget CONTINUE_BUTTON_XPATH if the label/markup differs.
CONTINUE_BUTTON_XPATH = (
    "//*[self::button or self::a or @role='button']"
    "[contains(translate(normalize-space(.),"
    " 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'),"
    " 'without logging in')]"
)
ZYTE_BOOTSTRAP_ACTIONS = [
    {"action": "waitForTimeout", "timeout": 4},
    {
        "action": "waitForSelector",
        "selector": {"type": "xpath", "value": CONTINUE_BUTTON_XPATH},
        "timeout": 15,
    },
    {
        "action": "click",
        "selector": {"type": "xpath", "value": CONTINUE_BUTTON_XPATH},
    },
    {"action": "waitForTimeout", "timeout": 5},
    {"action": "scrollBottom"},
    {"action": "waitForTimeout", "timeout": 4},
]

# Akamai's Cryptographic Challenge (sec-cpt): on a high-risk request the edge
# returns HTTP 428 {"sec-cp-challenge":"true","provider":"crypto",...} demanding
# the client fetch a challenge, solve a CPU proof-of-work and submit it to earn a
# `sec_cpt` cookie. A real browser's Akamai JS does this transparently; our raw
# httpResponseBody XHR can't. So when we hit it we render a page in the browser
# (which runs the solver and sets sec_cpt) and carry that cookie into the API
# calls. These actions just give the proof-of-work time to run and reload.
MAX_SEC_CPT_SOLVES = 2
ZYTE_SEC_CPT_ACTIONS = [
    {"action": "waitForTimeout", "timeout": 10},
    {"action": "scrollBottom"},
    {"action": "waitForTimeout", "timeout": 5},
]


def _cookie_domain(url: str) -> str:
    return urlparse(url).netloc


def _abck_validated(abck: str | None) -> bool:
    """Akamai Bot Manager marks a session in the `_abck` cookie: the second
    `~`-delimited field is `-1` while the sensor has NOT been accepted (treated
    as a bot) and flips to `0` once validated. Anything that isn't `-1` (and is
    a real multi-segment cookie) counts as validated/passed.
    """
    if not abck:
        return False
    parts = abck.split("~")
    return len(parts) > 1 and parts[1] != "-1"


class ZyteError(RuntimeError):
    """A non-200 from the Zyte API itself, carrying the decoded problem detail."""

    def __init__(self, status: int, type_: str, title: str, detail: str):
        self.status = status
        self.type = type_
        self.title = title
        self.detail = detail
        super().__init__(f"Zyte {status} {type_ or title}: {detail}")


class ZyteSession:
    def __init__(self) -> None:
        self.client = requests.Session()
        self.auth = (ZYTE_API_KEY, "")
        # Client-managed Zyte session: every request tagged with this id stays on
        # the SAME IP + cookie jar and Zyte will NOT rotate proxies mid-session.
        # One id per quote keeps our validated Akamai cookies (_abck/sec_cpt)
        # bound to a single IP, so they aren't re-challenged and Zyte doesn't
        # spiral into /download/website-ban. Rotate the id to start fresh.
        self.session_id = str(uuid.uuid4())

    def new_session(self) -> str:
        """Start a fresh Zyte session (new IP) — call after a website-ban."""
        self.session_id = str(uuid.uuid4())
        return self.session_id

    def request(
        self,
        url: str,
        method: str = "GET",
        headers: dict | None = None,
        body: str | None = None,
        cookies: dict | None = None,
    ) -> tuple[int, list[tuple[str, str]], str, list[dict]]:
        """Execute a request via Zyte. Returns
        (status_code, response_headers, body_text, experimental_response_cookies).
        """
        payload: dict = {
            "url": url,
            "httpResponseBody": True,
            "httpResponseHeaders": True,
            "httpRequestMethod": method,
            "geoLocation": "AU",
            "session": {"id": self.session_id},
            "experimental": {"responseCookies": True},
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

        data = self._execute(payload, url)
        raw_body = base64.b64decode(data.get("httpResponseBody", "")).decode(
            "utf-8", errors="replace"
        )
        status = data.get("statusCode", 200)
        resp_headers = [
            (h["name"].lower(), h["value"])
            for h in data.get("httpResponseHeaders", [])
        ]
        exp_cookies = data.get("experimental", {}).get("responseCookies", [])
        return status, resp_headers, raw_body, exp_cookies

    def browser_request(
        self,
        url: str,
        cookies: dict | None = None,
        actions: list[dict] | None = None,
    ) -> tuple[int, str, list[dict]]:
        """Render `url` in a real browser so page JavaScript (incl. the Akamai
        Bot Manager sensor) executes and returns a *validated* cookie jar.
        `actions` are browser steps (waits/scrolls) that give the sensor time and
        interaction to post its sensor_data and validate `_abck`.
        Returns (status_code, browser_html, experimental_response_cookies).
        """
        payload: dict = {
            "url": url,
            "browserHtml": True,
            "geoLocation": "AU",
            "session": {"id": self.session_id},
            "experimental": {"responseCookies": True},
        }
        if actions:
            payload["actions"] = actions
        if cookies:
            payload["experimental"]["requestCookies"] = [
                {"name": k, "value": v, "domain": _cookie_domain(url)}
                for k, v in cookies.items()
                if v
            ]
        data = self._execute(payload, url)
        status = data.get("statusCode", 200)
        html = data.get("browserHtml", "") or ""
        exp_cookies = data.get("experimental", {}).get("responseCookies", [])
        return status, html, exp_cookies

    def _execute(self, payload: dict, url: str) -> dict:
        """POST a payload to the Zyte API with transport + Zyte-error retries.
        Returns the parsed Zyte response dict, or raises ZyteError/RuntimeError.
        """
        for attempt in range(1, ZYTE_RETRY_ATTEMPTS + 1):
            try:
                resp = self.client.post(
                    ZYTE_API_URL, json=payload, auth=self.auth, timeout=90
                )
            except requests.exceptions.RequestException as exc:
                # Network-level failure talking to api.zyte.com itself.
                if attempt == ZYTE_RETRY_ATTEMPTS:
                    raise
                log.warning(
                    "  [Zyte] transport error %d/%d: %s — retrying in %.0fs",
                    attempt, ZYTE_RETRY_ATTEMPTS, exc, ZYTE_RETRY_DELAY * attempt,
                )
                time.sleep(ZYTE_RETRY_DELAY * attempt)
                continue

            if resp.status_code != 200:
                # Zyte returns RFC-7807 problem-detail JSON on its own errors.
                err = self._decode_zyte_error(resp)
                # A website-ban means this session's IP is burned — retrying on
                # the same pinned session won't help and just wastes time/credits.
                # Fail fast so the batch runner re-runs the quote on a fresh
                # session (new IP + fresh Akamai bootstrap).
                is_ban = "website-ban" in (err.type or "")
                if (
                    err.status in ZYTE_RETRYABLE_STATUS
                    and not is_ban
                    and attempt < ZYTE_RETRY_ATTEMPTS
                ):
                    log.warning(
                        "  [Zyte] %s (%d/%d) for %s — retrying in %.0fs",
                        err, attempt, ZYTE_RETRY_ATTEMPTS, _cookie_domain(url),
                        ZYTE_RETRY_DELAY * attempt,
                    )
                    time.sleep(ZYTE_RETRY_DELAY * attempt)
                    continue
                log.error("  [Zyte] %s for %s", err, url)
                raise err

            return resp.json()
        raise RuntimeError("All Zyte retries exhausted")

    @staticmethod
    def _decode_zyte_error(resp: requests.Response) -> "ZyteError":
        """Parse the Zyte API error response into a ZyteError.

        Zyte error bodies follow RFC 7807, e.g.:
          {"status": 520, "type": "/download/temporary-error",
           "title": "Temporary downloading error", "detail": "..."}
        """
        try:
            body = resp.json()
        except ValueError:
            body = {}
        status = body.get("status", resp.status_code)
        type_ = body.get("type", "")
        title = body.get("title", "")
        detail = body.get("detail") or title or (resp.text or "")[:300]
        return ZyteError(status, type_, title, detail)


class NrmaExtractor:
    BASE_URL = "https://insurance.nrma.com.au"
    API_PATH = "/domain/question/api/question/api/1.x.x"
    MOTOR_LANDING = (
        "https://insurance.nrma.com.au/get-cover/motor/3?"
        "serenityproduct=car-comprehensive-standard"
        "&serenitydefaultstate=NSW&serenityselectedstate=NSW"
    )
    MOTOR_REFERER = MOTOR_LANDING
    USER_AGENT = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
    )

    GARAGE_TYPE_MAPPING = {
        "Carport": "CARPORT",
        "Garage or secure parking lot": "GARAGE",
        "Driveway or off-street parking": "DRIVEWAY_OFF_STREET",
        "other": "STREET",
    }
    VEHICLE_USE_MAPPING = {
        "Private use": "PRIVATE_USE",
        "Trades": "TRADE",
        "Rideshare (e.g. Uber or Ola)": "RIDE_SHARE",
        "Food delivery (like UberEats or Deliveroo)": "TAKE_AWAY_FOOD_DELIVERY",
        "Courier delivery": "COURIER_OR_DELIVERY",
        "Taxi or people transportation": "TAXI_OR_PEOPLE_TRANSPORTATION",
        "Farming activities": "FARMING_ACTIVITIES",
        "Car sharing (like Drive mate)": "CAR_SHARING",
        "Hire or rental": "HIRE_OR_RENT",
        "Driving instruction": "DRIVING_INSTRUCTOR",
        "Professional motorsport": "PROFESSIONAL_MOTOR_SPORT",
        "Other business activities": "ALL_OTHER_BUSINESS",
    }
    YEARLY_KM_MAPPING = {
        "0 - 5000kms": "LESS_THAN_5K",
        "5001 - 10,000kms": "GREATER_THAN_5K_LESS_THAN_10K",
        "10,001 - 15,000kms": "GREATER_THAN_10K_LESS_THAN_15K",
        "15,001 - 20,000kms": "GREATER_THAN_15K_LESS_THAN_20K",
        "More than 20,000kms": "MORE_THAN_20K",
    }
    INSURERS = {
        "Budget Direct": "BUDGET_DIRECT",
        "AAMI": "AAMI",
        "Hollard": "HOLLARD_INSURANCE",
        "Allianz": "ALLIANZ_AUSTRALIA",
        "Suncorp Insurance": "SUNCORP",
        "NRMA": "NRMA_INSURANCE",
        "Guild Insurance": "GUILD_INSURANCE",
        "RAC": "RACV_INSURANCE",
        "RACT": "RACT_INSURANCE",
        "TIO": "TIO_INSURANCE",
        "RAA Insurance": "RAA_INSURANCE",
        "QBE": "QBE_INSURANCE_GROUP",
        "RACQ": "RACQ_INSURANCE",
        "Other": "OTHER",
        "Not currently insured": "NOT_CURRENTLY_INSURED",
        "GIO": "GIO",
        "YOUI": "YOUI",
        "BUDGET DIRECT": "BUDGET_DIRECT",
        "SUNCORP": "SUNCORP",
        "NOT INSURED": "NOT_CURRENTLY_INSURED",
    }
    INSURER_TENURE = {
        "More than 10 years": "MORE_THAN_10_YEARS",
        "1 to 3 years": "1_TO_3_YEARS",
        "3 to 5 years": "3_TO_5_YEARS",
        "Less than 1 year": "LESS_THAN_1_YEAR",
        "5 to 10 years": "5_TO_10_YEARS",
    }
    COVER_TYPES = {
        "COMPREHENSIVE_BASICS": "NRMABasic",
        "COMPREHENSIVE": "NRMA",
        "COMPREHENSIVE_PLUS": "NRMAPlus",
    }
    EXTRAS_MAPPING = {
        "glass_excess_waiver": "Windscreen",
        "hire_vehicle_after_incident": "HireCar",
        "choice_of_repairer": "ChoiceOfRepairer",
    }
    CLAIM_TYPE_MAPPING = {
        "At fault - Loss or damage to your car": "COLLISION",
        "At fault - Loss or damage to other person's property or car": "AT_FAULT_THIRD_PARTY",
        "Not at fault": "NOT_AT_FAULT",
        "Theft": "THEFT",
        "Weather / Storm / Flood / Hail": "WEATHER_STORM",
        "Fire": "FIRE",
        "Windscreen / Glass": "GLASS",
        "Collision": "COLLISION",
        "COLLISION": "COLLISION",
        "THEFT": "THEFT",
        "NOT_AT_FAULT": "NOT_AT_FAULT",
        "AT_FAULT_THIRD_PARTY": "AT_FAULT_THIRD_PARTY",
        "WEATHER_STORM": "WEATHER_STORM",
        "FIRE": "FIRE",
        "GLASS": "GLASS",
    }
    BRAND_CONFIG = {
        "pages": [],
        "features": {
            "isWebchatEnabled": True,
            "webchatAvailabilityBaseUrl": "https://www.insuranceonline.nrma.com.au/CONAV/rest/api/availability",
            "webchatBaseUrl": "https://www.insuranceonline.nrma.com.au/oss/livechat/login.html?",
            "useExp3SessionTimeout": True,
            "hideFortnightlyPaymentFromQuoteCreationDate": "2025-11-16T00:00:00.000Z",
            "enableRoadsideAssistanceSouthAustralia": True,
            "removePriceQueryCache": True,
            "isRtdeEnabled": True,
            "showRsaPageInQuoteFlow": True,
            "enableCurrentInsurer": True,
            "addLogoutRedirectToSessionTimeout": False,
            "byeStateMachine": False,
            "enableAnonStartPage": False,
        },
        "brand": "NRMA",
        "division": "au",
        "product": "motor",
        "config": {
            "env": "prdnoauth",
            "REACT_APP_ENABLED_WEBCHAT_BRANDS": "nrma",
            "REACT_APP_IS_WEBCHAT_ENABLED": "true",
            "REACT_APP_ENABLED_IDENTITY_BRANDS": "asb,anz,bnz,nrma,state,ami",
            "REACT_APP_IS_IDENTITY_MFE_ENABLED": "true",
            "REACT_APP_API_BASE_PATH": "/domain/question/api/question/api/1.x.x",
            "REACT_APP_API_BASE_PATH_V2": "/domain/question/api/question/api/2.x.x",
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
            "REACT_APP_BUNDLED_PRODUCT_API_PATH": "/domain/quote/api/bundled-product",
            "REACT_APP_NRMA_REFRESH_ACCESS_TOKEN_HOST": "https://connect.nrma.com.au",
        },
        "brandVariables": {
            "CONTACT_NUMBER": "132 132",
            "HOURS_VIA_WEBSITE": "8am-6pm Mon-Fri, 9am-6pm Sat-Sun & public holidays",
            "URL": "https://www.nrma.com.au",
            "MY_NRMA_URL": "https://www.mynrma.com.au",
            "ONLINE_ACCOUNT_URL": "https://onlineaccount.nrma.com.au",
            "ONLINE_LOGIN_URL": "/welcome/login/username",
            "EP_REDIRECT_URL": "/get-cover/home",
            "FIND_AN_INSURER_URL": "https://www.findaninsurer.com.au",
        },
        "underwritingConfig": {
            "VR0094": {"value": None, "ruleType": "NULL_VALUE"},
            "VR0292": {"value": 250000, "ruleType": "MAX_VALUE"},
            "VR0030": {"value": ["6798", "6799", "2899", "7151"], "ruleType": "EXCLUDE_LIST"},
            "VR0099": {"ruleType": "EXCLUDE_VALUE", "value": "OT"},
            "VR0270": {"value": "VIC", "ruleType": "EXCLUDE_VALUE"},
            "VRPM0001": {"value": 18, "ruleType": "MIN_VALUE"},
            "VR0006": {"value": {"sumInsuredType": "MARKET_VALUE", "damageType": ["HAIL_DAMAGE", "MORE_THAN_MINOR_DAMAGE"], "coverTypes": ["COMPREHENSIVE", "COMPREHENSIVE_BASICS", "COMPREHENSIVE_PLUS", "THIRD_PARTY_FIRE_AND_THEFT"]}, "ruleType": "DAMAGES"},
            "VR0310": {"value": "CUSTOM", "ruleType": "EXCLUDE_VALUE"},
            "VR0302": {"value": "ROLL_CAGE", "ruleType": "EXCLUDE_VALUE"},
            "VR0303": {"value": "SIGNIFICANT_SUSPENSION_ADJUSTMENT", "ruleType": "EXCLUDE_VALUE"},
            "VR0304": {"value": "OTHER_ENGINE", "ruleType": "EXCLUDE_VALUE"},
            "VR0036": {"value": True, "ruleType": "EXCLUDE_VALUE"},
            "VR0037": {"value": True, "ruleType": "EXCLUDE_VALUE"},
            "VR1001": {"value": "today", "ruleType": "MIN_DATE"},
        },
        "initialUrl": "https://insurance.nrma.com.au/get-cover/motor/3?serenityproduct=car-comprehensive-standard&serenitydefaultstate=NSW&serenityselectedstate=NSW",
    }

    RETRY_MAX_ATTEMPTS = 3
    RETRY_BACKOFF_BASE = 2
    RETRY_ON_STATUS = {429, 500, 502, 503, 504}
    # Inline recovery for the transient 401 that hits the first API call when the
    # freshly minted token hasn't propagated yet — re-mint + retry on the same
    # session instead of failing the whole quote.
    MAX_AUTH_RETRIES = 2

    # ─────────────────────────────────────────────────────────────────────────
    def __init__(self, input_data: dict, toggle: bool = False) -> None:
        self.input_data = input_data
        self.toggle = toggle
        self.quote_id = None
        self.access_token = None
        self.session_access_token = None
        # Cached quote signature/expiry so the token can be re-minted on a 401.
        self._quote_signature = None
        self._quote_expiry = None
        self.vehicle_info = None
        self.variant_search = None
        self.fmt = None
        self.matched_address = None
        self.parking_mapped = None
        self.yearly_km_mapped = None
        self.vehicle_use_mapped = None
        self.registered_gst = False
        self.hours_for_business = None
        self.input_tax_claim = None
        self.finance_info = False
        self.held = False
        self.insurer = None
        self.tenure = None
        self.default_excess = None
        self._available_excesses = []
        self.owner_type = None
        self.drive = False
        self.membership = False
        self.gender = None
        self.dob = None
        self.party_id = None
        self.temp_id = None
        self.add_second_driver = None
        self.second_owner_type = None
        self.second_drive = False
        self.second_membership = False
        self.second_gender = None
        self.second_dob = None
        self.party_id_second = None
        self.temp_id_second = None
        self._primary_claim_list = []
        self._second_claim_list = []
        self._request_count = 0
        self._total_request_time = 0.0
        self._agreed_value_low = None
        self._agreed_value_high = None
        self._agreed_value_avg = None
        self.zyte = ZyteSession()
        self.session_id = str(uuid.uuid4())
        self.cookies: dict = {}

        if self.toggle:
            self.fields_order = [
                "Sample_Number", "DOB_DD", "DOB_MM", "DOB_YYYY", "Postcode",
                "QuoteLink", "QuoteDate", "Timestamp (in hh:mm)", "QuoteNumber",
                "NRMABasic_DefaultExcess", "NRMABasic_FortnightlyPayment",
                "NRMABasic_MonthlyPayment", "NRMABasic_QuarterlyPayment",
                "NRMABasic_YearlyPayment",
                "NRMA_DefaultExcess", "NRMA_FortnightlyPayment",
                "NRMA_MonthlyPayment", "NRMA_QuarterlyPayment", "NRMA_YearlyPayment",
                "NRMA_Windscreen", "NRMA_HireCar", "NRMA_ChoiceOfRepairer",
                "NRMA_RoadsideAssist",
                "NRMA_excess_100", "NRMA_excess_150", "NRMA_excess_200",
                "NRMA_excess_250", "NRMA_excess_300", "NRMA_excess_350",
                "NRMA_excess_400", "NRMA_excess_450", "NRMA_excess_500",
                "NRMA_excess_550", "NRMA_excess_600", "NRMA_excess_650",
                "NRMA_excess_700", "NRMA_excess_750", "NRMA_excess_800",
                "NRMA_excess_850", "NRMA_excess_900", "NRMA_excess_950",
                "NRMA_excess_1000", "NRMA_excess_1050", "NRMA_excess_1100",
                "NRMA_excess_1150", "NRMA_excess_1200", "NRMA_excess_1250",
                "NRMA_excess_1300", "NRMA_excess_1350", "NRMA_excess_1400",
                "NRMA_excess_1450", "NRMA_excess_1500", "NRMA_excess_1550",
                "NRMA_excess_1600", "NRMA_excess_1650", "NRMA_excess_1700",
                "NRMA_excess_1750", "NRMA_excess_1800", "NRMA_excess_1850",
                "NRMA_excess_1900", "NRMA_excess_1950", "NRMA_excess_2000",
                "NRMA_excess_2050", "NRMA_excess_2100", "NRMA_excess_2150",
                "NRMA_excess_2200", "NRMA_excess_2250", "NRMA_excess_2300",
                "NRMA_excess_2350", "NRMA_excess_2400", "NRMA_excess_2450",
                "NRMA_excess_2500", "NRMA_excess_2550", "NRMA_excess_2600",
                "NRMA_excess_2650", "NRMA_excess_2700", "NRMA_excess_2750",
                "NRMA_excess_2800", "NRMA_excess_2850", "NRMA_excess_2900",
                "NRMA_excess_2950", "NRMA_excess_3000",
                "NRMAPlus_DefaultExcess", "NRMAPlus_FortnightlyPayment",
                "NRMAPlus_MonthlyPayment", "NRMAPlus_QuarterlyPayment",
                "NRMAPlus_YearlyPayment",
                "NRMAPlus_AgreedValue", "NRMAPlus_AgreedValue_Low",
                "NRMAPlus_AgreedValue_High",
                "NRMAPlus_Windscreen", "NRMAPlus_HireCar",
                "NRMAPlus_ChoiceOfRepairer", "NRMAPlus_RoadsideAssist",
                "NRMAPlus_excess_100", "NRMAPlus_excess_150", "NRMAPlus_excess_200",
                "NRMAPlus_excess_250", "NRMAPlus_excess_300", "NRMAPlus_excess_350",
                "NRMAPlus_excess_400", "NRMAPlus_excess_450", "NRMAPlus_excess_500",
                "NRMAPlus_excess_550", "NRMAPlus_excess_600", "NRMAPlus_excess_650",
                "NRMAPlus_excess_700", "NRMAPlus_excess_750", "NRMAPlus_excess_800",
                "NRMAPlus_excess_850", "NRMAPlus_excess_900", "NRMAPlus_excess_950",
                "NRMAPlus_excess_1000", "NRMAPlus_excess_1050", "NRMAPlus_excess_1100",
                "NRMAPlus_excess_1150", "NRMAPlus_excess_1200", "NRMAPlus_excess_1250",
                "NRMAPlus_excess_1300", "NRMAPlus_excess_1350", "NRMAPlus_excess_1400",
                "NRMAPlus_excess_1450", "NRMAPlus_excess_1500", "NRMAPlus_excess_1550",
                "NRMAPlus_excess_1600", "NRMAPlus_excess_1650", "NRMAPlus_excess_1700",
                "NRMAPlus_excess_1750", "NRMAPlus_excess_1800", "NRMAPlus_excess_1850",
                "NRMAPlus_excess_1900", "NRMAPlus_excess_1950", "NRMAPlus_excess_2000",
                "NRMAPlus_excess_2050", "NRMAPlus_excess_2100", "NRMAPlus_excess_2150",
                "NRMAPlus_excess_2200", "NRMAPlus_excess_2250", "NRMAPlus_excess_2300",
                "NRMAPlus_excess_2350", "NRMAPlus_excess_2400", "NRMAPlus_excess_2450",
                "NRMAPlus_excess_2500", "NRMAPlus_excess_2550", "NRMAPlus_excess_2600",
                "NRMAPlus_excess_2650", "NRMAPlus_excess_2700", "NRMAPlus_excess_2750",
                "NRMAPlus_excess_2800", "NRMAPlus_excess_2850", "NRMAPlus_excess_2900",
                "NRMAPlus_excess_2950", "NRMAPlus_excess_3000",
                "Reason_for_quote_failure",
            ]
        else:
            self.fields_order = [
                "Sample_Number", "DOB_DD", "DOB_MM", "DOB_YYYY", "Postcode",
                "QuoteLink", "QuoteDate", "Timestamp (in hh:mm)", "QuoteNumber",
                "NRMABasic_DefaultExcess", "NRMABasic_FortnightlyPayment",
                "NRMABasic_MonthlyPayment", "NRMABasic_QuarterlyPayment",
                "NRMABasic_YearlyPayment",
                "NRMA_DefaultExcess", "NRMA_FortnightlyPayment",
                "NRMA_MonthlyPayment", "NRMA_QuarterlyPayment", "NRMA_YearlyPayment",
                "NRMAPlus_DefaultExcess", "NRMAPlus_AgreedValue",
                "NRMAPlus_AgreedValue_Low", "NRMAPlus_AgreedValue_High",
                "NRMAPlus_FortnightlyPayment", "NRMAPlus_MonthlyPayment",
                "NRMAPlus_QuarterlyPayment", "NRMAPlus_YearlyPayment",
                "Reason_for_quote_failure",
            ]

        self.results: dict[str, str | None] = {k: "" for k in self.fields_order}
        self.results.update({
            "Sample_Number": input_data.get("Sample_Number"),
            "DOB_DD": input_data.get("DOB_DD_dummy"),
            "DOB_MM": input_data.get("DOB_MM_dummy"),
            "DOB_YYYY": input_data.get("DOB_YYYY_dummy"),
            "Postcode": input_data.get("Postcode"),
            "QuoteDate": time.strftime("%d-%b-%y"),
            "Timestamp (in hh:mm)": time.strftime("%H:%M"),
        })
        sample = input_data.get("Sample_Number", "?")
        log.info(f"{'=' * 80}")
        log.info(
            f"INIT — Sample {sample} | {input_data.get('gg_YEAR')} "
            f"{input_data.get('gg_MAKE')} {input_data.get('gg_FAMILY')}"
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Cookie jar — captured from every Zyte response
    # ─────────────────────────────────────────────────────────────────────────
    def _capture_auth_tokens(self, k: str, v: str):
        if k == "x-iag-auth-session":
            self.session_access_token = v
        elif k == "x-iag-customer":
            self.access_token = v

    def _store_cookie(self, k: str, v: str):
        # Never let a raw (non-JS) response downgrade a validated Akamai cookie
        # back to the `-1` bot state — that would re-flag the whole session.
        if (
            k == "_abck"
            and _abck_validated(self.cookies.get("_abck"))
            and not _abck_validated(v)
        ):
            return
        self.cookies[k] = v
        self._capture_auth_tokens(k, v)

    def _update_cookies_from_headers(self, resp_headers: list[tuple[str, str]]):
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
            self._store_cookie(k, v)

    def _update_cookies_from_experimental(self, experimental_cookies: list[dict]):
        for cookie in experimental_cookies:
            k = cookie.get("name", "").strip()
            v = cookie.get("value", "").strip()
            if not k:
                continue
            self._store_cookie(k, v)

    def _update_all_cookies(
        self, resp_headers: list[tuple[str, str]], experimental_cookies: list[dict]
    ):
        self._update_cookies_from_headers(resp_headers)
        # Experimental takes priority — run second to overwrite on conflict.
        self._update_cookies_from_experimental(experimental_cookies)

    # ─────────────────────────────────────────────────────────────────────────
    # Headers — no cookie header here; cookies are passed to ZyteSession
    # ─────────────────────────────────────────────────────────────────────────
    def _base_headers(self, referer: str = "") -> dict:
        h = {
            "accept": "application/json, text/plain, */*",
            "accept-language": "en-US,en;q=0.9",
            "cache-control": "no-cache",
            "pragma": "no-cache",
            "sec-ch-ua": '"Not)A;Brand";v="8", "Chromium";v="138", "Google Chrome";v="138"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Linux"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "user-agent": self.USER_AGENT,
            "x-iag-consumer-system": "RETAIL_WEB",
            "x-iag-session-id": self.session_id,
        }
        if referer:
            h["referer"] = referer
        if self.session_access_token:
            h["x-iag-auth-session"] = self.session_access_token
        if self.access_token:
            h["x-iag-customer"] = self.access_token
        return h

    def _json_headers(self, referer: str = "") -> dict:
        h = self._base_headers(referer)
        h["content-type"] = "application/json"
        h["origin"] = self.BASE_URL
        return h

    # ─────────────────────────────────────────────────────────────────────────
    # Core request — routed through Zyte, with light retry on transient status
    # ─────────────────────────────────────────────────────────────────────────
    def _request(self, url: str, method: str, payload: dict | None,
                headers: dict | None = None,
                cookies: dict | None = None) -> dict:
        hdrs = headers or self._json_headers(self.MOTOR_REFERER)
        # When the caller relies on the shared jar, keep pointing at it so a
        # mid-request sec_cpt/_abck refresh is picked up on the retry.
        use_jar = cookies is None
        ckies = self.cookies if use_jar else cookies
        body = json.dumps(payload) if payload is not None else None
        self._request_count += 1
        req_num = self._request_count
        short = url.split("/api/")[-1] if "/api/" in url else url.split(".au/")[-1]

        transient_retries = 0
        challenge_solves = 0
        auth_retries = 0
        while True:
            start = time.time()
            status, resp_headers, resp_body, exp_cookies = self.zyte.request(
                url, method=method, headers=hdrs, body=body,
                cookies=self.cookies if use_jar else ckies,
            )
            self._total_request_time += time.time() - start
            self._update_all_cookies(resp_headers, exp_cookies)

            # Transient 401 — the freshly minted token hasn't propagated yet.
            # Re-mint it and retry on the same session before failing the quote.
            if status == 401 and auth_retries < self.MAX_AUTH_RETRIES:
                auth_retries += 1
                log.warning(
                    f"  [REQ #{req_num}] HTTP 401 — re-minting token & retrying "
                    f"({auth_retries}/{self.MAX_AUTH_RETRIES}) — {short}"
                )
                self._refresh_access_token()
                hdrs = headers or self._json_headers(self.MOTOR_REFERER)
                time.sleep(self.RETRY_BACKOFF_BASE ** auth_retries)
                continue

            # Akamai crypto challenge — solve via browser, then retry the call.
            if status == 428 and "sec-cp-challenge" in resp_body:
                if challenge_solves < MAX_SEC_CPT_SOLVES:
                    challenge_solves += 1
                    log.warning(
                        f"  [REQ #{req_num}] Akamai sec-cp challenge "
                        f"({challenge_solves}/{MAX_SEC_CPT_SOLVES}) — solving — {short}"
                    )
                    self._solve_sec_cpt()
                    continue
                log.error(
                    f"  [REQ #{req_num}] sec-cp challenge unsolved after "
                    f"{challenge_solves} attempt(s) — {short}"
                )
                raise RuntimeError(f"HTTP 428 sec-cp-challenge — {short}")

            if status in self.RETRY_ON_STATUS:
                if transient_retries < self.RETRY_MAX_ATTEMPTS - 1:
                    transient_retries += 1
                    log.warning(
                        f"  [REQ #{req_num}] HTTP {status} "
                        f"({transient_retries}/{self.RETRY_MAX_ATTEMPTS - 1}) — {short}"
                    )
                    time.sleep(self.RETRY_BACKOFF_BASE ** transient_retries)
                    continue
                log.warning(f"  [REQ #{req_num}] HTTP {status} — {short}")
                raise RuntimeError(f"HTTP {status} — {short}: {resp_body[:300]}")

            if status >= 400:
                log.warning(f"  [REQ #{req_num}] HTTP {status} — {short}")
                log.warning(f"  [REQ #{req_num}] {resp_body[:500]}")
                raise RuntimeError(f"HTTP {status} — {short}: {resp_body[:300]}")

            if transient_retries or challenge_solves:
                log.info(f"  [REQ #{req_num}] ✓ Recovered — {short}")
            return json.loads(resp_body) if resp_body.strip() else {}

    def _solve_sec_cpt(self):
        """Render the quote page in a browser so Akamai's crypto-challenge JS
        runs the proof-of-work and sets the `sec_cpt` cookie, then carry it into
        the API jar. The challenge is what gates the parties/offer endpoints.
        """
        url = f"{self.BASE_URL}/get-cover/motor/3?quoteId={self.quote_id}"
        had = "sec_cpt" in self.cookies
        try:
            _status, _html, exp_cookies = self.zyte.browser_request(
                url, cookies=self.cookies, actions=ZYTE_SEC_CPT_ACTIONS,
            )
        except ZyteError as e:
            log.warning(f"  [Akamai] sec-cpt render failed: {e}")
            return
        self._update_cookies_from_experimental(exp_cookies)
        now = self.cookies.get("sec_cpt")
        state = "refreshed" if had else ("set" if now else "NOT set")
        log.info(f"  [Akamai] sec_cpt {state} (cookies={len(self.cookies)})")

    def _api(self, path: str) -> str:
        return f"{self.BASE_URL}{self.API_PATH}/{path}"

    def _log_excess_diagnostic(self, resp: dict, source: str):
        """Dump the response shape when BaseExcess is missing, to tell a
        *degraded/declined* quote (validation/referral errors, sparse keys)
        apart from a response that simply lacks the excess list.
        """
        novus = resp.get("novusContext", {}) if isinstance(resp, dict) else {}
        conditions = novus.get("conditions", {})
        log.warning(
            f"│  [diag/{source}] top-level keys: {sorted(resp.keys())}"
        )
        log.warning(
            f"│  [diag/{source}] novusContext keys: {sorted(novus.keys())}"
        )
        log.warning(
            f"│  [diag/{source}] conditions keys: {sorted(conditions.keys())}"
        )
        # Surface anything that looks like a decline/referral/validation message.
        for key in (
            "validationErrors", "validationResults", "errors", "messages",
            "referralReasons", "declineReasons", "underwritingResults",
            "ruleResults", "blockingReasons",
        ):
            for holder, label in ((resp, "root"), (novus, "novus")):
                val = holder.get(key)
                if val:
                    log.warning(
                        f"│  [diag/{source}] {label}.{key}: {str(val)[:400]}"
                    )

    # ─────────────────────────────────────────────────────────────────────────
    # Conditions helpers
    # ─────────────────────────────────────────────────────────────────────────
    def _update_conditions_from_response(self, resp: dict, source: str = ""):
        novus = resp.get("novusContext", {})
        conditions = novus.get("conditions", {})
        excesses = conditions.get("BaseExcess", [])
        if excesses:
            new_default = self._closest_to_1000(excesses)
            if self._available_excesses != excesses:
                log.info(
                    f"  [{source}] BaseExcess updated: {excesses} → default={new_default}"
                )
            self._available_excesses = excesses
            self.default_excess = new_default
        av = novus.get("ActualVehicleAgreedValue", {})
        if not av:
            av = conditions.get("CarAgreedValue", {})
        if av.get("min") and av.get("max") and not self._agreed_value_avg:
            self._agreed_value_low = av.get("min")
            self._agreed_value_high = av.get("max")
            self._agreed_value_avg = self._compute_agreed_value(
                self._agreed_value_low, self._agreed_value_high
            )
            log.info(
                f"  [{source}] AgreedValue: low={self._agreed_value_low}, "
                f"high={self._agreed_value_high}, avg={self._agreed_value_avg}"
            )

    @staticmethod
    def _extract_billing_from_novus(novus: dict, fallback=False) -> dict:
        prices = novus.get("billingFrequencyPrices", {})
        if prices:
            return {k: str(v) for k, v in prices.items()}
        options = novus.get("payments", {}).get("billing_plan_options", [])
        result = {}
        if fallback:
            for opt in options:
                freq = opt.get("billing_frequency", "")
                dd = opt.get("direct_debit", False)
                instalments = opt.get("instalments", [])
                if not instalments:
                    continue
                amount = instalments[0].get("amount")
                if amount is None:
                    continue
                if freq == "ANNUAL" and not dd:
                    result["ANNUAL"] = str(amount)
                elif freq == "MONTHLY" and dd:
                    result["MONTHLY"] = str(amount)
                elif freq == "QUARTERLY" and dd:
                    result["QUARTERLY"] = str(amount)
                elif freq == "FORTNIGHTLY" and dd:
                    result["FORTNIGHTLY"] = str(amount)
        return result

    @staticmethod
    def _extract_optional_cover_premium(
        novus: dict, extra_key: str, base_annual: float
    ) -> float | None:
        delta = novus.get("optionalCoverPremiums", {}).get(extra_key)
        if delta is None:
            return None
        return round(float(base_annual) + float(delta), 2)

    @staticmethod
    def _compute_agreed_value(min_val, max_val) -> int:
        return round((int(min_val) + int(max_val)) / 2)

    @staticmethod
    def _closest_to_1000(numbers, prox=1000):
        nums = [int(n) for n in numbers]
        return str(min(nums, key=lambda x: (abs(x - prox), -x)))

    def _match_vehicle(self, vehicle_list: list) -> dict:
        def norm(v):
            return str(v).strip().upper() if v is not None else ""

        def score(vehicle: dict) -> int:
            v = vehicle.get("vehicle", {})
            e = v.get("engine", {})
            return sum([
                norm(self.input_data.get("gg_YEAR")) == norm(v.get("year")),
                norm(self.input_data.get("gg_MAKE")) == norm(v.get("make")),
                norm(self.input_data.get("gg_FAMILY")) == norm(v.get("model")),
                norm(self.input_data.get("gg_BODYSTYLE")) == norm(v.get("body_type")),
                norm(self.input_data.get("gg_SERIES")) == norm(v.get("series")),
                norm(self.input_data.get("gg_VARIANT")) == norm(v.get("equipment_level")),
                norm(self.input_data.get("gg_ENGINE")) == norm(e.get("type")),
                norm(self.input_data.get("gg_ENGSIZE")) == norm(e.get("capacity_value_litres")),
                norm(str(self.input_data.get("gg_ENGCAP"))) == norm(e.get("capacity_value_cc")),
                norm(self.input_data.get("gg_TRANS")) == norm(e.get("transmission_type")),
                norm(self.input_data.get("gg_CYLINDERDESC")) == norm(e.get("cylinder_code")),
            ])

        scored = [(score(v), v) for v in vehicle_list]
        scored.sort(key=lambda x: x[0], reverse=True)
        if not scored:
            raise ValueError("No vehicle matched")
        best_score, best = scored[0]
        bv = best.get("vehicle", {})
        log.info(
            f"│  Matched: {bv.get('year')} {bv.get('make')} {bv.get('model')} "
            f"{bv.get('equipment_level')} (score: {best_score}/11)"
        )
        if best_score < 6:
            log.warning(f"│  ⚠ Low match score ({best_score}/11)")
        return best

    @staticmethod
    def _find_matching_address(results, address, suburb, state, postcode):
        if not results:
            return None

        def norm(val):
            return str(val).strip().upper() if val else ""

        best_match = None
        best_score = 0
        for entry in results:
            sc = 0
            if norm(entry.get("postcode", "")) == norm(postcode):
                sc += 3
            if norm(entry.get("state", "")) == norm(state):
                sc += 2
            if norm(entry.get("locality_name", "")) == norm(suburb):
                sc += 2
            street = entry.get("street_address", {})
            if norm(street.get("street_name", "")) and \
               norm(street.get("street_name", "")) in norm(address):
                sc += 2
            if norm(street.get("house_number", "")) and \
               norm(street.get("house_number", "")) in norm(address):
                sc += 1
            if sc > best_score:
                best_score = sc
                best_match = entry
        if best_score >= 7:
            log.info(f"│  Address match score: {best_score}/10")
            return best_match
        log.warning(f"│  Address match score: {best_score}/10 — below threshold")
        return None

    def _build_claim_events(self, party_prefix: str = "0") -> tuple[dict, list]:
        if party_prefix == "0":
            claims_count = int(self.input_data.get("Claim_5yrs", 0) or 0)
            claim_type_raw = self.input_data.get("Type_of_claim", "")
            claim_year = self.input_data.get("Claim_year", "")
        else:
            claims_count = int(self.input_data.get("Second_driver_claim_5yrs", 0) or 0)
            claim_type_raw = self.input_data.get("Second_driver_type_of_claim", "")
            claim_year = self.input_data.get("Second_driver_claim_year", "")

        if claims_count == 0:
            return {}, []

        dot_answers = {}
        price_list = []
        claim_event = self.CLAIM_TYPE_MAPPING.get(
            str(claim_type_raw).strip(), "COLLISION"
        )
        claim_year_str = ""
        if claim_year and str(claim_year) not in ("", "nan", "None"):
            try:
                claim_year_str = str(int(float(claim_year)))
            except (ValueError, TypeError):
                claim_year_str = ""

        if claim_year_str:
            dot_answers[f"Parties.{party_prefix}.PriorClaimsEvent.0.Event"] = claim_event
            dot_answers[f"Parties.{party_prefix}.PriorClaimsEvent.0.YearOccurred"] = claim_year_str
            price_list.append({"Event": claim_event, "YearOccurred": claim_year_str})
            log.info(
                f"│  Claim event (P{party_prefix}): {claim_event}, Year: {claim_year_str}"
            )
        else:
            log.warning(
                f"│  Claims count={claims_count} but no valid claim year — skipping"
            )
        return dot_answers, price_list

    # ─────────────────────────────────────────────────────────────────────────
    # Sense check
    # ─────────────────────────────────────────────────────────────────────────
    def _sense_check(self) -> list[str]:
        issues = []

        for prefix in ["NRMABasic", "NRMA", "NRMAPlus"]:
            base_raw = self.results.get(f"{prefix}_YearlyPayment")
            if not base_raw:
                continue
            try:
                base = float(base_raw)
            except (ValueError, TypeError):
                continue

            for extra_col in ["Windscreen", "HireCar", "ChoiceOfRepairer", "RoadsideAssist"]:
                col = f"{prefix}_{extra_col}"
                val_raw = self.results.get(col)
                if not val_raw:
                    continue
                try:
                    val = float(val_raw)
                except (ValueError, TypeError):
                    continue
                if val < base:
                    msg = (
                        f"SENSE CHECK FAIL: {col}={val} < "
                        f"{prefix}_YearlyPayment={base} — nullified"
                    )
                    issues.append(msg)
                    log.warning(f"  ⚠ {msg}")
                    self.results[col] = None

        low_raw = self.results.get("NRMAPlus_AgreedValue_Low")
        high_raw = self.results.get("NRMAPlus_AgreedValue_High")
        av_raw = self.results.get("NRMAPlus_AgreedValue")
        if low_raw and high_raw and av_raw:
            try:
                expected = self._compute_agreed_value(low_raw, high_raw)
                actual = int(float(av_raw))
                if actual != expected:
                    msg = (
                        f"SENSE CHECK FAIL: NRMAPlus_AgreedValue={actual} "
                        f"!= (Low+High)/2={expected} — auto-corrected"
                    )
                    issues.append(msg)
                    log.warning(f"  ⚠ {msg}")
                    self.results["NRMAPlus_AgreedValue"] = str(expected)
            except (ValueError, TypeError):
                pass

        if not issues:
            log.info("  ✓ Sense check passed")
        return issues

    # ─────────────────────────────────────────────────────────────────────────
    # Answer builder
    # ─────────────────────────────────────────────────────────────────────────
    def _answers(self, **extra) -> dict:
        a = {
            "SelectAdditionalCoverages": [],
            "BillingFrequency": "MONTHLY",
            "AdditionalCoverages.glass_excess_waiver": [],
            "AdditionalCoverages.hire_vehicle_after_incident": [],
            "AdditionalCoverages.choice_of_repairer": [],
            "SumInsured": "MARKET_VALUE",
            "HowDoYouWantToReceive": "email_address",
            "Vehicle": self.vehicle_info,
            "VariantSearch": self.variant_search,
            "MakeSearch": self.vehicle_info.get("make") if self.vehicle_info else None,
            "ModelSearch": self.vehicle_info.get("model") if self.vehicle_info else None,
            "YearSearch": str(self.vehicle_info.get("year")) if self.vehicle_info else None,
            "VehicleSearchVIN": "MakeAndModel",
            "TermsConditionAgreement": True,
            "SelectCoverType": "COMPREHENSIVE",
            "VehicleDamages": "__REMOVED__",
            "WhichCarDamages.0": "__REMOVED__",
            "WhichCarDamages.1": "__REMOVED__",
            "WhichCarDamages.2": "__REMOVED__",
        }
        if self.fmt:
            a["CoverStartDate"] = self.fmt
        if self.matched_address:
            a["AddressLookup"] = self.matched_address
        if self.parking_mapped:
            a["WhereIsYourCarParked"] = self.parking_mapped
        if self.yearly_km_mapped:
            a["KilometresDriven"] = self.yearly_km_mapped
        if self.vehicle_use_mapped:
            a["WhatYouUseYourCarFor"] = [self.vehicle_use_mapped]
            if "RIDE_SHARE" not in self.vehicle_use_mapped:
                a["RideshareFoodHours"] = None
            a["CarSharePercent"] = None
        if self.hours_for_business:
            a["RideshareFoodHours"] = self.hours_for_business
        if self.registered_gst:
            a["RegisteredForGst"] = self.registered_gst
            a["ITCAmount"] = self.input_tax_claim
        if self.finance_info is not None:
            a["IsThereFinance"] = self.finance_info
        if self.held is not None:
            a["HasCurrentInsurance"] = self.held
        if self.insurer:
            a["CurrentInsurer"] = self.insurer
        if self.tenure:
            a["CurrentInsurerTenure"] = self.tenure
        a.update(extra)
        return a

    def _post_update(self, answers: dict) -> dict:
        resp = self._request(
            self._api(
                f"nrma/motor/{self.quote_id}/targetStep/Experience3UpdateProcessing"
            ),
            "POST", {"answers": answers},
        )
        self._update_conditions_from_response(resp, "UpdateProcessing")
        return resp

    # ─────────────────────────────────────────────────────────────────────────
    # Steps 0-8 — unchanged from working version
    # ─────────────────────────────────────────────────────────────────────────
    def _bootstrap_akamai(self, max_attempts: int = 3):
        """Render the landing page in a browser so the Akamai sensor JS runs and
        returns a *validated* `_abck` cookie (`~0~`, not `~-1~`). A raw GET never
        executes that JS, so the session stays bot-flagged and the protected
        parties step 403s. Retried until `_abck` validates.
        """
        for attempt in range(1, max_attempts + 1):
            status, _html, exp_cookies = self.zyte.browser_request(
                self.MOTOR_LANDING,
                cookies=self.cookies,
                actions=ZYTE_BOOTSTRAP_ACTIONS,
            )
            self._update_cookies_from_experimental(exp_cookies)
            abck = self.cookies.get("_abck", "")
            if _abck_validated(abck):
                log.info(
                    f"│  Bootstrap ✓ Akamai validated on attempt {attempt} "
                    f"({len(self.cookies)} cookies)"
                )
                return
            log.warning(
                f"│  Bootstrap attempt {attempt}/{max_attempts}: "
                f"_abck still bot-flagged (~-1~)"
            )
        log.warning(
            "│  ⚠ Akamai _abck never validated — parties step may be rejected"
        )

    def init_session(self):
        log.info("┌─ STEP 0: Bootstrap, create quote & get token")
        self._bootstrap_akamai()

        data = self._request(
            self._api("nrma/motor/experience3/createQuote"),
            "POST", {"answers": {}},
        )
        self.quote_id = data["quoteId"]
        qid_sig = data["novusContext"]["quoteIdSignature"]
        qid_exp = data["novusContext"]["quoteIdSignatureExpiry"]
        # Cache so _request can re-mint the token on a transient 401.
        self._quote_signature = qid_sig
        self._quote_expiry = qid_exp
        log.info(f"│  Quote ID: {self.quote_id}")

        self._exchange_token(qid_sig, qid_exp)
        log.info(f"└─ Token acquired ({len(self.access_token)} chars)")

    def _refresh_access_token(self) -> bool:
        """Re-mint the x-iag-customer token from the cached quote signature.
        Used to recover from a transient 401. Returns True on success.
        """
        if not self._quote_signature or self._quote_expiry is None:
            return False
        try:
            self._exchange_token(self._quote_signature, self._quote_expiry)
            return True
        except Exception as e:
            log.warning(f"  [Auth] token re-mint failed: {e}")
            return False

    def _exchange_token(self, signature: str, expiry):
        """Exchange the quote signature for an x-iag-customer JWT at the
        connect.nrma.com.au subdomain (same-site, filtered cookies)."""
        payload = {
            "payload": {"quoteId": self.quote_id, "expiry": expiry},
            "signature": signature,
        }
        headers = {
            "accept": "application/json, text/plain, */*",
            "accept-language": "en-US,en;q=0.9",
            "content-type": "application/json",
            "origin": self.BASE_URL,
            "referer": "https://insurance.nrma.com.au/",
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-site",
            "user-agent": self.USER_AGENT,
        }
        # Only send insurance.nrma.com.au Akamai cookies to the connect subdomain.
        connect_cookies = {
            k: v for k, v in self.cookies.items() if k in ("ak_bmsc", "bm_sz", "_abck")
        }
        status, resp_headers, resp_body, exp_cookies = self.zyte.request(
            "https://connect.nrma.com.au/cb/anonymous/quote/nrma",
            method="POST", headers=headers, body=json.dumps(payload),
            cookies=connect_cookies,
        )
        self._update_all_cookies(resp_headers, exp_cookies)
        if status >= 400:
            raise RuntimeError(f"Token exchange → {status}: {resp_body[:300]}")
        token_data = json.loads(resp_body) if resp_body.strip() else {}
        self.access_token = token_data.get("accessToken")
        if not self.access_token:
            raise ValueError("Failed to get access token")
        self.cookies["x-iag-customer"] = self.access_token

    def step_vehicle_search(self):
        log.info("┌─ STEP 1: Vehicle search")
        data = self._request(
            self._api(
                f"nrma/motor/{self.quote_id}/targetStep/ProcessingVehicleSearchAlt"
            ),
            "POST",
            {"answers": {
                "YearSearch": self.input_data.get("gg_YEAR"),
                "MakeSearch": self.input_data.get("gg_MAKE"),
                "ModelSearch": self.input_data.get("gg_FAMILY"),
                "SelectBodyType": "",
                "SelectTransmissionType": "",
                "SelectVariant": "",
            }},
        )
        self._update_conditions_from_response(data, "VehicleSearch")
        vehicles = data.get("novusContext", {}).get("vehicles", [])
        log.info(f"│  {len(vehicles)} variant(s) returned")
        if not vehicles:
            raise ValueError("No vehicles found")
        best = self._match_vehicle(vehicles)
        self.vehicle_info = best.get("vehicle", {})
        self.variant_search = json.dumps({
            "year": self.vehicle_info.get("year"),
            "make": self.vehicle_info.get("make"),
            "model": self.vehicle_info.get("model"),
            "bodyType": self.vehicle_info.get("body_type"),
            "engineCapacity": self.vehicle_info.get("engine", {}).get("capacity_value_litres"),
            "transmission": self.vehicle_info.get("engine", {}).get("transmission_type"),
            "equipmentLevel": self.vehicle_info.get("equipment_level"),
            "series": self.vehicle_info.get("series"),
        })
        self._post_update(self._answers())
        log.info("└─ Vehicle registered")

    @staticmethod
    def _parse_date(raw: str) -> str:
        """Parse a policy date to YYYY-MM-DD, accepting both 4-digit and 2-digit
        years (e.g. '13-Jul-2026' and '13-Jul-26') and ISO input."""
        raw = str(raw).strip()
        for fmt in ("%d-%b-%Y", "%d-%b-%y", "%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y"):
            try:
                return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue
        raise ValueError(f"Unrecognised policy date format: {raw!r}")

    def step_policy_date(self):
        raw = self.input_data.get("Policy_start_date", "")
        self.fmt = self._parse_date(raw)
        log.info(f"┌─ STEP 2: Policy date — {raw} → {self.fmt}")
        self._post_update(self._answers())
        log.info("└─ Done")

    def step_address(self):
        log.info("┌─ STEP 3: Address lookup")
        address = self.input_data.get("Address", "").strip()
        suburb = self.input_data.get("Suburb", "").strip()
        state = self.input_data.get("State", "").strip()
        postcode = str(self.input_data.get("Postcode", "")).zfill(4)
        full_query = f"{address}, {suburb} {state} {postcode}"
        log.info(f"│  Attempt 1: {full_query}")
        results = self._request(
            self._api("reference/address/lookup/"), "POST",
            {"excludePostalAddresses": True, "address": full_query, "brand": "nrma"},
        )
        matched = self._find_matching_address(results, address, suburb, state, postcode)
        if not matched and address:
            log.info(f"│  Attempt 2: {address}")
            results = self._request(
                self._api("reference/address/lookup/"), "POST",
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

        addr_resp = self._post_update(self._answers())
        log.info(f"│  BaseExcess after address: {self._available_excesses}")
        if not self._available_excesses:
            self._log_excess_diagnostic(addr_resp, "address")
        log.info("└─ Done")

    def step_parking(self):
        raw = self.input_data.get("Night_garage_type", "")
        self.parking_mapped = self.GARAGE_TYPE_MAPPING.get(raw)
        log.info(f"┌─ STEP 4: Parking — {raw} → {self.parking_mapped}")
        self._post_update(self._answers())
        log.info("└─ Done")

    def step_kilometres(self):
        raw = self.input_data.get("Yearly_km_band_NRMA", "")
        self.yearly_km_mapped = self.YEARLY_KM_MAPPING.get(raw)
        log.info(f"┌─ STEP 5: KM — {raw} → {self.yearly_km_mapped}")
        self._post_update(self._answers())
        log.info("└─ Done")

    def step_usage(self):
        use = self.input_data.get("Vehicle_use", "Private use")
        self.vehicle_use_mapped = self.VEHICLE_USE_MAPPING.get(use)
        log.info(f"┌─ STEP 6: Usage — {use} → {self.vehicle_use_mapped}")
        self._post_update(self._answers())
        if "rideshare" in use.lower() or "food delivery" in use.lower():
            raw_hours = self.input_data.get("Hours_for_business", "")
            if raw_hours and str(raw_hours) not in ("", "nan", "None"):
                self.hours_for_business = str(int(float(raw_hours)))
            log.info(f"│  Rideshare/delivery hours: {self.hours_for_business}")
            self._post_update(self._answers())
        self.registered_gst = self.input_data.get("Registered_GST") == "Yes"
        self.input_tax_claim = self.input_data.get("Pct_input_tax_claim")
        if use != "Private use":
            log.info(
                f"│  Business use — GST: {self.registered_gst}, "
                f"ITC: {self.input_tax_claim}"
            )
            self._post_update(self._answers())
        log.info("└─ Done")

    def step_finance(self):
        self.finance_info = self.input_data.get("Finance", "No") == "Yes"
        log.info(f"┌─ STEP 7: Finance — {self.finance_info}")
        self._post_update(self._answers())
        log.info("└─ Done")

    def step_insurance(self):
        self.held = self.input_data.get("Currently_Insured") == "Yes"
        log.info(f"┌─ STEP 8: Insurance — held={self.held}")
        if self.held:
            self.insurer = self.INSURERS.get(
                self.input_data.get("Previous_insurer", ""),"OTHER"
            )
            log.info(f"│  Insurer mapped: {self.insurer}")
            self._post_update(self._answers())
            self.tenure = self.INSURER_TENURE.get(
                self.input_data.get("Years_insured_band_NRMA", "")
            )
            if self.tenure:
                log.info(f"│  Tenure: {self.tenure}")
                self._post_update(self._answers())
            else:
                log.info("│  No tenure value — skipping tenure update")
        else:
            self._post_update(self._answers())
        log.info("└─ Done")

    def step_parties(self):
        log.info("┌─ STEP 9: Party details")
        self.owner_type = (
            "Individual"
            if self.input_data.get("Owner_type") == "Person"
            else "Blank"
        )
        self.drive = self.input_data.get("Drive_this_car") == "Yes"
        self.membership = self.input_data.get("Membership") == "Yes"
        self.gender = self.input_data.get("Gender", "").upper()
        self.dob = date(
            int(self.input_data.get("DOB_YYYY_dummy", 2000)),
            int(self.input_data.get("DOB_MM_dummy", 1)),
            int(self.input_data.get("DOB_DD_dummy", 1)),
        ).strftime("%Y-%m-%d")
        log.info(f"│  Primary: {self.gender}, DOB={self.dob}, Driver={self.drive}")

        self.add_second_driver = self.input_data.get("Add_second_driver") == "Yes"
        second_driver_details = {}
        second_driver_parties = {}

        if self.add_second_driver:
            self.second_owner_type = (
                "Individual"
                if self.input_data.get("Second_driver_type") == "Person"
                else "Blank"
            )
            self.second_drive = (
                self.input_data.get("Second_driver_drive_this_car") == "Yes"
            )
            self.second_membership = (
                self.input_data.get("Second_driver_membership") == "Yes"
            )
            self.second_gender = self.input_data.get("Second_Driver_Gender", "").upper()
            try:
                self.second_dob = date(
                    int(self.input_data.get("Second_driver_DOB_YYYY_dummy", 2000)),
                    int(self.input_data.get("Second_driver_DOB_MM_dummy", 1)),
                    int(self.input_data.get("Second_driver_DOB_DD_dummy", 1)),
                ).strftime("%Y-%m-%d")
            except (ValueError, TypeError) as e:
                log.warning(f"│  ⚠ Second driver DOB invalid: {e} — using default")
                self.second_dob = "2000-01-01"
            log.info(
                f"│  Secondary: {self.second_gender}, "
                f"DOB={self.second_dob}, Driver={self.second_drive}"
            )
            second_driver_details = {
                "Parties.1.AreYouAnExistingCustomer": str(self.second_membership).lower(),
                "Parties.1.IsCustomer": self.second_membership,
                "Parties.1.IsDriver": self.second_drive,
                "Parties.1.IsOwner": False,
                "Parties.1.IsOwnerDriver": ["Driver"],
                "Parties.1.PartyDOB": self.second_dob,
                "Parties.1.PartyGender": self.second_gender,
                "Parties.1.PartyType": self.second_owner_type,
            }

        current_excess = self.default_excess or "1000"
        party_fields = {
            "Parties.0.AreYouAnExistingCustomer": str(self.membership).lower(),
            "Parties.0.IsCustomer": self.membership,
            "Parties.0.IsDriver": self.drive,
            "Parties.0.IsOwner": True,
            "Parties.0.IsOwnerDriver": (
                ["Owner", "Driver"] if self.drive else ["Owner"]
            ),
            "Parties.0.PartyDOB": self.dob,
            "Parties.0.PartyGender": self.gender,
            "Parties.0.PartyType": self.owner_type,
            "BaseExcess": current_excess,
            **second_driver_details,
        }

        answers = self._answers(**party_fields)
        log.info("│  Submitting Experience3UpdatePartiesProcessing...")
        data = self._request(
            self._api(
                f"nrma/motor/{self.quote_id}/targetStep"
                f"/Experience3UpdatePartiesProcessing"
            ),
            "POST", {"answers": answers},
        )

        parties_novus = data.get("novusContext", {})
        parties_conditions = parties_novus.get("conditions", {})
        parties_excesses = parties_conditions.get("BaseExcess", [])
        if parties_excesses:
            old_default = self.default_excess
            new_default = self._closest_to_1000(parties_excesses)
            self._available_excesses = parties_excesses
            self.default_excess = new_default
            if old_default != new_default:
                log.info(
                    f"│  ⚠ BaseExcess changed after parties: "
                    f"{parties_excesses} → default={new_default} (was {old_default})"
                )
            else:
                log.info(
                    f"│  BaseExcess confirmed: {parties_excesses} → default={new_default}"
                )
        else:
            log.warning("│  ⚠ No BaseExcess in parties response — keeping current")
            self._log_excess_diagnostic(data, "parties")

        ans = parties_novus.get("answers", {})
        self.party_id = ans.get("Parties.0.PartyId")
        self.temp_id = ans.get("Parties.0.TempId")
        if not self.party_id:
            raise ValueError(
                "PartyId not returned from Experience3UpdatePartiesProcessing — "
                "session may have been rejected by underwriting rules"
            )
        log.info(f"│  Party ID: {self.party_id}")

        rsa_answers = self._answers(**party_fields)
        rsa_answers["Parties.0.PartyId"] = self.party_id
        rsa_answers["Parties.0.TempId"] = self.temp_id
        rsa_answers["Parties.0.BackendPartyRole"] = "PRIMARY_POLICY_HOLDER"

        if self.add_second_driver:
            self.party_id_second = ans.get("Parties.1.PartyId")
            self.temp_id_second = ans.get("Parties.1.TempId")
            log.info(f"│  Second driver Party ID: {self.party_id_second}")
            rsa_answers["Parties.1.PartyId"] = self.party_id_second
            rsa_answers["Parties.1.TempId"] = self.temp_id_second
            rsa_answers["Parties.1.BackendPartyRole"] = "DRIVER"

        log.info("│  Checking roadside eligibility...")
        try:
            self._request(
                self._api(
                    f"nrma/motor/{self.quote_id}/targetStep"
                    f"/Experience3BundledProductGetRoadsideAssistanceEligibility"
                ),
                "POST",
                {"answers": rsa_answers, "brandConfig": self.BRAND_CONFIG},
            )
        except Exception as e:
            log.warning(
                f"│  ⚠ Roadside eligibility call failed (non-fatal): {e} — continuing"
            )

        age_licensed = str(self.input_data.get("Age_licensed", "18"))
        claims = str(self.input_data.get("Claim_5yrs", "0"))
        licence_events = str(self.input_data.get("Licence_disqualified", "0"))
        log.info(
            f"│  History: licensed={age_licensed}, "
            f"claims={claims}, events={licence_events}"
        )

        primary_claim_dots, self._primary_claim_list = self._build_claim_events("0")
        self._second_claim_list = []
        second_claim_dots = {}

        if self.add_second_driver:
            second_age_licensed = self.input_data.get("Second_Driver_Age_Licensed")
            second_claims = self.input_data.get("Second_driver_claim_5yrs")
            second_license = self.input_data.get("Second_driver_licence_disqlf")
            try:
                if second_age_licensed and int(float(str(second_age_licensed))) > 0:
                    pass
                else:
                    second_age_licensed = "18"
                    log.warning("│  ⚠ Invalid Second_Driver_Age_Licensed — defaulting to 18")
            except (ValueError, TypeError):
                second_age_licensed = "18"
            log.info(
                f"│  Secondary history: licensed={second_age_licensed}, "
                f"claims={second_claims}, events={second_license}"
            )
            second_claim_dots, self._second_claim_list = self._build_claim_events("1")
            second_driver_parties = {
                "Parties.1.PartyId": self.party_id_second,
                "Parties.1.TempId": self.temp_id_second,
                "Parties.1.BackendPartyRole": "DRIVER",
                "Parties.1.AgeLicenseAcquired": str(second_age_licensed),
                "Parties.1.HowManyPriorClaims": str(second_claims or 0),
                "Parties.1.HowManyLicenseEvents": str(second_license or 0),
            }

        party_fields["BaseExcess"] = self.default_excess
        offer_answers = self._answers(**party_fields)
        offer_answers.update({
            "Parties.0.PartyId": self.party_id,
            "Parties.0.TempId": self.temp_id,
            "Parties.0.BackendPartyRole": "PRIMARY_POLICY_HOLDER",
            "Parties.0.AgeLicenseAcquired": age_licensed,
            "Parties.0.HowManyPriorClaims": claims,
            "Parties.0.HowManyLicenseEvents": licence_events,
            **primary_claim_dots,
            **second_driver_parties,
            **second_claim_dots,
        })

        log.info("│  Submitting Experience3OfferProcessing (initial)...")
        self._request(
            self._api(
                f"nrma/motor/{self.quote_id}/targetStep/Experience3OfferProcessing"
            ),
            "POST", {"answers": offer_answers},
        )
        log.info(f"└─ Done — final default_excess={self.default_excess}")

    # ─────────────────────────────────────────────────────────────────────────
    # Offer payload builder
    # ─────────────────────────────────────────────────────────────────────────
    def _build_offer_answers(
        self,
        cover_enum: str,
        excess: str,
        extras: list | None = None,
        agreed_value: str | None = None,
    ) -> dict:
        extras = extras or []
        age_licensed = str(self.input_data.get("Age_licensed", "18"))
        claims = str(self.input_data.get("Claim_5yrs", "0"))
        licence_events = str(self.input_data.get("Licence_disqualified", "0"))

        second_driver_details = {}
        if self.add_second_driver:
            second_age_licensed = self.input_data.get("Second_Driver_Age_Licensed", "18")
            try:
                if not second_age_licensed or int(float(str(second_age_licensed))) <= 0:
                    second_age_licensed = "18"
            except (ValueError, TypeError):
                second_age_licensed = "18"
            second_driver_details = {
                "Parties.1.PartyId": self.party_id_second,
                "Parties.1.TempId": self.temp_id_second,
                "Parties.1.BackendPartyRole": "DRIVER",
                "Parties.1.AgeLicenseAcquired": str(second_age_licensed),
                "Parties.1.HowManyPriorClaims": str(
                    self.input_data.get("Second_driver_claim_5yrs", 0) or 0
                ),
                "Parties.1.HowManyLicenseEvents": str(
                    self.input_data.get("Second_driver_licence_disqlf", 0) or 0
                ),
                "Parties.1.AreYouAnExistingCustomer": str(self.second_membership).lower(),
                "Parties.1.IsCustomer": self.second_membership,
                "Parties.1.IsDriver": self.second_drive,
                "Parties.1.IsOwner": False,
                "Parties.1.IsOwnerDriver": ["Driver"],
                "Parties.1.PartyDOB": self.second_dob,
                "Parties.1.PartyGender": self.second_gender,
                "Parties.1.PartyType": self.second_owner_type,
            }

        extra_fields = {
            "Parties.0.PartyId": self.party_id,
            "Parties.0.TempId": self.temp_id,
            "Parties.0.BackendPartyRole": "PRIMARY_POLICY_HOLDER",
            "Parties.0.AgeLicenseAcquired": age_licensed,
            "Parties.0.HowManyPriorClaims": claims,
            "Parties.0.HowManyLicenseEvents": licence_events,
            "Parties.0.AreYouAnExistingCustomer": str(self.membership).lower(),
            "Parties.0.IsCustomer": self.membership,
            "Parties.0.IsDriver": self.drive,
            "Parties.0.IsOwner": True,
            "Parties.0.IsOwnerDriver": (
                ["Owner", "Driver"] if self.drive else ["Owner"]
            ),
            "Parties.0.PartyDOB": self.dob,
            "Parties.0.PartyGender": self.gender,
            "Parties.0.PartyType": self.owner_type,
            "BaseExcess": excess,
            "SelectCoverType": cover_enum,
            "RoadsideAssistanceEligibility": False,
            "EmailAddressConsent": "",
            **second_driver_details,
        }

        answers = self._answers(**extra_fields)

        if cover_enum == "COMPREHENSIVE_PLUS":
            answers["SumInsured"] = "AGREED_VALUE"
            if agreed_value:
                answers["CarAgreedValue"] = str(agreed_value)
        else:
            answers["SumInsured"] = "MARKET_VALUE"

        answers["SelectAdditionalCoverages"] = extras
        for key in self.EXTRAS_MAPPING:
            answers[f"AdditionalCoverages.{key}"] = (
                ["true"] if key in extras else []
            )

        primary_claim_dots, _ = self._build_claim_events("0")
        answers.update(primary_claim_dots)
        if self.add_second_driver:
            second_claim_dots, _ = self._build_claim_events("1")
            answers.update(second_claim_dots)

        return answers

    def _fetch_offer(
        self,
        cover_enum: str,
        excess: str,
        extras: list | None = None,
        agreed_value: str | None = None,
    ) -> dict:
        answers = self._build_offer_answers(
            cover_enum=cover_enum,
            excess=excess,
            extras=extras or [],
            agreed_value=agreed_value,
        )
        return self._request(
            self._api(
                f"nrma/motor/{self.quote_id}/targetStep/Experience3OfferProcessing"
            ),
            "POST", {"answers": answers},
        )

    def _build_price_payload(self, cover_type: str, excess: str) -> dict:
        """Used only for Roadside Assist pricing (Phase 4)."""
        primary_party = {
            "IsOwnerDriver": ["Owner", "Driver"] if self.drive else ["Owner"],
            "IsOwner": True,
            "PartyType": self.owner_type,
            "IsCustomer": self.membership,
            "PartyGender": self.gender,
            "PartyDOB": self.dob,
            "IsDriver": self.drive,
            "AreYouAnExistingCustomer": str(self.membership).lower(),
            "PartyId": self.party_id,
            "TempId": self.temp_id,
            "BackendPartyRole": "PRIMARY_POLICY_HOLDER",
            "AgeLicenseAcquired": str(self.input_data.get("Age_licensed", "18")),
            "HowManyPriorClaims": str(self.input_data.get("Claim_5yrs", "0")),
            "HowManyLicenseEvents": str(self.input_data.get("Licence_disqualified", "0")),
        }
        if self._primary_claim_list:
            primary_party["PriorClaimsEvent"] = self._primary_claim_list

        sum_insured_type = (
            "AGREED_VALUE" if cover_type == "COMPREHENSIVE_PLUS" else "MARKET_VALUE"
        )
        payload = {
            "SelectAdditionalCoverages": [],
            "AdditionalCoverages": {
                "glass_excess_waiver": [],
                "hire_vehicle_after_incident": [],
                "choice_of_repairer": [],
            },
            "SumInsured": sum_insured_type,
            "HowDoYouWantToReceive": "email_address",
            "Vehicle": self.vehicle_info,
            "VariantSearch": self.variant_search,
            "MakeSearch": self.vehicle_info.get("make") if self.vehicle_info else None,
            "ModelSearch": self.vehicle_info.get("model") if self.vehicle_info else None,
            "YearSearch": str(self.vehicle_info.get("year")) if self.vehicle_info else None,
            "TermsConditionAgreement": True,
            "CoverStartDate": self.fmt,
            "AddressLookup": self.matched_address,
            "WhereIsYourCarParked": self.parking_mapped,
            "KilometresDriven": self.yearly_km_mapped,
            "WhatYouUseYourCarFor": [self.vehicle_use_mapped],
            "RegisteredForGst": self.registered_gst,
            "ITCAmount": self.input_tax_claim,
            "IsThereFinance": self.finance_info,
            "HasCurrentInsurance": self.held,
            "CurrentInsurer": self.insurer,
            "CurrentInsurerTenure": self.tenure,
            "SelectCoverType": cover_type,
            "BaseExcess": excess,
            "AddRoadsideAssistance": False,
            "Parties": [primary_party],
        }

        if cover_type == "COMPREHENSIVE_PLUS" and self._agreed_value_avg:
            payload["CarAgreedValue"] = str(self._agreed_value_avg)

        if self.add_second_driver:
            second_age_licensed = self.input_data.get("Second_Driver_Age_Licensed", "18")
            try:
                if not second_age_licensed or int(float(str(second_age_licensed))) <= 0:
                    second_age_licensed = "18"
            except (ValueError, TypeError):
                second_age_licensed = "18"
            second_party = {
                "IsOwnerDriver": ["Driver"],
                "IsOwner": False,
                "PartyType": self.second_owner_type,
                "IsCustomer": self.second_membership,
                "PartyGender": self.second_gender,
                "PartyDOB": self.second_dob,
                "IsDriver": self.second_drive,
                "AreYouAnExistingCustomer": str(self.second_membership).lower(),
                "PartyId": self.party_id_second,
                "TempId": self.temp_id_second,
                "BackendPartyRole": "DRIVER",
                "AgeLicenseAcquired": str(second_age_licensed),
                "HowManyPriorClaims": str(
                    self.input_data.get("Second_driver_claim_5yrs", "0") or "0"
                ),
                "HowManyLicenseEvents": str(
                    self.input_data.get("Second_driver_licence_disqlf", "0") or "0"
                ),
            }
            if self._second_claim_list:
                second_party["PriorClaimsEvent"] = self._second_claim_list
            payload["Parties"].append(second_party)

        return payload

    def _fetch_price(self, cover_type: str, excess: str) -> dict:
        """Used only for Roadside Assist pricing (Phase 4)."""
        payload = self._build_price_payload(cover_type, excess)
        return self._request(
            self._api(f"nrma/motor/experience3/{self.quote_id}/price"),
            "PUT", payload,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Step 10: Premium collection
    # ─────────────────────────────────────────────────────────────────────────
    def step_collect_premiums(self):
        log.info("┌─ STEP 10: Collect premiums")
        log.info(
            f"│  Using default_excess={self.default_excess}, "
            f"available={self._available_excesses}"
        )

        if not self.default_excess:
            raise ValueError(
                "default_excess is None — BaseExcess was never captured from API"
            )

        # ── Phase 1: THREE separate OfferProcessing calls (one per cover type)
        log.info("│  Phase 1: Base prices")

        # ── 1a: COMPREHENSIVE_BASICS
        log.info("│  ├─ COMPREHENSIVE_BASICS")
        try:
            resp_basic = self._fetch_offer(
                cover_enum="COMPREHENSIVE_BASICS",
                excess=self.default_excess,
                extras=[],
            )
            novus_basic = resp_basic.get("novusContext", {})
            billing_basic = self._extract_billing_from_novus(novus_basic)

            # ── FIX 1: Detect when Basic is not genuinely available.
            # The API returns a price for COMPREHENSIVE_BASICS even when the
            # website does not show the Basic option. The signal is that the
            # returned price equals the COMPREHENSIVE price — in that case
            # the API is falling back to Comprehensive pricing.
            # We compare AFTER collecting both prices in Phase 1b.
            # Store temporarily; validation happens after 1b.
            if billing_basic.get("ANNUAL"):
                self._basic_billing_temp = billing_basic
                log.info(
                    f"│  │  ✓ NRMABasic (provisional): "
                    f"Annual=${billing_basic.get('ANNUAL')}"
                )
            else:
                self._basic_billing_temp = {}
                log.info(
                    "│  │  ℹ NRMABasic: no billing returned — "
                    "will leave empty"
                )
        except Exception as e:
            self._basic_billing_temp = {}
            log.error(f"│  │  ✗ COMPREHENSIVE_BASICS: {e}")
            log.debug(traceback.format_exc())

        # ── 1b: COMPREHENSIVE
        log.info("│  ├─ COMPREHENSIVE")
        try:
            resp_comp = self._fetch_offer(
                cover_enum="COMPREHENSIVE",
                excess=self.default_excess,
                extras=[],
            )
            novus_comp = resp_comp.get("novusContext", {})
            if not self._agreed_value_avg:
                self._update_conditions_from_response(resp_comp, "COMPREHENSIVE")
            billing_comp = self._extract_billing_from_novus(novus_comp)
            if billing_comp.get("ANNUAL"):
                self.results["NRMA_DefaultExcess"] = self.default_excess
                self.results["NRMA_YearlyPayment"] = billing_comp.get("ANNUAL")
                self.results["NRMA_MonthlyPayment"] = billing_comp.get("MONTHLY")
                self.results["NRMA_QuarterlyPayment"] = billing_comp.get("QUARTERLY")
                self.results["NRMA_FortnightlyPayment"] = billing_comp.get("FORTNIGHTLY")
                log.info(f"│  │  ✓ NRMA: Annual=${billing_comp.get('ANNUAL')}")
            else:
                log.warning("│  │  ⚠ No billing data for COMPREHENSIVE")
        except Exception as e:
            log.error(f"│  │  ✗ COMPREHENSIVE: {e}")
            log.debug(traceback.format_exc())

        # ── FIX 1 (continued): Now that we have both Basic and Comprehensive
        # prices, check if Basic == Comprehensive. If so, Basic is not
        # genuinely available — clear it and leave those columns empty.
        basic_annual = (
            self._basic_billing_temp.get("ANNUAL")
            if self._basic_billing_temp
            else None
        )
        comp_annual = self.results.get("NRMA_YearlyPayment")

        if basic_annual and comp_annual:
            try:
                if float(basic_annual) == float(comp_annual):
                    # Basic price == Comprehensive price → Basic not available
                    log.info(
                        f"│  ℹ NRMABasic_YearlyPayment ({basic_annual}) == "
                        f"NRMA_YearlyPayment ({comp_annual}) — "
                        f"Basic cover not available for this profile, leaving empty"
                    )
                    self._basic_billing_temp = None
                else:
                    # Genuine Basic price — store it
                    self.results["NRMABasic_DefaultExcess"] = self.default_excess
                    self.results["NRMABasic_YearlyPayment"] = self._basic_billing_temp.get("ANNUAL")
                    self.results["NRMABasic_MonthlyPayment"] = self._basic_billing_temp.get("MONTHLY")
                    self.results["NRMABasic_QuarterlyPayment"] = self._basic_billing_temp.get("QUARTERLY")
                    self.results["NRMABasic_FortnightlyPayment"] = self._basic_billing_temp.get("FORTNIGHTLY")
                    log.info(
                        f"│  ✓ NRMABasic confirmed available: "
                        f"Annual=${basic_annual}"
                    )
            except (ValueError, TypeError):
                pass
        elif basic_annual:
            # No Comprehensive to compare against — store Basic as-is
            self.results["NRMABasic_DefaultExcess"] = self.default_excess
            self.results["NRMABasic_YearlyPayment"] = self._basic_billing_temp.get("ANNUAL")
            self.results["NRMABasic_MonthlyPayment"] = self._basic_billing_temp.get("MONTHLY")
            self.results["NRMABasic_QuarterlyPayment"] = self._basic_billing_temp.get("QUARTERLY")
            self.results["NRMABasic_FortnightlyPayment"] = self._basic_billing_temp.get("FORTNIGHTLY")

        # ── 1c: COMPREHENSIVE_PLUS — get AgreedValue range
        log.info("│  ├─ COMPREHENSIVE_PLUS (MARKET_VALUE — for AgreedValue range)")
        try:
            resp_plus_mv = self._fetch_offer(
                cover_enum="COMPREHENSIVE_PLUS",
                excess=self.default_excess,
                extras=[],
                agreed_value=None,
            )
            if not self._agreed_value_avg:
                self._update_conditions_from_response(
                    resp_plus_mv, "COMPREHENSIVE_PLUS_MV"
                )
            if self._agreed_value_avg:
                self.results["NRMAPlus_AgreedValue_Low"] = self._agreed_value_low
                self.results["NRMAPlus_AgreedValue_High"] = self._agreed_value_high
                self.results["NRMAPlus_AgreedValue"] = str(self._agreed_value_avg)
                log.info(
                    f"│  │  AgreedValue: low={self._agreed_value_low}, "
                    f"high={self._agreed_value_high}, avg={self._agreed_value_avg}"
                )
        except Exception as e:
            log.error(f"│  │  ✗ COMPREHENSIVE_PLUS (MV): {e}")
            log.debug(traceback.format_exc())

        # ── 1d: COMPREHENSIVE_PLUS — with correct AGREED_VALUE
        log.info("│  └─ COMPREHENSIVE_PLUS (AGREED_VALUE)")
        if self._agreed_value_avg:
            try:
                resp_plus = self._fetch_offer(
                    cover_enum="COMPREHENSIVE_PLUS",
                    excess=self.default_excess,
                    extras=[],
                    agreed_value=str(self._agreed_value_avg),
                )
                novus_plus = resp_plus.get("novusContext", {})
                billing_plus = self._extract_billing_from_novus(novus_plus)
                if billing_plus.get("ANNUAL"):
                    self.results["NRMAPlus_DefaultExcess"] = self.default_excess
                    self.results["NRMAPlus_YearlyPayment"] = billing_plus.get("ANNUAL")
                    self.results["NRMAPlus_MonthlyPayment"] = billing_plus.get("MONTHLY")
                    self.results["NRMAPlus_QuarterlyPayment"] = billing_plus.get("QUARTERLY")
                    self.results["NRMAPlus_FortnightlyPayment"] = billing_plus.get("FORTNIGHTLY")
                    log.info(f"│  │  ✓ NRMAPlus: Annual=${billing_plus.get('ANNUAL')}")
                else:
                    log.warning("│  │  ⚠ No billing data for COMPREHENSIVE_PLUS")
            except Exception as e:
                log.error(f"│  │  ✗ COMPREHENSIVE_PLUS (AV): {e}")
                log.debug(traceback.format_exc())
        else:
            log.warning("│  ⚠ Skipping NRMAPlus — no agreed value range")

        # ── Phases 2-5: Toggle mode only
        if self.toggle:

            # Phase 2: Optional extras for COMPREHENSIVE
            log.info("│  Phase 2: Optional extras (COMPREHENSIVE)")
            base_annual_comp_raw = self.results.get("NRMA_YearlyPayment")
            if base_annual_comp_raw:
                base_annual_comp = float(base_annual_comp_raw)
                for extra_key, col_suffix in self.EXTRAS_MAPPING.items():
                    col = f"NRMA_{col_suffix}"
                    try:
                        offer = self._fetch_offer(
                            cover_enum="COMPREHENSIVE",
                            excess=self.default_excess,
                            extras=[extra_key],
                        )
                        novus_extra = offer.get("novusContext", {})
                        with_extra = self._extract_optional_cover_premium(
                            novus_extra, extra_key, base_annual_comp
                        )
                        if with_extra is not None:
                            self.results[col] = str(with_extra)
                            delta = novus_extra.get(
                                "optionalCoverPremiums", {}
                            ).get(extra_key)
                            log.info(
                                f"│  ✓ {col}: {base_annual_comp} + {delta} = {with_extra}"
                            )
                        else:
                            log.warning(
                                f"│  ⚠ {col}: optionalCoverPremiums missing '{extra_key}'"
                            )
                            self.results[col] = None
                    except Exception as e:
                        log.error(f"│  ✗ {col}: {e}")
                        self.results[col] = None
            else:
                log.warning("│  ⚠ Skipping NRMA extras — no base annual")

            # Phase 3: Optional extras for COMPREHENSIVE_PLUS
            log.info("│  Phase 3: Optional extras (COMPREHENSIVE_PLUS)")
            base_annual_plus_raw = self.results.get("NRMAPlus_YearlyPayment")
            if base_annual_plus_raw and self._agreed_value_avg:
                base_annual_plus = float(base_annual_plus_raw)
                self.results["NRMAPlus_Windscreen"] = base_annual_plus_raw
                self.results["NRMAPlus_HireCar"] = base_annual_plus_raw
                log.info(
                    f"│  ✓ NRMAPlus_Windscreen = NRMAPlus_HireCar = "
                    f"{base_annual_plus_raw} (auto-included)"
                )
                try:
                    offer = self._fetch_offer(
                        cover_enum="COMPREHENSIVE_PLUS",
                        excess=self.default_excess,
                        extras=["choice_of_repairer"],
                        agreed_value=str(self._agreed_value_avg),
                    )
                    novus_extra = offer.get("novusContext", {})
                    with_extra = self._extract_optional_cover_premium(
                        novus_extra, "choice_of_repairer", base_annual_plus
                    )
                    if with_extra is not None:
                        self.results["NRMAPlus_ChoiceOfRepairer"] = str(with_extra)
                        delta = novus_extra.get(
                            "optionalCoverPremiums", {}
                        ).get("choice_of_repairer")
                        log.info(
                            f"│  ✓ NRMAPlus_ChoiceOfRepairer: "
                            f"{base_annual_plus} + {delta} = {with_extra}"
                        )
                    else:
                        self.results["NRMAPlus_ChoiceOfRepairer"] = None
                        log.warning("│  ⚠ NRMAPlus_ChoiceOfRepairer: delta missing")
                except Exception as e:
                    log.error(f"│  ✗ NRMAPlus_ChoiceOfRepairer: {e}")
                    self.results["NRMAPlus_ChoiceOfRepairer"] = None
            else:
                log.warning(
                    "│  ⚠ Skipping NRMAPlus extras — no base annual or agreed value"
                )

            # Reset to no extras
            try:
                self._fetch_offer("COMPREHENSIVE", self.default_excess, extras=[])
            except Exception:
                pass

            # Phase 4: Roadside Assist via /price PUT
            # Roadside uses /price PUT with AddRoadsideAssistance=True
            # This is the ONLY remaining use of the /price endpoint
            log.info("│  Phase 4: Roadside Assist")
            for cover_enum, prefix in [
                ("COMPREHENSIVE", "NRMA"),
                ("COMPREHENSIVE_PLUS", "NRMAPlus"),
            ]:
                base_raw = self.results.get(f"{prefix}_YearlyPayment")
                if not base_raw:
                    continue
                try:
                    payload = self._build_price_payload(cover_enum, self.default_excess)
                    payload["AddRoadsideAssistance"] = True
                    resp_rsa = self._request(
                        self._api(f"nrma/motor/experience3/{self.quote_id}/price"),
                        "PUT", payload,
                    )
                    billing = resp_rsa.get("quoteData", {}).get(
                        "billingFrequencyPrices", {}
                    )
                    rsa_annual = billing.get("ANNUAL")
                    if rsa_annual:
                        self.results[f"{prefix}_RoadsideAssist"] = str(rsa_annual)
                        log.info(f"│  ✓ {prefix}_RoadsideAssist: ${rsa_annual}")
                    else:
                        log.warning(
                            f"│  ⚠ {prefix}_RoadsideAssist: no price in response"
                        )
                except Exception as e:
                    log.error(f"│  ✗ {prefix}_RoadsideAssist: {e}")

            # ── FIX 2: Excess sweep now uses Experience3OfferProcessing
            # (same endpoint as base pricing) instead of /price PUT.
            # This guarantees all excess prices come from the same source,
            # eliminating the cross-endpoint price mismatch that caused
            # NRMA_excess_1000 < NRMA_excess_1250.
            # The default excess price is reused directly from YearlyPayment
            # (safe because it also came from Experience3OfferProcessing).
            log.info("│  Phase 5: Excess sweep (via Experience3OfferProcessing)")
            if not self._available_excesses:
                log.warning("│  ⚠ No available excesses — skipping sweep")
            else:
                log.info(
                    f"│  Sweeping {len(self._available_excesses)} excesses: "
                    f"{self._available_excesses}"
                )
                for cover_enum, prefix in self.COVER_TYPES.items():

                    # FIX 1 (excess sweep): If NRMABasic was detected as
                    # unavailable (temp cleared), skip its excess sweep entirely
                    if cover_enum == "COMPREHENSIVE_BASICS" and not self._basic_billing_temp:
                        log.info(
                            f"│  ℹ Skipping {prefix} excess sweep — "
                            f"Basic not available for this profile"
                        )
                        continue

                    for exc in self._available_excesses:
                        exc_str = str(exc)
                        col = f"{prefix}_excess_{exc_str}"
                        if col not in self.results:
                            continue

                        # Reuse the already-captured default excess price.
                        # Safe: YearlyPayment came from the same endpoint
                        # (Experience3OfferProcessing), so prices are comparable.
                        if exc_str == str(self.default_excess):
                            annual = self.results.get(f"{prefix}_YearlyPayment")
                            if annual:
                                self.results[col] = annual
                                log.info(
                                    f"│  ✓ {prefix} excess ${exc_str}: ${annual} "
                                    f"(reused — same endpoint)"
                                )
                                continue

                        # FIX 2: Use Experience3OfferProcessing for all
                        # non-default excess values — same endpoint as base price
                        try:
                            agreed_val = (
                                str(self._agreed_value_avg)
                                if cover_enum == "COMPREHENSIVE_PLUS"
                                and self._agreed_value_avg
                                else None
                            )
                            resp = self._fetch_offer(
                                cover_enum=cover_enum,
                                excess=exc_str,
                                extras=[],
                                agreed_value=agreed_val,
                            )
                            novus = resp.get("novusContext", {})
                            billing = self._extract_billing_from_novus(novus)
                            annual = billing.get("ANNUAL")
                            self.results[col] = annual
                            log.info(
                                f"│  ✓ {prefix} excess ${exc_str}: ${annual}"
                            )
                        except Exception as e:
                            log.error(f"│  ✗ {col}: {e}")
                            self.results[col] = None

        # ── Finalise
        self.results["QuoteNumber"] = self.quote_id
        self.results["QuoteDate"] = datetime.now().strftime("%d/%m/%Y")
        self.results["Timestamp (in hh:mm)"] = datetime.now().strftime("%H:%M")
        self.results["QuoteLink"] = (
            f"{self.BASE_URL}/get-cover/motor/3?quoteId={self.quote_id}"
        )

        sense_issues = self._sense_check()
        if sense_issues:
            log.warning(
                f"│  ⚠ {len(sense_issues)} sense check issue(s) found and corrected"
            )

        log.info("└─ Premiums collected")

    # ─────────────────────────────────────────────────────────────────────────
    # Runner
    # ─────────────────────────────────────────────────────────────────────────
    def run(self) -> dict:
        """Runner — closes HTTP session on completion."""
        run_start = time.time()
        try:
            self.init_session()
            self.step_vehicle_search()
            self.step_policy_date()
            self.step_address()
            self.step_parking()
            self.step_kilometres()
            self.step_usage()
            self.step_finance()
            self.step_insurance()
            self.step_parties()
            self.step_collect_premiums()
            elapsed = time.time() - run_start
            log.info(
                f"✓ COMPLETE — {self.quote_id} in {elapsed:.1f}s "
                f"({self._request_count} requests)"
            )
            for pf_label, pf_key in [
                ("Basic", "NRMABasic"),
                ("Std", "NRMA"),
                ("Plus", "NRMAPlus"),
            ]:
                log.info(
                    f"  {pf_label}: "
                    f"Annual=${self.results.get(f'{pf_key}_YearlyPayment', 'N/A')}"
                )
            return self.results
        except Exception as e:
            elapsed = time.time() - run_start
            log.error(
                f"✗ FAILED — {self.quote_id or 'no quote'} in {elapsed:.1f}s: "
                f"{type(e).__name__}: {e}"
            )
            log.debug(traceback.format_exc())
            self.results["Reason_for_quote_failure"] = f"{type(e).__name__}: {e}"
            return self.results
        finally:
            try:
                self.zyte.client.close()
            except Exception:
                pass


# ─────────────────────────────────────────────────────────────────────────────
# Batch runner
# ─────────────────────────────────────────────────────────────────────────────
# Persist the output every N collected rows. The whole CSV is rewritten on each
# save, so flushing per-quote is O(n²) on large inputs; batching cuts that while
# keeping resume granularity tight (unsaved rows are retryable on the next run).
SAVE_EVERY = 10

# Failure reasons that are transient — the sample is re-run on the next pass.
# A reason matching none of these is treated as terminal (genuine decline, no
# address, no vehicle match, bad input) and NOT retried.
#
# Aligned with the messages raised after the Zyte refactor, e.g.
#   "ZyteError: Zyte 520 /download/website-ban: ..."
#   "RuntimeError: HTTP 428 sec-cp-challenge — .../Experience3UpdatePartiesProcessing"
#   "RuntimeError: HTTP 503 — ...: ..."
RETRYABLE_ERRORS = [
    # ── Network / transport ──────────────────────────────────────────────
    "ConnectionError", "ConnectionResetError", "RemoteDisconnected",
    "ChunkedEncodingError", "SSLError", "ProxyError",
    "Timeout", "ReadTimeout", "ConnectTimeout", "HTTPError",
    # ── Zyte-API level (ban / temporary) — a fresh session+IP recovers ───
    "ZyteError", "website-ban", "temporary-error", "/download/",
    "All Zyte retries exhausted",
    # ── Akamai crypto challenge not cleared this attempt ─────────────────
    "sec-cp-challenge", "HTTP 428",
    # ── Transient auth — 401 fires on the first API call after the token is
    #    minted (token/session not yet propagated in the pinned Zyte session);
    #    a fresh session almost always clears it. Seen at ProcessingVehicleSearch
    #    and occasionally Experience3UpdatePartiesProcessing. ────────────────
    "HTTP 401", "Unauthorized",
    # ── Transient upstream HTTP statuses ─────────────────────────────────
    "HTTP 408", "HTTP 409", "HTTP 425", "HTTP 429",
    "HTTP 500", "HTTP 502", "HTTP 503", "HTTP 504",
    "502", "503", "504", "429", "403",  # legacy reasons from earlier runs
    # ── Auth / session hiccups ───────────────────────────────────────────
    "Failed to get access token", "Token exchange", "Request failed after",
    # ── Degraded-session symptoms the IP-pinning fix should resolve ──────
    "default_excess is None", "BaseExcess was never captured",
    "PartyId not returned",
]


def _is_retryable(reason: str) -> bool:
    if not reason or str(reason).strip().lower() in ("", "nan", "none"):
        return False
    reason_l = str(reason).lower()
    return any(p.lower() in reason_l for p in RETRYABLE_ERRORS)


# Input column that flags whether a profile should be quoted at all.
QUOTE_FLAG_COLUMN = "Quote this row?"
_SKIP_VALUES = {"no", "n", "false", "0"}


def _should_quote_row(row: dict) -> bool:
    """Honour the input's 'Quote this row?' flag: skip the profile only when it
    explicitly says No. A missing column or any other value defaults to quoting,
    so inputs without the column behave exactly as before.
    """
    val = row.get(QUOTE_FLAG_COLUMN)
    if val is None:
        return True
    return str(val).strip().lower() not in _SKIP_VALUES


def _has_required_prices(row, toggle: bool) -> bool:
    """A successful row must carry the headline price, and in toggle mode it must
    also have the excess sweep populated. Partial rows (e.g. base prices saved by
    an earlier code version but no excess) count as incomplete and are re-run.
    """
    def filled(v) -> bool:
        return str(v).strip().lower() not in ("", "nan", "none")

    if not filled(row.get("NRMA_YearlyPayment", "")):
        return False
    if toggle:
        # at least one collected excess value must be present
        return any(
            filled(v)
            for k, v in row.items()
            if isinstance(k, str) and k.startswith("NRMA_excess_")
        )
    return True


def _load_completed(output_path, toggle: bool = False):
    try:
        df = pd.read_csv(output_path, dtype=str)
        completed = set()
        retry_count = 0
        incomplete_count = 0
        for _, row in df.iterrows():
            sid = str(row.get("Sample_Number", "")).strip()
            if not sid:
                continue
            reason = str(row.get("Reason_for_quote_failure", "")).strip()
            if not reason or reason.lower() in ("", "nan", "none"):
                # No failure recorded — only "done" if the prices are actually
                # there (and, in toggle mode, the excess sweep too).
                if _has_required_prices(row, toggle):
                    completed.add(sid)
                else:
                    incomplete_count += 1
            elif _is_retryable(reason):
                retry_count += 1
            else:
                completed.add(sid)
        log.info(
            f"  {len(completed)} done, {retry_count} will retry, "
            f"{incomplete_count} incomplete (re-run)"
        )
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

def run_all_quotes(csv_path: str, output_path: str, toggle: bool = False):
    log.info(f"Input:  {csv_path}")
    log.info(f"Output: {output_path}")
    log.info(f"Mode:   {'FULL TOGGLE' if toggle else 'DEFAULT EXCESS ONLY'}")

    completed = _load_completed(output_path, toggle=toggle)
    existing = []
    try:
        existing = pd.read_csv(output_path, dtype=str).to_dict(orient="records")
    except (FileNotFoundError, pd.errors.EmptyDataError):
        pass

    df = pd.read_csv(csv_path).convert_dtypes()
    rows = df.where(pd.notna(df), None).to_dict(orient="records")

    # Skip profiles flagged "Quote this row? = No" in the input.
    total_loaded = len(rows)
    rows = [r for r in rows if _should_quote_row(r)]
    skipped = total_loaded - len(rows)
    if skipped:
        log.info(f"Skipped {skipped} row(s) flagged '{QUOTE_FLAG_COLUMN} = No'")

    pending = [
        r for r in rows
        if str(r.get("Sample_Number", "")).strip() not in completed
    ]
    log.info(
        f"Quotable: {len(rows)}, Completed: {len(completed)}, Pending: {len(pending)}"
    )
    if not pending:
        log.info("Nothing to do!")
        return

    new_results = []
    fields_order = None
    ok = fail = 0

    def _flush():
        if new_results and fields_order:
            _save(existing, new_results, fields_order, output_path)

    try:
        for i, row in enumerate(pending):
            sid = row.get("Sample_Number", "?")
            log.info(f"{'─' * 60} {i + 1}/{len(pending)} — Sample {sid}")

            try:
                ext = NrmaExtractor(row, toggle=toggle)
                if fields_order is None:
                    fields_order = ext.fields_order
                result = ext.run()
                result["Sample_Number"] = sid
                new_results.append(result)
                if result.get("Reason_for_quote_failure"):
                    fail += 1
                else:
                    ok += 1
            except Exception as e:
                fail += 1
                new_results.append({
                    "Sample_Number": sid,
                    "Reason_for_quote_failure": str(e),
                })

            # Persist periodically (full-CSV rewrite is O(n²) per quote), and
            # always at the end via the finally below. Lost rows on a hard kill
            # are retryable, so the next run just re-collects them.
            if len(new_results) % SAVE_EVERY == 0:
                _flush()
    finally:
        _flush()

    log.info(f"Done — OK: {ok}, Failed: {fail}, Saved: {output_path}")

if __name__ == "__main__":
    run_all_quotes(
        csv_path="/home/wmc/WMC/DataManipulation/Suncorp/NRMA/inputs/202608/data/Motor_NRMA_quarterly_202608_main_newaddress.csv",
        output_path="/home/wmc/WMC/DataManipulation/Suncorp/NRMA/outputs/202608/Motor_NRMA_quarterly_202608_main.csv",
        toggle=False,
    )
    
    # run_all_quotes(
    #     csv_path="/home/wmc/WMC/DataManipulation/Suncorp/NRMA/inputs/202608/data/Motor_NRMA_quarterly_202608_toggle_newaddress.csv",
    #     output_path="/home/wmc/WMC/DataManipulation/Suncorp/NRMA/outputs/202608/Motor_NRMA_quarterly_202608_toggle.csv",
    #     toggle=True,
    # )

