# main.py — Optimized Youi Motors Flow Engine
import base64
import json
import logging
import os
import re
import time
from datetime import datetime
from typing import Any, Dict, Hashable, List, Optional, Tuple
from zoneinfo import ZoneInfo

import pandas as pd
import requests
from fuzzywuzzy import fuzz, process
from requests import RequestException
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ==============================================================================
# CONFIG & LOGGING
# ==============================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("YouiScraper")

# Per-record request/response traces. This was hardcoded to outputs/202608/trace,
# so a 20260817 run wrote its traces into the already-delivered July folder.
TRACE_DIR = os.getenv(
    "YOUI_TRACE_DIR",
    "/home/wmc/WMC/DataManipulation/Suncorp/Youi/outputs/20260817/trace",
)
os.makedirs(TRACE_DIR, exist_ok=True)


# ==============================================================================
# CONSTANTS
# ==============================================================================
CONSTANTS = {
    "YES_NO": {"No": "808", "Yes": "807"},
    "COVER_TYPE": {
        "Comprehensive": "COMP",
        "Third Party Fire And Theft": "LTDC",
        "Third Party Property Damage": "OTHER",
    },
    "VEHICLE_USAGE": {"Private": "12439", "Business": "10384"},
    "TRIPS_TO_WORK": {'0': '10724','1': '10724', '2': '10725', '3': '10726', '4': '10727', '5': '10728', '6': '10729', '7': '10730'},
    "COMMUTING": {"Yes": "658", "No": "669"},
    "TRAVEL": {
        "0-5,000km": "14473",
        "5,001-10,000km": "14474",
        "10,001-15,000km": "14475",
        "15,001-20,000km": "14476",
        "20,001-30,000km": "14477",
        "30,001+km": "14478",
    },
    "GENDERS": {"M": "Male", "F": "Female"},
    "DEMERITS": {
        "0": "10539",
        "Unsure": "10804",
        "1 To 3 Points": "10792",
        "4 To 6 Points": "10795",
        "7 To 12 Points": "10798",
        "More Than 12 Points": "10775",
    },
    "MOBILE_PHONE": {
        "Never": "12395",
        "Without A Handsfree Kit - Up To 2 Times Per Week": "12396",
        "Without A Handsfree Kit - More Than 2 Times Per Week": "12397",
        "With A Handsfree Kit - Up To 2 Times Per Week": "12398",
        "With A Handsfree Kit - More Than 2 Times Per Week": "12399",
    },
    "INCIDENT_TYPES": {
        "Not At Fault Collision": "10823",
        "Accident Or Any Other Damage": "5541",
        "Glass/windscreen Only": "5547",
        "Loss, Theft Or Attempted Theft": "5562",
        "Natural Hazard ( Hail, Storm, Bushfire Etc.)": "5591",
    },
    "INSURERS": {
        "Nrma": "057", "Racq": "079", "Apia": "070", "Gio": "061",
        "Youi": "087", "Aami": "056", "Not Insured": None,
        "Allianz": "060", "Raa": "079", "Racv": "079",
        "Budget Direct": "058", "Suncorp": "083", "Other": "038",
        "Woolworths": "011", "Ract": "079", "Rac": "079",
        "Coles": "003", "Real Insurance": "059", "Cgu": "073",
        "Sgio": "090", "Shannons": "082", "Qbe": "086",
        "Comminsure": "065", "Sgic": "090", "Tio": "090",
        "Westpac": "085",
    },
}

COLORS = [
    {"key": "516", "value": "Beige"},
    {"key": "509", "value": "Black"},
    {"key": "510", "value": "Blue"},
    {"key": "521", "value": "Brown"},
    {"key": "523", "value": "Burgundy"},
    {"key": "525", "value": "Charcoal"},
    {"key": "522", "value": "Gold"},
    {"key": "511", "value": "Green"},
    {"key": "526", "value": "Grey"},
    {"key": "520", "value": "Maroon"},
    {"key": "512", "value": "Orange"},
    {"key": "524", "value": "Pink"},
    {"key": "513", "value": "Purple"},
    {"key": "514", "value": "Red"},
    {"key": "519", "value": "Silver"},
    {"key": "10661", "value": "Champagne"},
    {"key": "515", "value": "White"},
    {"key": "518", "value": "Yellow"},
]


# ==============================================================================
# UTILS
# ==============================================================================
def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (ValueError, TypeError):
        return default


def find_best_match(
    user_input: str,
    lookup_list: List[Dict],
    parameter: str = "description",
    threshold: int = 40,
) -> Dict:
    user_input = str(user_input)
    if not lookup_list:
        logger.warning(
            f"⚠️ Lookup list empty. Cannot match: '{user_input}'"
        )
        return {}

    if user_input.lower() == "mg":
        user_input = "M.G."

    exact = next(
        (
            item
            for item in lookup_list
            if item.get(parameter, "").lower() == user_input.lower()
        ),
        None,
    )
    if exact:
        logger.info(
            f"✅ Exact Match: '{user_input}' -> "
            f"'{exact.get(parameter)}' (Key: {exact.get('key')})"
        )
        return exact

    choices = [item.get(parameter, "") for item in lookup_list]
    result = process.extractOne(
        user_input, choices, scorer=fuzz.token_sort_ratio
    )

    if result:
        best_match_str, score = result[:2]
        if score >= threshold:
            matched = next(
                item
                for item in lookup_list
                if item.get(parameter) == best_match_str
            )
            logger.info(
                f"🔍 Fuzzy Match: '{user_input}' -> "
                f"'{best_match_str}' (Score: {score})"
            )
            return matched

    logger.warning(
        f"❌ No Match for '{user_input}'. "
        f"Defaulting to: '{lookup_list[0].get(parameter)}'"
    )
    return lookup_list[0]


def format_values(data: dict) -> dict:
    result = {}
    for key, value in data.items():
        if isinstance(value, str):
            result[key] = value.title()
        elif isinstance(value, float) and value.is_integer():
            result[key] = str(int(value))
        elif isinstance(value, (int, float)):
            result[key] = str(value)
        else:
            result[key] = value
    return result


class KnockoutException(Exception):
    """A genuine underwriting decline — the whole quote is uninsurable,
    not just the specific premium variant being calculated when it hit."""


# ==============================================================================
# FLOW STATE TRACKER
# ==============================================================================
class FlowStateTracker:
    """Tracks server state, step guards, and dynamic data."""

    def __init__(self):
        self.step_guards: Dict[str, bool] = {}
        self.current_step: Optional[str] = None
        self.switch_flow_decision: str = "None"
        self.switch_flow_service_decision: str = "None"
        self.has_client_made_switch_decision: bool = False
        self.lead_generation_status: str = "NotApplicable"
        self.fringe_hours: bool = False
        self.sales_channel: str = "Online"
        self.optional_covers: List[Dict] = []
        self.cover_index_map: Dict[str, int] = {}
        self.excess_list: List[float] = []
        self.default_excess: float = 0
        self.knockouts_found: bool = False
        self.knockout_message: str = ""
        self.quote_number: str = ""
        self.warnings: List[str] = []
        self.validation_errors: List[Dict] = []

    def update_from_start_flow(self, data: Dict):
        """Extract initial state from start-flow response."""
        self.fringe_hours = data.get("fringeHours", False)
        self.sales_channel = data.get("salesChannel", "Online")
        self.switch_flow_decision = data.get(
            "switchFlowDecision", "None"
        )
        self.lead_generation_status = data.get(
            "leadGenerationStatus", "NotApplicable"
        )
        self.has_client_made_switch_decision = data.get(
            "hasClientMadeSwitchFlowDecision", False
        )

        guards = data.get("stepGuards", {})
        self.step_guards.update(guards)

        self.quote_number = str(
            data.get("summitQuoteNumber")
            or (data.get("displayedQuoteNumber") or "")
            .split(" ")[-1].lstrip("#").strip()
            or ""
        )

        logger.info(
            f"📊 Initial State: fringeHours={self.fringe_hours}, "
            f"salesChannel={self.sales_channel}, "
            f"switchFlow={self.switch_flow_decision}, "
            f"leadGen={self.lead_generation_status}, "
            f"quoteNumber={self.quote_number}"
        )

    def update_from_response(self, resp: Any) -> bool:
        """Parse dataDeltaOperations and update tracked state.

        Returns True if THIS response reported a fresh knockout
        (knockouts_found itself is sticky for logging/inspection, but
        callers should gate on the return value — a knockout tripped
        by one probe step must not silently kill every later step).
        """
        if not isinstance(resp, dict):
            return False

        knockout_in_this_response = False
        for op in resp.get("dataDeltaOperations", []):
            path = op.get("path", "")
            value = op.get("value")

            # Step guards
            if path.startswith("/stepGuards/"):
                step_name = path.replace("/stepGuards/", "")
                self.step_guards[step_name] = bool(value)
                logger.info(
                    f"🚦 StepGuard: {step_name} = {value}"
                )

            # Current step
            elif path == "/currentStepName":
                self.current_step = value

            # Switch flow
            elif path == "/switchFlowDecision":
                self.switch_flow_decision = value
                logger.info(
                    f"🔀 SwitchFlow updated: {value}"
                )
            elif path == "/switchFlowServiceDecision":
                self.switch_flow_service_decision = value

            # Knockouts
            elif path == "/underwriting/knockouts":
                if isinstance(value, list) and value:
                    messages = [
                        ko.get("screenMessage")
                        for ko in value
                        if isinstance(ko, dict)
                        and ko.get("isReadable")
                        and ko.get("screenMessage")
                    ]
                    if messages:
                        self.knockout_message = " ".join(messages)
            elif "knockoutsFound" in path and value is True:
                self.knockouts_found = True
                knockout_in_this_response = True
                logger.error(
                    f"🚨 KNOCKOUT FOUND: {path} - "
                    f"{self.knockout_message}"
                )

            # Validation errors
            elif path == "/validationResults":
                if isinstance(value, list) and value:
                    self.validation_errors = value
                    for err in value:
                        logger.warning(
                            f"⚠️ Validation: "
                            f"{err.get('errorMessage')}"
                        )

            # Optional covers
            elif path == "/vehiclePremium/optionalCoverOptions":
                self._parse_optional_covers(value)
            elif path == "/vehiclePremium":
                if isinstance(value, dict):
                    covers = value.get("optionalCoverOptions", [])
                    if covers:
                        self._parse_optional_covers(covers)
                    el = value.get("excessList", [])
                    if el:
                        self.excess_list = el
                    de = value.get("defaultExcess")
                    if de:
                        self.default_excess = de

            # Excess
            elif path == "/vehiclePremium/excessList":
                self.excess_list = value or []
            elif path == "/vehiclePremium/defaultExcess":
                self.default_excess = value

        return knockout_in_this_response

    def _parse_optional_covers(self, covers: List[Dict]):
        """Build dynamic cover index map from server response."""
        self.optional_covers = covers
        self.cover_index_map = {}
        for idx, cover in enumerate(covers):
            code = cover.get("code", "")
            self.cover_index_map[code] = idx
            logger.info(
                f"📋 Cover [{idx}] {code}: "
                f"{cover.get('title', 'Unknown')} "
                f"(selected={cover.get('isSelected', False)})"
            )

    def is_step_available(self, step_name: str) -> bool:
        return self.step_guards.get(step_name, False)

    def needs_switch_flow(self) -> bool:
        return (
            self.switch_flow_decision
            not in ("None", "ContinueOnline", "ContinueOnlineLead")
            and not self.has_client_made_switch_decision
        )

    def get_cover_index(self, code: str) -> Optional[int]:
        return self.cover_index_map.get(code)

    def has_cover(self, code: str) -> bool:
        return code in self.cover_index_map


# ==============================================================================
# RESPONSE CAPTURE (for debugging)
# ==============================================================================
class ResponseCapture:
    def __init__(self, filepath: Optional[str] = None):
        self.filepath = filepath
        self.entries = []
        self.enabled = filepath is not None

    def capture(self, method, url, request_body, status, response):
        if not self.enabled:
            return
        self.entries.append({
            "index": len(self.entries),
            "timestamp": datetime.now().isoformat(),
            "method": method,
            "url": url,
            "request_body": request_body,
            "response_status": status,
            "response_body": response,
        })
        self._save()

    def _save(self):
        if self.filepath:
            with open(self.filepath, "w") as f:
                json.dump(self.entries, f, indent=2, default=str)


# ==============================================================================
# API CLIENT
# ==============================================================================
class YouiApiClient:
    BASE_URL = "https://quotes.youi.com.au"

    def __init__(
        self,
        headers: Dict,
        proxies: Dict,
        cookies: Optional[Dict] = None,
        timeout: int = 180,
        capture_path: Optional[str] = None,
        session=None,
    ):
        # `session` lets a caller supply youi_browser.BrowserSession, which issues
        # each call as same-origin fetch() from a real page. Youi's WAF routes
        # sessions it doesn't recognise to the call centre and never prices them,
        # so a plain requests.Session cannot complete a quote any more.
        self.session = session if session is not None else requests.Session()
        self.session.headers.update(headers)
        self.session.proxies.update(proxies)
        if cookies:
            self.session.cookies.update(cookies)
        self.timeout = timeout
        self.capture = ResponseCapture(capture_path)

    def set_auth(self, token: str):
        self.session.headers["Authorization"] = f"Bearer {token}"

    def request(
        self,
        path: str,
        method: str = "GET",
        payload: Optional[Dict] = None,
        params: Optional[Dict] = None,
        retries: int = 3,
    ) -> Any:
        url = f"{self.BASE_URL}{path}"
        for attempt in range(retries + 1):
            try:
                resp = self.session.request(
                    method,
                    url,
                    json=payload,
                    params=params,
                    timeout=self.timeout,
                    verify=False,
                )
                if (
                    resp.status_code in {429, 500, 502, 503, 504}
                    and attempt < retries
                ):
                    wait = 0.5 * (2 ** attempt)
                    logger.warning(
                        f"⚠️ {resp.status_code} at {path}, "
                        f"retry {attempt + 1}/{retries} "
                        f"in {wait}s"
                    )
                    time.sleep(wait)
                    continue

                # raise_for_status() reports only the status line, so a 400 from
                # the BFF arrived as a bare "400 Client Error" with the field-level
                # reason — the only thing that says WHICH answer it rejected —
                # thrown away. Log the body before raising.
                if resp.status_code >= 400:
                    logger.error(
                        f"❌ {resp.status_code} at {path} — response body: "
                        f"{resp.text[:1000]}"
                    )
                resp.raise_for_status()

                try:
                    response_body = resp.json()
                except ValueError:
                    response_body = resp.text

                self.capture.capture(
                    method, url,
                    payload or params,
                    resp.status_code,
                    response_body,
                )

                return response_body

            except RequestException as e:
                if attempt >= retries:
                    self.capture.capture(
                        method, url,
                        payload or params,
                        "ERROR",
                        str(e),
                    )
                    logger.error(
                        f"❌ Request failed after {retries} "
                        f"retries: {e}"
                    )
                    raise
                time.sleep(0.5 * (2 ** attempt))


# ==============================================================================
# MAIN FLOW ENGINE
# ==============================================================================
class YouiMotorsNewFlow:
    def __init__(self, input_data: Dict[Hashable, Any], browser=None):
        # `browser` is (youi_browser.BrowserSession, initialize_body) from
        # youi_browser.quote_session(). Supplied → the whole quote runs through a
        # real page; omitted → the legacy Zyte-render bootstrap, which the WAF now
        # routes to the call centre.
        self.browser = browser
        self.input = input_data
        self.session_id = None
        self.flow_state_id = None
        self.address_id = None
        self.driver_id = None
        self.mid_agreed_value = 0
        self.state = FlowStateTracker()

        logger.info("=" * 60)
        logger.info(
            f"🚀 STARTING FLOW: {self.input.get('SAMPLE_NUMBER')}"
        )
        logger.info("=" * 60)

        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:147.0) "
                "Gecko/20100101 Firefox/147.0"
            ),
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Content-Type": "application/json",
            "Origin": "https://quotes.youi.com.au",
            # The live site sends this on every BFF call; captured from a real
            # browser render alongside stratos-flow-state-id (set per session in
            # start_quote once the flowStateId is known).
            "x-stratos-location-origin": "https://quotes.youi.com.au",
            "Connection": "keep-alive",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        }

        self.ZYTE_API_KEY = "4d39f5403fa24cf6a31cf8873b3d9101"
        zyte_proxy = f"http://{self.ZYTE_API_KEY}:@api.zyte.com:8011/"
        self.proxies = {
            "http": zyte_proxy,
            "https": zyte_proxy,
        }

        aest_tz = ZoneInfo("Australia/Sydney")
        now = datetime.now(aest_tz)

        self.output = {
            "SAMPLE_NUMBER": self.input.get("SAMPLE_NUMBER"),
            "Postcode": self.input.get("POSTCODE"),
            "DOB_day": self.input.get("DOB_DUMMY_DAY"),
            "DOB_month": self.input.get("DOB_DUMMY_MONTH"),
            "DOB_year": self.input.get("DOB_DUMMY_YEAR"),
            "QuoteDate": now.strftime("%d-%b-%y"),
            "Timestamp AEST (in hh:mm)": now.strftime("%H:%M"),
            "DeducedVehicleSelection": "",
            "QuoteLink": "",
            "QuoteNumber": "",
            "DefaultExcess": "",
            "MonthlyPayment": "",
            "YearlyPayment": "",
            "RoadsideAssist": "",
            "HireCar": "",
            "WindowGlassExcess": "",
            "BusinessItemsCover": "",
            "AgreedValue": "",
            "MiddleAgreedValueSI": "",
            "Reason_for_quote_failure": "",
            "Remark (if any)": "",
        }

        # Generate excess columns
        for e in range(500, 2550, 50):
            self.output[f"excess{e}"] = ""

    # ------------------------------------------------------------------
    # Helper: resolve mapping
    # ------------------------------------------------------------------
    def _resolve(self, category: str, raw_input: Any) -> str:
        mapping = CONSTANTS.get(category, {})
        if category == "TRIPS_TO_WORK":
            key = str(safe_int(raw_input))
        elif category == "TRAVEL":
            key = str(raw_input).lower()
        else:
            key = str(raw_input).title()
        result = mapping.get(key, "NOT_FOUND")
        if result == "NOT_FOUND":
            logger.warning(
                f"⚠️ Mapping [{category}]: '{raw_input}' "
                f"-> NOT FOUND"
            )
        return result

    # ------------------------------------------------------------------
    # Helper: persist (with state tracking)
    # ------------------------------------------------------------------
    def _persist(self, endpoint: str, ops: List[Dict], **kwargs):
        payload = {
            "dataOperations": ops,
            "context": {
                "surrogateQuoteId": self.session_id,
                "riskCode": "VEH",
            },
            "flowStateId": self.flow_state_id,
        }
        if kwargs.get("additional_data") and kwargs.get("child_data"):
            payload["additionalData"] = kwargs["additional_data"]
            payload["childDataKey"] = kwargs["child_data"]

        resp = self.client.request(
            f"/bff/api/v1/persist-flow/dcq/vehicle/quote/{endpoint}",
            "PATCH",
            payload,
        )
        knockout_hit = self.state.update_from_response(resp)

        if knockout_hit:
            raise KnockoutException(
                self.state.knockout_message
                or f"Knockout detected at step '{endpoint}'"
            )

        return resp

    def _patch_data(self, step: str):
        payload = {
            "dataOperations": [],
            "context": {
                "surrogateQuoteId": self.session_id,
                "riskCode": "VEH",
            },
            "flowStateId": self.flow_state_id,
        }
        resp = self.client.request(
            f"/bff/api/v1/data/dcq/vehicle/quote/{step}",
            "PATCH",
            payload,
        )
        self.state.update_from_response(resp)
        return resp

    def _metric(self, name: str):
        if self.flow_state_id:
            try:
                self.client.request(
                    f"/bff/api/v1/metrics/"
                    f"{self.flow_state_id}/{name}",
                    "POST",
                )
            except Exception:
                pass

    def _lookup(
        self,
        endpoint: str,
        params: Dict = {},
        method: str = "GET",
        payload: Dict = {},
    ):
        resp = self.client.request(
            f"/bff/api/v1/lookups/{endpoint}",
            method,
            payload=payload,
            params=params,
        )
        return resp.get("lookupData", [])

    # ------------------------------------------------------------------
    # Step 0: Bootstrap a real session via Zyte's managed browser
    # ------------------------------------------------------------------
    # Youi gates /bff/api/v1/initialize behind a JS bot-detection sensor
    # (commonbd.js). Plain `requests` never passes it (confirmed: even
    # replaying real captured browser cookies/fingerprint headers still
    # gets the redirectUrl gate), and Playwright automation stalls on
    # the same sensor regardless of headless/headed. Zyte's browser
    # rendering (a different product from the :8011 proxy used below)
    # runs a real, unblocked browser server-side, so we let it click
    # through quote-start -> Car -> Continue and pull the sessionId /
    # flowStateId / authorizationToken out of its network capture.
    def _bootstrap_session(self, retries: int = 2) -> Dict:
        payload = {
            "url": "https://www.youi.com.au/quote-start",
            "browserHtml": True,
            "actions": [
                {
                    "action": "click",
                    "selector": {
                        "type": "css",
                        "value": "label[for='toggleGroup-Car']",
                    },
                },
                {
                    "action": "click",
                    "selector": {
                        "type": "css",
                        "value": "#quoteStartContinueButton",
                    },
                },
                {"action": "waitForTimeout", "timeout": 15},
            ],
            "networkCapture": [
                {
                    "filterType": "url",
                    "value": "bff/api/v1/initialize",
                    "matchType": "contains",
                    "httpResponseBody": True,
                }
            ],
        }

        for attempt in range(retries + 1):
            logger.info(
                f"🔑 Bootstrapping session via Zyte browser render "
                f"(attempt {attempt + 1}/{retries + 1})..."
            )
            resp = requests.post(
                "https://api.zyte.com/v1/extract",
                auth=(self.ZYTE_API_KEY, ""),
                json=payload,
                timeout=90,
            )
            resp.raise_for_status()
            captures = resp.json().get("networkCapture", [])
            if not captures:
                # The bot-detection sensor is timing-sensitive — it
                # doesn't always clear within the 15s window. This is
                # a flake, not a hard block, so retry before giving up.
                logger.warning(
                    "⚠️ Bot challenge didn't clear in time, retrying..."
                    if attempt < retries else
                    "⚠️ Bot challenge didn't clear after all retries"
                )
                continue

            body = json.loads(
                base64.b64decode(captures[0]["httpResponseBody"])
            )
            # Youi renamed the session handle: /initialize used to answer with
            # `sessionId` and now answers with `surrogateQuoteId` (the `sqid` the
            # browser posts). The old check treated the renamed-but-perfectly-good
            # response as a failed bot challenge and burned all three retries per
            # record, so every row failed with a misleading reason.
            session_id = body.get("sessionId") or body.get("surrogateQuoteId")
            if not session_id:
                logger.warning(
                    f"⚠️ No session handle in bootstrapped response: {body}"
                )
                continue
            body["sessionId"] = session_id
            # Youi now ships a `configure-enhanced-routing` feature flag
            # (internally enableWafBotDetection). A session the WAF doesn't
            # recognise is not blocked — it is silently routed to the call centre
            # and never priced, which is why every record came back with
            # switchFlowDecision=CallCentre and 200s carrying zero deltas.
            # Carry the rendered browser's own identity (cookies, fingerprint and
            # sensor headers) onto the API session so the WAF sees one session.
            body["_browserHeaders"] = (
                (captures[0].get("request") or {}).get("headers") or {}
            )
            return body

        raise Exception(
            "Zyte browser render never triggered "
            "/bff/api/v1/initialize after retries — bot challenge "
            "consistently failed to clear"
        )

    # Headers requests/urllib3 must own, or that we set ourselves elsewhere.
    # Everything else the rendered browser sent is forwarded verbatim — the WAF
    # sensor header names are randomised per deployment (hqsudwprpp-* today), so
    # a denylist survives a rotation where an allowlist would not.
    _BROWSER_HEADER_DENYLIST = frozenset({
        "host", "content-length", "connection", "accept-encoding",
        "content-type", "cookie", "authorization", "traceparent",
        "stratos-flow-state-id",
    })

    def _adopt_browser_identity(self, browser_headers: dict) -> None:
        """Copy the rendered browser's cookies + sensor headers onto the session."""
        if not browser_headers:
            logger.warning(
                "⚠️ No browser headers captured — the WAF will see an "
                "unrecognised session and may route this quote to the call centre"
            )
            return

        cookie_header = next(
            (v for k, v in browser_headers.items() if k.lower() == "cookie"), ""
        )
        cookies = 0
        for part in cookie_header.split(";"):
            if "=" not in part:
                continue
            name, value = part.strip().split("=", 1)
            if name:
                self.client.session.cookies.set(
                    name, value, domain="quotes.youi.com.au", path="/"
                )
                cookies += 1

        forwarded = []
        for key, value in browser_headers.items():
            if key.lower() in self._BROWSER_HEADER_DENYLIST:
                continue
            self.client.session.headers[key] = value
            forwarded.append(key)

        logger.info(
            f"🕵️ Adopted browser identity: {cookies} cookies, "
            f"{len(forwarded)} headers ({', '.join(sorted(forwarded)[:8])}…)"
        )

    # ------------------------------------------------------------------
    # Step 1: Initialize & Start
    # ------------------------------------------------------------------
    def start_quote(self):
        logger.info("🎬 Initializing Quote Session...")

        browser_session = None
        if self.browser is not None:
            browser_session, session_data = self.browser
        else:
            session_data = self._bootstrap_session()
        self.session_id = session_data["sessionId"]
        self.flow_state_id = session_data["flowStateId"]

        sample = self.input.get("SAMPLE_NUMBER", "unknown")
        capture_path = os.path.join(TRACE_DIR, f"captured_{sample}.json")
        self.client = YouiApiClient(
            self.headers, self.proxies, capture_path=capture_path,
            session=browser_session,
        )
        if token := session_data.get("authorizationToken"):
            self.client.set_auth(token)
        # The BFF now expects the flow state in a header as well as the body; a
        # real browser render sends stratos-flow-state-id on every call.
        self.client.session.headers["stratos-flow-state-id"] = self.flow_state_id
        if browser_session is None:
            # Only meaningful for the legacy transport; a real page already owns
            # its cookies and has its sensor headers signed per request.
            self._adopt_browser_identity(session_data.get("_browserHeaders") or {})

        logger.info(f"✅ Session: {self.session_id}")

        # Start flow and parse initial state
        start_resp = self.client.request(
            "/bff/api/v1/start-flow/dcq/vehicle/quote",
            "POST",
            {
                # Youi renamed the context key sessionId -> surrogateQuoteId, and
                # start-flow 400s on the old name. Confirmed against the live site:
                # start-flow echoes back
                # context={"surrogateQuoteId": ..., "riskCode": "VEH"}.
                "context": {"surrogateQuoteId": self.session_id},
                "flowStateId": self.flow_state_id,
            },
        )

        start_data = start_resp.get("data", {})
        self.state.update_from_start_flow(start_data)
        if self.state.quote_number:
            self.output["QuoteNumber"] = self.state.quote_number

        # Check fringe hours warning
        if self.state.fringe_hours:
            logger.warning(
                "⚠️ FRINGE HOURS detected — switch flow "
                "gate may appear"
            )

    # ------------------------------------------------------------------
    # Step 2: Vehicle Lookup
    # ------------------------------------------------------------------
    def vehicle_lookup(self):
        self._metric("vehicle-lookup")
        i = self.input

        makers = self._lookup("common/vehicle-makes")
        make = find_best_match(i["PG_GG_MAKE"], makers, "value")

        years = self._lookup(
            "common/vehicle-years",
            method="POST",
            payload={"queryParameters": {"Make": make["key"]}},
        )
        year = find_best_match(i["VEHICLE_YEAR"], years, "value")

        models = self._lookup(
            "common/vehicle-models",
            method="POST",
            payload={
                "queryParameters": {
                    "Make": make["key"],
                    "Year": year["key"],
                }
            },
        )
        model = find_best_match(i["PG_GG_FAMILY"], models, "value")

        types = self._lookup(
            "common/vehicle-types",
            method="POST",
            payload={
                "queryParameters": {
                    "Make": make["key"],
                    "Year": year["key"],
                    "Model": model["key"],
                }
            },
        )
        v_type_input = (
            f"{i['PG_GG_VARIANT']} - {i['PG_GG_SERIES']} - "
            f"{i['PG_GG_BODYSTYLE']}"
        )
        v_type = find_best_match(v_type_input, types, "value")

        styles = self._lookup(
            "common/vehicle-styles",
            method="POST",
            payload={
                "queryParameters": {
                    "Make": make["key"],
                    "Year": year["key"],
                    "Model": model["key"],
                    "Type": v_type["key"],
                }
            },
        )
        style_input = (
            f"{i['PG_GG_TRANS']} - {i['PG_GG_ENGSIZE']}"
        )
        style = find_best_match(style_input, styles, "value")

        descs = self._lookup(
            "common/vehicle-descriptions",
            method="POST",
            payload={
                "queryParameters": {
                    "Make": make["key"],
                    "Year": year["key"],
                    "Model": model["key"],
                    "Type": v_type["key"],
                    "Style": style["key"],
                }
            },
        )
        desc = find_best_match(i["PG_GG_ENGINE"], descs, "value")

        color = find_best_match(i["COLOUR"], COLORS, "value")

        deduced = (
            f"{make['value']} | {model['value']} | "
            f"{year['value']} | {v_type['value']} | "
            f"{style['value']} | {desc['value']} | "
            f"{color['value']}"
        )
        self.output["DeducedVehicleSelection"] = deduced
        logger.info(f"🚘 Vehicle: {deduced}")

        self._persist("vehicle-lookup-pre-next-task", [
            {
                "op": "replace",
                "path": "/currentStepName",
                "value": "vehicle-lookup",
            },
            {
                "op": "add",
                "path": "/vehicleLookup",
                "value": {
                    "selectedLookupOption": "make-and-model",
                    "showRegistrationApiError": False,
                    "vehicleFoundByRegoSearch": "808",
                    "makeNameModelSearch": make["value"],
                    "showRegoSearchInlineLoader": False,
                    "make": make["key"],
                    "year": year["key"],
                    "model": model["key"],
                    "type": v_type["key"],
                    "style": style["key"],
                    "modelCode": desc["key"],
                    "colour": color["key"],
                },
            },
        ])
        return self._patch_data("vehicle-lookup")

    # ------------------------------------------------------------------
    # Step 3: Vehicle Condition
    # ------------------------------------------------------------------
    def vehicle_condition(self):
        self._metric("vehicle-condition")

        cond_list = self._lookup(
            "common/new-used",
            method="POST",
            payload={
                "queryParameters": {
                    "year": self.input["VEHICLE_YEAR"]
                }
            },
        )
        cond = find_best_match(
            "Used - Already Owner", cond_list, "value"
        )

        poss_list = self._lookup("common/possession")
        poss = find_best_match("Yes", poss_list, "value")

        cov_list = self._lookup("common/covered")
        cov = find_best_match(
            self.input.get("PREVIOUSINSURER_COVER", ""),
            cov_list,
            "value",
        )

        no_val = self._resolve("YES_NO", "No")

        resp = self._persist("vehicle-condition-pre-next-task", [
            {
                "op": "replace",
                "path": "/vehicleCondition/carAlreadyInPossession",
                "value": True,
            },
            {
                "op": "add",
                "path": "/vehicleCondition/carCondition",
                "value": cond["key"],
            },
            {
                "op": "add",
                "path": "/vehicleCondition/currentVehicleLocation",
                "value": poss["key"],
            },
            {
                "op": "add",
                "path": "/vehicleCondition/currentInsuranceCover",
                "value": cov["key"],
            },
            {
                "op": "add",
                "path": "/vehicleCondition/currentExistingDamage",
                "value": no_val,
            },
            {
                "op": "replace",
                "path": "/currentStepName",
                "value": "vehicle-condition",
            },
            {
                "op": "add",
                "path": "/underwriting",
                "value": {
                    "readableConditionsOfCover": [],
                    "conditionsOfCover": [],
                },
            },
        ])

        # Extract agreed value range from response
        for dop in resp.get("dataDeltaOperations", []):
            if dop.get("path") == "/vehiclePremium":
                val = dop.get("value", {})
                mn = val.get("minimumSumInsuredAmount", 0)
                mx = val.get("maximumSumInsuredAmount", 0)
                if mn and mx:
                    self.mid_agreed_value = (mx + mn) / 2
                    logger.info(
                        f"💰 Agreed Range: {mn}-{mx}, "
                        f"Mid: {self.mid_agreed_value}"
                    )

        # Handle warning dialog (e.g., tray cover)
        self._persist(
            "mark-active-warning-read-task",
            [],
            additional_data={
                "underwriting": {
                    "activeWarningText": (
                        "This policy automatically provides "
                        "cover for the Tray up to the maximum "
                        "of $2500, if you require additional "
                        "cover please add this value to the "
                        "aftermarket accessories sum insured."
                    )
                }
            },
            child_data="warning-dialog-form",
        )

        self._persist("vehicle-condition-pre-next-task", [])

    # ------------------------------------------------------------------
    # Step 4: Cover Type
    # ------------------------------------------------------------------
    def cover_information(self):
        self._patch_data("vehicle-condition")
        self._metric("covers")

        dt = datetime(
            safe_int(self.input["POLICY_START_YEAR"]),
            safe_int(self.input["POLICY_START_MONTH"]),
            safe_int(self.input["POLICY_START_DAY"]),
        )
        pol_date = dt.strftime("%Y-%m-%dT%H:%M:%S.000")
        cover_code = self._resolve(
            "COVER_TYPE", self.input.get("COVER_TYPE")
        )

        self._persist("cover-pre-next-task", [
            {
                "op": "replace",
                "path": "/currentStepName",
                "value": "covers",
            },
            {
                "op": "add",
                "path": "/cover/coverTypeCode",
                "value": cover_code,
            },
            {
                "op": "add",
                "path": "/riskStartDate",
                "value": pol_date,
            },
        ])

    # ------------------------------------------------------------------
    # Step 5: Vehicle Usage
    # ------------------------------------------------------------------
    def vehicle_usage(self):
        self._patch_data("covers")
        self._metric("vehicle-usages")

        usage_code = self._resolve(
            "VEHICLE_USAGE", self.input.get("PG_VEHICLEUSE1")
        )

        if usage_code == "12439":  # Private
            days_code = self._resolve(
                "TRIPS_TO_WORK", self.input.get("DAYSPERWEEK")
            )
            commute = self._resolve("COMMUTING", "Yes")
            self._persist("vehicle-usages-pre-next-task", [
                {
                    "op": "add",
                    "path": (
                        "/vehicleParking"
                        "/isDaytimeParkingAtSameAddress"
                    ),
                    "value": "808",
                },
                {
                    "op": "replace",
                    "path": "/currentStepName",
                    "value": "vehicle-usages",
                },
                {
                    "op": "add",
                    "path": "/vehicleUsage",
                    "value": {
                        "selectedUsage": usage_code,
                        "selectedIndustryControlKeyRequired": False,
                        "howManyDaysAWeekCommuting": days_code,
                        "usedForCommuting": commute,
                    },
                },
            ])
        else:  # Business
            type_of_business = self.input.get(
                "PG_VEHICLEUSE2", ""
            )
            business_code = re.sub(
                r"[^\d+]", "", type_of_business
            ).strip()
            if not business_code:
                business_code = type_of_business.split()[0]

            industries = self._lookup(
                "common/industries",
                method="GET",
                params={"filter": business_code},
            )
            industry = find_best_match(
                type_of_business, industries, "value"
            )
            radius_list = self._lookup(
                "common/radius-of-operations"
            )
            radius = find_best_match(
                "25 Up To 50km", radius_list, "value"
            )

            self._persist("vehicle-usages-pre-next-task", [
                {
                    "op": "replace",
                    "path": "/currentStepName",
                    "value": "vehicle-usages",
                },
                {
                    "op": "add",
                    "path": "/vehicleUsage",
                    "value": {
                        "selectedUsage": usage_code,
                        "selectedIndustryControlKeyRequired": False,
                        "selectedAnzSicCode": industry.get(
                            "data", {}
                        ).get("ANZSICCode"),
                        "selectedIndustryCode": industry.get(
                            "data", {}
                        ).get("industryCode"),
                        "businessDescription": industry.get(
                            "data", {}
                        ).get("industryDescription"),
                        "primaryActivities": industry.get(
                            "data", {}
                        ).get("relatedActivities"),
                        "businessTitle": industry.get(
                            "data", {}
                        ).get("industryName"),
                        "maximumDistanceTravelledFromBase": radius.get(
                            "key"
                        ),
                    },
                },
            ])

    # ------------------------------------------------------------------
    # Step 6: Kilometers
    # ------------------------------------------------------------------
    def kilometers(self):
        self._patch_data("vehicle-usages")
        self._metric("kilometers-per-year")

        km_code = self._resolve(
            "TRAVEL", self.input.get("PG_ANNUALKM")
        )
        self._persist("kilometers-per-year-pre-next-task", [
            {
                "op": "replace",
                "path": "/currentStepName",
                "value": "kilometers-per-year",
            },
            {
                "op": "add",
                "path": "/kilometersPerYear",
                "value": {
                    "selectedKilometersPerYear": km_code
                },
            },
        ])

    # ------------------------------------------------------------------
    # Step 7: Vehicle Parking / Address
    # ------------------------------------------------------------------
    def vehicle_parking(self):
        self._patch_data("kilometers-per-year")
        self._metric("vehicle-parking")

        self._persist("vehicle-parking-pre-task", [
            {
                "op": "replace",
                "path": "/currentStepName",
                "value": "vehicle-parking",
            }
        ])

        addr_str = (
            f"{self.input['ADDRESS']}, {self.input['SUBURB']} "
            f"{self.input['STATE']} {self.input['POSTCODE']}"
        )
        sub_str = (
            f"{self.input['SUBURB']} {self.input['POSTCODE']}"
        )

        addr_list = self._lookup(
            "address/one-line", params={"filter": addr_str}
        )
        addr = find_best_match(addr_str, addr_list, "value")
        if not addr:
            raise ValueError(f"Address not found: {addr_str}")

        store_list = self._lookup("common/stored-daytime")
        store = find_best_match(
            self.input.get("PG_GARAGE", ""), store_list, "value"
        )

        hyd_resp = self._persist(
            "hydrate-overnight-parking-address-details-task",
            [
                {
                    "op": "replace",
                    "path": (
                        "/vehicleParking/addressDetails"
                        "/anotherAddressDisplayText"
                    ),
                    "value": addr["value"],
                },
                {
                    "op": "replace",
                    "path": (
                        "/vehicleParking/addressDetails"
                        "/anotherAddressLookupKey"
                    ),
                    "value": addr["key"],
                },
            ],
        )

        # Extract address ID
        for op in hyd_resp.get("dataDeltaOperations", []):
            if op.get("path") == (
                "/vehicleParking/addressDetails"
                "/anotherAddressDetails"
            ):
                self.address_id = op.get("value", {}).get(
                    "addressId"
                )
        logger.info(f"🏠 Address ID: {self.address_id}")

        sub_list = self._lookup(
            "address/suburbsWithoutPoAddress",
            params={"filter": sub_str},
        )
        sub = find_best_match(sub_str, sub_list, "value")

        self._persist("vehicle-parking-pre-next-task-group", [
            {
                "op": "replace",
                "path": (
                    "/vehicleParking"
                    "/selectedDaytimeParkingLocationInput"
                ),
                "value": store["key"],
            },
            {
                "op": "replace",
                "path": (
                    "/vehicleParking"
                    "/selectedNightParkingLocation"
                ),
                "value": store["key"],
            },
            {
                "op": "add",
                "path": "/vehicleParking/daytimeParkingSuburb",
                "value": int(sub["key"]),
            },
            {
                "op": "add",
                "path": (
                    "/vehicleParking"
                    "/selectedDaytimeParkingLocation"
                ),
                "value": int(store["key"]),
            },
            {
                "op": "add",
                "path": (
                    "/vehicleParking"
                    "/daytimeParkingSuburbInput"
                ),
                "value": sub["key"],
            },
        ])

    # ------------------------------------------------------------------
    # Step 8: Policyholder
    # ------------------------------------------------------------------
    def policyholder(self):
        self._patch_data("vehicle-parking")
        self._metric("policyholder")

        dt = datetime(
            safe_int(self.input["DOB_DUMMY_YEAR"]),
            safe_int(self.input["DOB_DUMMY_MONTH"]),
            safe_int(self.input["DOB_DUMMY_DAY"]),
        )
        dob = dt.strftime("%Y-%m-%dT%H:%M:%S.000")
        gender = self._resolve(
            "GENDERS", self.input.get("PG_GENDER")
        )

        self._persist("policyholder-pre-next-task", [
            {
                "op": "add",
                "path": "/policyholder/firstName",
                "value": self.input["FIRST_NAME_DUMMY"],
            },
            {
                "op": "add",
                "path": "/policyholder/dateOfBirth",
                "value": dob,
            },
            {
                "op": "add",
                "path": "/policyholder/gender",
                "value": gender,
            },
            {
                "op": "replace",
                "path": "/currentStepName",
                "value": "policyholder",
            },
        ])

    # ------------------------------------------------------------------
    # Step 9: Driving Details
    # ------------------------------------------------------------------
    def driving_details(self):
        self._patch_data("policyholder")
        self._metric("driving-details")

        unsup_age = (
            safe_int(self.input["PC_AGE"])
            - safe_int(self.input["PC_YEARSHELDLICENSE"])
        )

        lic_list = self._lookup("common/licence-types")
        lic = find_best_match(
            self.input.get("LICENCE_TYPE", ""), lic_list, "value"
        )

        self._persist("driving-details-pre-next-task", [
            {
                "op": "add",
                "path": "/drivingDetails/licenceType",
                "value": lic["key"],
            },
            {
                "op": "add",
                "path": "/drivingDetails/ageStartDriving",
                "value": unsup_age,
            },
            {
                "op": "add",
                "path": "/drivingDetails/licenseSuspended",
                "value": self._resolve("YES_NO", "No"),
            },
            {
                "op": "add",
                "path": "/drivingDetails/demeritPointsInput",
                "value": self._resolve("DEMERITS", "0"),
            },
            {
                "op": "add",
                "path": "/drivingDetails/mobilePhoneUsage",
                "value": self._resolve("MOBILE_PHONE", "Never"),
            },
            {
                "op": "add",
                "path": "/drivingDetails/ownAnotherVehicle",
                "value": self._resolve("YES_NO", "No"),
            },
            {
                "op": "replace",
                "path": "/currentStepName",
                "value": "driving-details",
            },
        ])

    # ------------------------------------------------------------------
    # Step 10: Incidents
    # ------------------------------------------------------------------
    def incidents(self):
        self._patch_data("driving-details")
        self._metric("previous-incidents")

        cause = self.input.get("INCIDENT_CAUSE1")

        if cause != "No Claim":
            inc_type = self._resolve("INCIDENT_TYPES", cause)
            yr = safe_int(self.input["DATE_INCIDENT_YEAR"])
            mn = safe_int(self.input["DATE_INCIDENT_MONTH"])
            yes_val = self._resolve("YES_NO", "Yes")

            self._persist("incident-toggle-task", [
                {
                    "op": "replace",
                    "path": "/currentStepName",
                    "value": "previous-incidents",
                },
                {
                    "op": "add",
                    "path": "/incidents",
                    "value": {
                        "hasAny": yes_val,
                        "count": 0,
                        "historyItems": [],
                    },
                },
            ])
            self._persist("incident-populate-name-task", [
                {
                    "op": "add",
                    "path": (
                        "/incidents/historyItems/0/incidentType"
                    ),
                    "value": inc_type,
                }
            ])
            self._persist("incident-change-task", [
                {
                    "op": "replace",
                    "path": (
                        "/incidents/historyItems/0/onChange"
                    ),
                    "value": True,
                },
                {
                    "op": "add",
                    "path": (
                        "/incidents/historyItems/0"
                        "/incidentYearKey"
                    ),
                    "value": f"Y-{yr}",
                },
            ])
            self._persist("incident-change-task", [
                {
                    "op": "replace",
                    "path": (
                        "/incidents/historyItems/0/onChange"
                    ),
                    "value": True,
                },
                {
                    "op": "add",
                    "path": (
                        "/incidents/historyItems/0"
                        "/headerSeparator"
                    ),
                    "value": "/",
                },
                {
                    "op": "add",
                    "path": (
                        "/incidents/historyItems/0"
                        "/incidentMonthKey"
                    ),
                    "value": f"M-{str(mn).zfill(2)}",
                },
            ])
        else:
            no_val = self._resolve("YES_NO", "No")
            self._persist("incident-toggle-task", [
                {
                    "op": "replace",
                    "path": "/currentStepName",
                    "value": "previous-incidents",
                },
                {
                    "op": "add",
                    "path": "/incidents",
                    "value": {
                        "hasAny": no_val,
                        "count": 0,
                        "historyItems": [],
                    },
                },
            ])
            self._persist("incident-check-for-staged-task", [
                {
                    "op": "remove",
                    "path": "/incidents/historyItems",
                }
            ])

        self._persist("incident-history-pre-next-task", [])

    # ------------------------------------------------------------------
    # Step 11: Employment
    # ------------------------------------------------------------------
    def employment(self):
        self._patch_data("previous-incidents")
        self._metric("employment-details")

        emp = find_best_match(
            self.input.get("EMPLOYMENT", ""),
            self._lookup("common/employment-status"),
            "value",
        )

        if self.input.get("EMPLOYMENT", "") in (
            "Employed", "Self Employed"
        ):
            occ = find_best_match(
                self.input.get("OCCUPATION", ""),
                self._lookup("common/occupation-industry"),
                "value",
            )
            resp = self._persist(
                "employment-details-pre-next-task",
                [
                    {
                        "op": "replace",
                        "path": (
                            "/employmentDetails"
                            "/prepopulatedOccupationIndustry"
                        ),
                        "value": "",
                    },
                    {
                        "op": "replace",
                        "path": (
                            "/employmentDetails/employmentStatus"
                        ),
                        "value": emp["key"],
                    },
                    {
                        "op": "replace",
                        "path": (
                            "/employmentDetails"
                            "/needOccupationIndustry"
                        ),
                        "value": True,
                    },
                    {
                        "op": "add",
                        "path": (
                            "/employmentDetails"
                            "/occupationIndustry"
                        ),
                        "value": occ["key"],
                    },
                ],
            )
        else:
            self._persist("populate-occupation-code-task", [
                {
                    "op": "add",
                    "path": (
                        "/employmentDetails/employmentStatus"
                    ),
                    "value": emp["key"],
                },
                {
                    "op": "add",
                    "path": (
                        "/employmentDetails"
                        "/prepopulatedOccupationIndustry"
                    ),
                    "value": "",
                },
                {
                    "op": "replace",
                    "path": "/currentStepName",
                    "value": "employment-details",
                },
            ])
            resp = self._persist(
                "employment-details-pre-next-task", []
            )

        # Check switch flow from employment response
        self.state.update_from_response(resp)

    # ------------------------------------------------------------------
    # Step 11b: Switch Flow Decision (conditional)
    # ------------------------------------------------------------------
    def handle_switch_flow(self):
        decision = self.state.switch_flow_decision
        logger.info(
            f"🔀 Switch Flow Decision: {decision}"
        )

        if decision in ("None",):
            logger.info("✅ No switch flow needed")
            return

        if decision in ("ContinueOnline", "ContinueOnlineLead"):
            # Server already decided we can continue —
            # still need to confirm
            logger.info(
                f"✅ Server says '{decision}' — confirming"
            )
            self._persist("final-switch-flow-decision-task", [
                {
                    "op": "replace",
                    "path": "/leadGenerationStatus",
                    "value": "NotApplicable",
                },
                {
                    "op": "replace",
                    "path": "/hasClientMadeSwitchFlowDecision",
                    "value": True,
                },
                {
                    "op": "replace",
                    "path": "/switchFlowDecision",
                    "value": "ContinueOnline",
                },
            ])
            return

        if decision == "CustomerChoiceCallNowLater":
            logger.error(
                "❌ Server requires call centre routing — "
                "cannot continue online during fringe hours"
            )
            raise Exception(
                "Quote requires call centre (fringe hours). "
                "Run during AEST business hours."
            )

        # "CallCentre" here is routine, not a refusal: the server sets it at
        # employment-details-pre-next-task on effectively every risk, and the
        # journey continues by confirming ContinueOnline via
        # final-switch-flow-decision-task — which is what unlocks the next step
        # guard. Verified against trace captured_50742.2025.json (2026-07-09),
        # where this exact decision was followed by the confirm call and a real
        # $767.47 premium.
        #
        # An earlier revision raised here because forcing ContinueOnline was seen
        # to yield a fake $0 quote. That symptom is guarded directly now — a
        # non-positive premium raises in extract_premium — so this path no longer
        # has to be blocked wholesale, which was failing 100% of records.
        logger.info(
            f"🔀 Server routed to '{decision}' — confirming ContinueOnline "
            f"to unlock the next step (premium is validated later)"
        )
        self._persist("final-switch-flow-decision-task", [
            {
                "op": "replace",
                "path": "/leadGenerationStatus",
                "value": "NotApplicable",
            },
            {
                "op": "replace",
                "path": "/hasClientMadeSwitchFlowDecision",
                "value": True,
            },
            {
                "op": "replace",
                "path": "/switchFlowDecision",
                "value": "ContinueOnline",
            },
        ])

    # ------------------------------------------------------------------
    # Step 12: Insurance History
    # ------------------------------------------------------------------
    def insurance_history(self):
        self._patch_data("employment-details")
        self._metric("insurance-history")

        held = self.input.get("HELD_INS_2W", "").title()
        held_code = self._resolve("YES_NO", held)

        if held == "Yes":
            comp = self._resolve(
                "INSURERS",
                self.input.get("PG_PREVIOUSINSURER", ""),
            )
            dur = find_best_match(
                self.input.get("PC_YRSINSURED", ""),
                self._lookup("common/cover-duration"),
                "value",
            )
            val = {
                "isCurrentlyCovered": held_code,
                "whichInsurerPreviousToYoui": comp,
                "howLongwithPreviousInsurer": dur["key"],
                "previousInsuranceStatus": False,
            }
        else:
            no_cov = find_best_match(
                "Never Been Covered",
                self._lookup("common/no-cover-duration"),
                "value",
            )
            val = {
                "isCurrentlyCovered": held_code,
                "howLongWithoutInsurance": no_cov["key"],
            }

        self._persist("insurance-history-pre-next-task", [
            {
                "op": "add",
                "path": "/insuranceHistory",
                "value": val,
            }
        ])

    # ------------------------------------------------------------------
    # Step 13: Modifications (conditional)
    # ------------------------------------------------------------------
    def modifications(self):
        resp = self._patch_data("insurance-history")

        # Check if modifications step is available
        if not self.state.is_step_available(
            "modifications-and-accessories"
        ):
            logger.info(
                "⏭️ Modifications step not available — skipping"
            )
            return

        self._metric("modifications-and-accessories")
        no_val = self._resolve("YES_NO", "No")

        self._persist("modifications-pre-next-task", [
            {
                "op": "remove",
                "path": (
                    "/modifications"
                    "/factoryFittedOptionalAccessoryOptions"
                ),
            },
            {
                "op": "remove",
                "path": (
                    "/modifications"
                    "/factoryFittedListItemElements"
                ),
            },
            {
                "op": "remove",
                "path": "/modifications/modelSummary",
            },
            {
                "op": "remove",
                "path": (
                    "/modifications"
                    "/factoryOptionalAccessoriesAvailable"
                ),
            },
            {
                "op": "remove",
                "path": (
                    "/modifications"
                    "/factoryOptionalAccessoriesMoreThanTen"
                ),
            },
            {
                "op": "remove",
                "path": (
                    "/modifications"
                    "/hasFactoryFittedOptionalAccessories"
                    "AndAccessoriesListLessThanTen"
                ),
            },
            {
                "op": "remove",
                "path": (
                    "/modifications"
                    "/showOptionalFactoryFittedAccessoriesList"
                ),
            },
            {
                "op": "remove",
                "path": (
                    "/modifications"
                    "/hasOptionalFactoryFittedAccessoriesList"
                ),
            },
            {
                "op": "remove",
                "path": (
                    "/modifications"
                    "/showFactoryFittedAccessoriesList"
                ),
            },
            {
                "op": "remove",
                "path": (
                    "/modifications"
                    "/hasFactoryFittedAccessoriesList"
                ),
            },
            {
                "op": "remove",
                "path": (
                    "/modifications"
                    "/showFactoryFittedAccessoriesQuestion"
                ),
            },
            {
                "op": "add",
                "path": "/modifications/hasAnyModifications",
                "value": no_val,
            },
            {
                "op": "add",
                "path": (
                    "/modifications"
                    "/selectedModificationsDescriptionList"
                ),
                "value": [],
            },
            {
                "op": "add",
                "path": "/modifications/modificationElements",
                "value": [],
            },
            {
                "op": "add",
                "path": (
                    "/modifications"
                    "/hasAnyAftermarketAccessories"
                ),
                "value": no_val,
            },
            {
                "op": "replace",
                "path": "/currentStepName",
                "value": "modifications-and-accessories",
            },
        ])

    # ------------------------------------------------------------------
    # Step 14: Additional Policyholder Details
    # ------------------------------------------------------------------
    def additional_policyholder(self):
        # Check which step to patch based on what's available
        if self.state.is_step_available(
            "modifications-and-accessories"
        ):
            self._patch_data("modifications-and-accessories")
        else:
            # Already patched from insurance-history
            pass

        if not self.state.is_step_available(
            "additional-policyholder-details"
        ):
            logger.info(
                "⏭️ Additional policyholder not available"
            )
            return

        self._metric("additional-policyholder-details")
        yes_val = self._resolve("YES_NO", "Yes")

        self._persist(
            "additional-policyholder-details"
            "-create-policyholder-task",
            [
                {
                    "op": "add",
                    "path": (
                        "/additionalPolicyholderDetails"
                        "/addressDetails/useExistingAddress"
                    ),
                    "value": self.address_id,
                },
                {
                    "op": "add",
                    "path": (
                        "/additionalPolicyholderDetails"
                        "/willDriveTheCar"
                    ),
                    "value": yes_val,
                },
                {
                    "op": "add",
                    "path": (
                        "/additionalPolicyholderDetails"
                        "/firstName"
                    ),
                    "value": self.input.get("FIRST_NAME_DUMMY"),
                },
                {
                    "op": "add",
                    "path": (
                        "/additionalPolicyholderDetails"
                        "/lastName"
                    ),
                    "value": self.input.get("LAST_NAME_DUMMY"),
                },
                {
                    "op": "add",
                    "path": "/drivingDetails/driverType",
                    "value": "ListedDriver",
                },
                {
                    "op": "replace",
                    "path": "/currentStepName",
                    "value": "additional-policyholder-details",
                },
            ],
        )

        self._persist(
            "additional-policyholder-details-pre-next-task", []
        )

        # ------------------------------------------------------------------
    # Step 15: Drivers List (continued)
    # ------------------------------------------------------------------
    def drivers_list(self):
        self._patch_data("additional-policyholder-details")
        self._metric("drivers-list")

        resp = self._persist("build-listed-drivers-task", [
            {
                "op": "replace",
                "path": "/currentStepName",
                "value": "drivers-list",
            },
            {
                "op": "add",
                "path": "/driverSummary",
                "value": {
                    "hasRegularDriver": False,
                    "numberOfRegularDriversToNominate": 0,
                    "policyholderDisplayList": [],
                    "regularDriverOptions": [],
                    "isRegularDriverMustBeExplicitlyNominated": False,
                    "showListedAnotherDriver": False,
                    "hasAvailableAnotherDrivers": False,
                    "listAnotherDriverOptions": [],
                },
            },
        ])

        driver_flag = self.input.get(
            "SECOND_DRIVER_FLAG", ""
        ).title()
        no_val = self._resolve("YES_NO", "No")

        if driver_flag == "Yes":
            needed_val = self._resolve("YES_NO", "Yes")

            self._persist("set-edit-listed-driver-task", [
                {
                    "op": "remove",
                    "path": "/driverSummary/selectedAnotherDriver",
                },
                {
                    "op": "remove",
                    "path": (
                        "/driverSummary"
                        "/listedDriversDisplayList/0/dateOfBirth"
                    ),
                },
                {
                    "op": "remove",
                    "path": (
                        "/driverSummary"
                        "/policyholderDisplayList/0"
                        "/driverNameSuffix"
                    ),
                },
                {
                    "op": "remove",
                    "path": (
                        "/driverSummary"
                        "/policyholderDisplayList/0"
                        "/hasRegularDriverWhenThisOneRemoved"
                    ),
                },
                {
                    "op": "remove",
                    "path": (
                        "/driverSummary"
                        "/policyholderDisplayList/0"
                        "/hasOtherDriversWhenThisOneRemoved"
                    ),
                },
                {
                    "op": "remove",
                    "path": (
                        "/driverSummary"
                        "/policyholderDisplayList/0/dateOfBirth"
                    ),
                },
                {
                    "op": "add",
                    "path": (
                        "/driverSummary"
                        "/addAnotherDriverQuestion"
                    ),
                    "value": needed_val,
                },
            ])

            self._persist("driver-details-pre-task", [
                {
                    "op": "remove",
                    "path": "/driverSummary/selectedAnotherDriver",
                }
            ])
            self._metric("driver-details")
            self._add_second_driver(needed_val, resp)
        else:
            # No second driver — set regular driver directly
            self._persist("set-regular-driver-task", [
                {
                    "op": "remove",
                    "path": "/driverSummary/selectedAnotherDriver",
                },
                {
                    "op": "remove",
                    "path": (
                        "/driverSummary"
                        "/listedDriversDisplayList/0/dateOfBirth"
                    ),
                },
                {
                    "op": "remove",
                    "path": (
                        "/driverSummary"
                        "/policyholderDisplayList/0"
                        "/driverNameSuffix"
                    ),
                },
                {
                    "op": "remove",
                    "path": (
                        "/driverSummary"
                        "/policyholderDisplayList/0"
                        "/hasRegularDriverWhenThisOneRemoved"
                    ),
                },
                {
                    "op": "remove",
                    "path": (
                        "/driverSummary"
                        "/policyholderDisplayList/0"
                        "/hasOtherDriversWhenThisOneRemoved"
                    ),
                },
                {
                    "op": "remove",
                    "path": (
                        "/driverSummary"
                        "/policyholderDisplayList/0/dateOfBirth"
                    ),
                },
                {
                    "op": "add",
                    "path": (
                        "/driverSummary"
                        "/addAnotherDriverQuestion"
                    ),
                    "value": no_val,
                },
            ])

    # ------------------------------------------------------------------
    # Step 15b: Add Second Driver
    # ------------------------------------------------------------------
    def _add_second_driver(self, needed_val: str, build_resp: dict):
        logger.info("➕ Adding Second Driver...")
        i = self.input

        dob = datetime(
            safe_int(i["DOB_DUMMY_YEAR2"]),
            safe_int(i["DOB_DUMMY_MONTH2"]),
            safe_int(i["DOB_DUMMY_DAY2"]),
        ).strftime("%Y-%m-%dT%H:%M:%S.000")

        unsup = (
            safe_int(i["PC_AGE2"])
            - safe_int(i["PC_YEARSHELDLICENSE2"])
        )

        lic = find_best_match(
            i["LICENCE_TYPE2"],
            self._lookup("common/licence-types"),
            "value",
        )
        emp = find_best_match(
            i["EMPLOYMENT2"],
            self._lookup("common/employment-status"),
            "value",
        )
        occ = find_best_match(
            i["OCCUPATION2"],
            self._lookup("common/occupation-industry"),
            "value",
        )

        no_val = self._resolve("YES_NO", "No")
        demerit_val = self._resolve("DEMERITS", "0")
        mobile_val = self._resolve("MOBILE_PHONE", "Never")
        gender_val = self._resolve("GENDERS", i["PG_GENDER2"])

        # Calculate driver age
        self._persist("calculate-driver-age-task", [
            {
                "op": "add",
                "path": (
                    "/editListedDriver/employmentDetails"
                    "/employmentStatus"
                ),
                "value": None,
            },
            {
                "op": "add",
                "path": (
                    "/editListedDriver/drivingDetails/driverType"
                ),
                "value": "ListedDriver",
            },
            {
                "op": "add",
                "path": (
                    "/editListedDriver/addressDetails"
                    "/useExistingAddress"
                ),
                "value": self.address_id,
            },
            {
                "op": "add",
                "path": "/editListedDriver/dateOfBirth",
                "value": dob,
            },
            {
                "op": "add",
                "path": "/editListedDriver/gender",
                "value": gender_val,
            },
            {
                "op": "add",
                "path": "/driverSummary/selectedAnotherDriver",
                "value": needed_val,
            },
            {
                "op": "add",
                "path": "/underwriting/knockoutsFound",
                "value": False,
            },
            {
                "op": "replace",
                "path": "/currentStepName",
                "value": "driver-details",
            },
        ])

        # Set name and incidents
        self._persist("incident-toggle-task", [
            {
                "op": "add",
                "path": (
                    "/editListedDriver/incidentHistory/hasAny"
                ),
                "value": no_val,
            },
            {
                "op": "add",
                "path": "/editListedDriver/firstName",
                "value": i["FIRST_NAME_DUMMY2"],
            },
            {
                "op": "add",
                "path": "/editListedDriver/lastName",
                "value": i["LAST_NAME_DUMMY2"],
            },
        ])

        # Set driving/employment details and check for staged
        self._persist("incident-check-for-staged-task", [
            {
                "op": "replace",
                "path": (
                    "/editListedDriver/employmentDetails"
                    "/needOccupationIndustry"
                ),
                "value": True,
            },
            {
                "op": "add",
                "path": (
                    "/editListedDriver/employmentDetails"
                    "/employmentStatus"
                ),
                "value": emp["key"],
            },
            {
                "op": "add",
                "path": (
                    "/editListedDriver/employmentDetails"
                    "/occupationIndustry"
                ),
                "value": occ["key"],
            },
            {
                "op": "add",
                "path": (
                    "/editListedDriver/employmentDetails"
                    "/prepopulatedOccupationIndustry"
                ),
                "value": "",
            },
            {
                "op": "add",
                "path": (
                    "/editListedDriver/drivingDetails"
                    "/licenceType"
                ),
                "value": lic["key"],
            },
            {
                "op": "add",
                "path": (
                    "/editListedDriver/drivingDetails"
                    "/demeritPointsInput"
                ),
                "value": demerit_val,
            },
            {
                "op": "add",
                "path": (
                    "/editListedDriver/drivingDetails"
                    "/mobilePhoneUsage"
                ),
                "value": mobile_val,
            },
            {
                "op": "add",
                "path": "/editListedDriver/ageStartDriving",
                "value": unsup,
            },
            {
                "op": "add",
                "path": "/editListedDriver/licenseSuspended",
                "value": no_val,
            },
            {
                "op": "add",
                "path": "/editListedDriver/ownAnotherVehicle",
                "value": no_val,
            },
        ])

        # Duplicate check and save
        self._persist("check-duplicate-person-task", [])
        self._persist("save-listed-driver-task", [])

        # Rebuild drivers list and find regular driver ID
        build_resp = self._persist("build-listed-drivers-task", [
            {
                "op": "replace",
                "path": "/currentStepName",
                "value": "drivers-list",
            }
        ])

        # Extract policyholder's driver ID for regular driver
        target = self.input["FULL_NAME_DUMMY"].lower()
        for d in build_resp.get("dataDeltaOperations", []):
            if d.get("path") == (
                "/driverSummary/regularDriverOptions"
            ):
                for v in d.get("value", []):
                    if v.get("value", "").lower() == target:
                        self.driver_id = v.get("key")
                        logger.info(
                            f"👤 Regular Driver ID: "
                            f"{self.driver_id}"
                        )
                        break

        # Set regular driver
        self._persist("set-regular-driver-task", [
            {
                "op": "remove",
                "path": "/driverSummary/selectedAnotherDriver",
            },
            {
                "op": "replace",
                "path": (
                    "/driverSummary"
                    "/addAnotherDriverQuestion"
                ),
                "value": no_val,
            },
            {
                "op": "replace",
                "path": (
                    "/driverSummary"
                    "/isRegularDriverMustBeExplicitlyNominated"
                ),
                "value": True,
            },
            {
                "op": "remove",
                "path": (
                    "/driverSummary"
                    "/listedDriversDisplayList/1/dateOfBirth"
                ),
            },
            {
                "op": "remove",
                "path": (
                    "/driverSummary"
                    "/listedDriversDisplayList/0/dateOfBirth"
                ),
            },
            {
                "op": "remove",
                "path": (
                    "/driverSummary"
                    "/policyholderDisplayList/0"
                    "/hasOtherDriversWhenThisOneRemoved"
                ),
            },
            {
                "op": "add",
                "path": (
                    "/driverSummary"
                    "/nominatedRegularDriverId"
                ),
                "value": self.driver_id,
            },
        ])

    # ------------------------------------------------------------------
    # Step 16: Duty of Disclosure
    # ------------------------------------------------------------------
    def duty_of_disclosure(self):
        self._persist("save-regular-driver-task", [])
        self._metric("duty-of-disclosure")

        no_val = self._resolve("YES_NO", "No")

        self._persist("cancellation-add-task", [
            {
                "op": "replace",
                "path": "/currentStepName",
                "value": "duty-of-disclosure",
            },
            {
                "op": "add",
                "path": "/dutyOfDisclosure",
                "value": {"hasAnyPreviousCancellations": no_val},
            },
        ])

        self._persist("duty-of-disclosure-pre-next-task", [
            {
                "op": "add",
                "path": (
                    "/dutyOfDisclosure"
                    "/hasAnyCriminalConvictions"
                ),
                "value": no_val,
            }
        ])

    # ------------------------------------------------------------------
    # Step 17: Premium Extraction (Dynamic)
    # ------------------------------------------------------------------
    def extract_premiums(self):
        logger.info("💰 Extracting Premiums...")
        annual_resp = self._patch_data("duty-of-disclosure")
        self._metric("vehicle-premium")

        def _get_val(resp, path):
            if not isinstance(resp, dict):
                return None
            for op in resp.get("dataDeltaOperations", []):
                if op.get("path") == path:
                    return op.get("value")
            return None

        def _to_float(d, c):
            raw_d = str(d or "0").replace(",", "")
            raw_c = str(c or "0")
            try:
                return float(f"{raw_d}.{raw_c}")
            except ValueError:
                return 0.0

        # --- Parse initial premium data ---
        a_dollars = _get_val(
            annual_resp, "/vehiclePremium/premiumDollars"
        )
        a_cents = _get_val(
            annual_resp, "/vehiclePremium/premiumCents"
        )
        quote_num = (
            _get_val(
                annual_resp,
                "/vehiclePremium/displayedQuoteNumber",
            ) or ""
        ).split(" ")[-1].lstrip("#").strip() or self.state.quote_number

        def_excess = _get_val(
            annual_resp, "/vehiclePremium/defaultExcess"
        )
        excess_list = (
            _get_val(annual_resp, "/vehiclePremium/excessList")
            or []
        )

        cover_map = self.state.cover_index_map
        annual_pay = _to_float(a_dollars, a_cents)

        # Youi's API is diff-based: if a recalculated premium comes
        # back identical to the previous one, the response omits
        # premiumDollars/premiumCents entirely (nothing changed, so
        # nothing to diff). That must resolve to "unchanged", not "$0".
        last_known_premium = annual_pay

        def _resolve_premium(resp):
            nonlocal last_known_premium
            d = _get_val(resp, "/vehiclePremium/premiumDollars")
            c = _get_val(resp, "/vehiclePremium/premiumCents")
            if d is None and c is None:
                return last_known_premium
            last_known_premium = _to_float(d, c)
            return last_known_premium

        logger.info(
            f"💵 Annual: {annual_pay} | Quote: {quote_num} | "
            f"Default Excess: {def_excess}"
        )

        # A risk the server really did route away from online pricing comes back
        # here with no premium at all. Writing that as $0 would look like a
        # genuine quote, so fail the record instead and let the reason column say
        # so. This is the guard that makes confirming ContinueOnline above safe.
        #
        # Deliberately NOT raising on annual_pay == 0 here: at this point only the
        # duty-of-disclosure patch has been read, and the premium legitimately
        # arrives later from the set-payment-frequency-and-recalc-premium
        # sequence. The row is validated in the worker before it is written.
        logger.info(f"💵 Dynamic Excess List: {excess_list}")
        logger.info(f"💵 Available Covers: {cover_map}")

        self.output.update({
            "QuoteNumber": quote_num,
            "QuoteLink": (
                f"https://quotes.youi.com.au/dcq/vehicle/v2/"
                f"{self.session_id}/premium"
            ),
            "DefaultExcess": def_excess,
            "YearlyPayment": annual_pay,
            "MiddleAgreedValueSI": self.mid_agreed_value,
        })

        # =============================================
        # STEP 1: AGREED VALUE — FIRST (clean snapshot)
        # No covers have been toggled yet so the server
        # snapshot is guaranteed uncontaminated [1]
        # =============================================
        if self.mid_agreed_value > 0:
            logger.info(
                f"💵 Agreed Value ({self.mid_agreed_value}) "
                f"— calculating FIRST while snapshot is clean..."
            )
            try:
                agreed_ops = [
                    {
                        "op": "remove",
                        "path": (
                            "/modifications"
                            "/aftermarketAccessories"
                            "/sumInsuredAmount"
                        ),
                    },
                    {
                        "op": "remove",
                        "path": (
                            "/vehiclePremium"
                            "/checkedSpecialConditions"
                        ),
                    },
                    {
                        "op": "remove",
                        "path": (
                            "/vehiclePremium/annualGrossPremium"
                        ),
                    },
                    {
                        "op": "replace",
                        "path": (
                            "/vehiclePremium/insuredValueInput"
                        ),
                        "value": "6343",
                    },
                    {
                        "op": "replace",
                        "path": "/vehiclePremium/premiumCents",
                        "value": "--",
                    },
                    {
                        "op": "replace",
                        "path": "/vehiclePremium/premiumDollars",
                        "value": "___",
                    },
                    {
                        "op": "replace",
                        "path": (
                            "/vehiclePremium"
                            "/paymentFrequencyAbbreviated"
                        ),
                        "value": "P/-",
                    },
                    {
                        "op": "replace",
                        "path": (
                            "/vehiclePremium/paymentFrequency"
                        ),
                        "value": "Annual",
                    },
                    {
                        "op": "replace",
                        "path": (
                            "/vehiclePremium"
                            "/coverOptionsRecalcRequired"
                        ),
                        "value": True,
                    },
                    {
                        "op": "replace",
                        "path": (
                            "/vehiclePremium/isInReCalcMode"
                        ),
                        "value": True,
                    },
                    {
                        "op": "replace",
                        "path": "/vehiclePremium/insuredValue",
                        "value": 6343,
                    },
                    {
                        "op": "add",
                        "path": "/vehiclePremium/agreedValue",
                        "value": self.mid_agreed_value,
                    },
                ]

                agreed_resp = self._persist(
                    "set-payment-frequency-and-recalc-premium",
                    agreed_ops,
                )

                # Safety net: detect if server re-enabled covers
                contaminated = False
                for op_item in agreed_resp.get(
                    "dataDeltaOperations", []
                ):
                    path = op_item.get("path", "")
                    value = op_item.get("value")
                    if (
                        "optionalCoverOptions" in path
                        and "isSelected" in path
                        and value is True
                    ):
                        logger.warning(
                            f"⚠️ Server re-enabled cover: {path}"
                        )
                        contaminated = True

                if contaminated:
                    logger.info(
                        "🔄 Contamination detected — turning off "
                        "all covers and recalculating..."
                    )
                    fix_ops = []
                    for code, idx in cover_map.items():
                        fix_ops.append({
                            "op": "replace",
                            "path": (
                                f"/vehiclePremium"
                                f"/optionalCoverOptions"
                                f"/{idx}/isSelected"
                            ),
                            "value": False,
                        })
                    fix_ops.extend([
                        {
                            "op": "remove",
                            "path": (
                                "/vehiclePremium"
                                "/annualGrossPremium"
                            ),
                        },
                        {
                            "op": "replace",
                            "path": "/vehiclePremium/premiumCents",
                            "value": "--",
                        },
                        {
                            "op": "replace",
                            "path": (
                                "/vehiclePremium/premiumDollars"
                            ),
                            "value": "___",
                        },
                        {
                            "op": "replace",
                            "path": (
                                "/vehiclePremium/isInReCalcMode"
                            ),
                            "value": True,
                        },
                    ])

                    self._persist(
                        "update-premium-details", fix_ops
                    )
                    clean_resp = self._persist(
                        "recalc-premium-task", []
                    )

                    self.output["AgreedValue"] = _resolve_premium(
                        clean_resp
                    )
                    logger.info(
                        f"💵 Agreed Value (cleaned): "
                        f"{self.output['AgreedValue']}"
                    )
                else:
                    self.output["AgreedValue"] = _resolve_premium(
                        agreed_resp
                    )
                    logger.info(
                        f"💵 Agreed Value: "
                        f"{self.output['AgreedValue']}"
                    )

                # Revert back to market value for remaining calcs
                logger.info(
                    "🔄 Reverting to market value for "
                    "remaining calculations..."
                )

                revert_ops = [
                    {"op":"add","path":"/modifications/aftermarketAccessories/sumInsuredAmount","value":0},
                    {"op":"remove","path":"/vehiclePremium/checkedSpecialConditions"},
                    {"op":"remove","path":"/vehiclePremium/agreedValue"},
                    {"op":"replace","path":"/vehiclePremium/insuredValueInput","value":"6340"},
                    {"op":"replace","path":"/vehiclePremium/coverOptionsRecalcRequired","value":True},
                    {"op":"replace","path":"/vehiclePremium/isInReCalcMode","value":True},
                    {"op":"replace","path":"/vehiclePremium/insuredValue","value":6340}]

                self._persist(
                    "set-premium-state-stale-seed-patch-task",
                    revert_ops,
                )
                self._premium_update()
                self._persist("recalc-premium-task", [])
                logger.info("✅ Reverted to market value")

            except KnockoutException:
                raise
            except Exception as e:
                logger.error(f"❌ Agreed Value failed: {e}")
                self.output["AgreedValue"] = ""
                self.output["MiddleAgreedValueSI"] = ""

        # =============================================
        # STEP 2: EXCESS RECALCULATIONS (dynamic list)
        # Server is back on market value, clean state
        # =============================================
        logger.info(
            f"💵 Calculating {len(excess_list)} excess values..."
        )
        for exc in excess_list:
            try:
                if def_excess is not None and int(exc) == int(def_excess):
                    # This is the currently active excess — its
                    # premium is the annual premium already fetched.
                    # Assign directly instead of a network round trip
                    # that a transient blip could needlessly fail.
                    self.output[f"excess{int(exc)}"] = annual_pay
                    logger.info(
                        f"💵 Excess {int(exc)} (default): {annual_pay}"
                    )
                    continue

                self._premium_update(excess=exc)
                exc_resp = self._persist(
                    "recalc-premium-task", []
                )
                self.output[f"excess{int(exc)}"] = _resolve_premium(
                    exc_resp
                )
                logger.info(
                    f"💵 Excess {int(exc)}: "
                    f"{self.output[f'excess{int(exc)}']}"
                )
            except KnockoutException:
                raise
            except Exception as e:
                logger.error(f"❌ Excess {exc} failed: {e}")
                self.output[f"excess{int(exc)}"] = ""

        # =============================================
        # STEP 3: WINDOW/GLASS (WSD) — dynamic index
        # Only if WSD cover exists for this vehicle [1]
        # =============================================
        wsd_idx = cover_map.get("WSD")
        if wsd_idx is not None:
            logger.info(
                f"💵 Window Glass (WSD at index {wsd_idx})..."
            )
            try:
                ops = [
                    {
                        "op": "remove",
                        "path": (
                            "/vehiclePremium"
                            "/checkedSpecialConditions"
                        ),
                    },
                    {
                        "op": "replace",
                        "path": (
                            f"/vehiclePremium"
                            f"/optionalCoverOptions"
                            f"/{wsd_idx}/isSelected"
                        ),
                        "value": True,
                    },
                    {
                        "op": "add",
                        "path": (
                            "/underwriting"
                            "/hasReferableKnockouts"
                        ),
                        "value": False,
                    },
                    {
                        "op": "add",
                        "path": "/vehicleLookup/foundVehicle",
                        "value": {"yearOfManufacture": 0},
                    },
                    {
                        "op": "replace",
                        "path": "/currentStepName",
                        "value": "vehicle-premium",
                    },
                ]
                self._premium_update(
                    excess=def_excess, extra_ops=ops
                )
                win_resp = self._persist(
                    "recalc-premium-task", []
                )
                self.output["WindowGlassExcess"] = _resolve_premium(
                    win_resp
                )
                logger.info(
                    f"💵 Window Glass: "
                    f"{self.output['WindowGlassExcess']}"
                )
            except KnockoutException:
                raise
            except Exception as e:
                logger.error(f"❌ Window Glass failed: {e}")
                self.output["WindowGlassExcess"] = ""
        else:
            logger.info("⏭️ WSD cover not available — skipping")
            self.output["WindowGlassExcess"] = ""

        # =============================================
        # STEP 4: HIRE CAR (CHO) — dynamic index
        # Only if CHO cover exists for this vehicle [1]
        # =============================================
        cho_idx = cover_map.get("CHO")
        if cho_idx is not None:
            logger.info(
                f"💵 Hire Car (CHO at index {cho_idx})..."
            )
            try:
                ops_hc = [
                    {
                        "op": "replace",
                        "path": (
                            f"/vehiclePremium"
                            f"/optionalCoverOptions"
                            f"/{cho_idx}/isSelected"
                        ),
                        "value": True,
                    },
                ]
                # Turn off windscreen if it was on
                if wsd_idx is not None:
                    ops_hc.append({
                        "op": "replace",
                        "path": (
                            f"/vehiclePremium"
                            f"/optionalCoverOptions"
                            f"/{wsd_idx}/isSelected"
                        ),
                        "value": False,
                    })
                self._premium_update(extra_ops=ops_hc)
                hc_resp = self._persist(
                    "recalc-premium-task", []
                )
                self.output["HireCar"] = _resolve_premium(hc_resp)
                logger.info(
                    f"💵 Hire Car: {self.output['HireCar']}"
                )
            except KnockoutException:
                raise
            except Exception as e:
                logger.error(f"❌ Hire Car failed: {e}")
                self.output["HireCar"] = ""
        else:
            logger.info("⏭️ CHO cover not available — skipping")
            self.output["HireCar"] = ""

        # =============================================
        # STEP 5: BUSINESS ITEMS (TOT) — dynamic index
        # Only if TOT cover exists for this vehicle [1]
        # =============================================
        tot_idx = cover_map.get("TOT")
        if tot_idx is not None:
            logger.info(
                f"💵 Business Items (TOT at index {tot_idx})..."
            )
            try:
                ops_bi = [
                    {
                        "op": "replace",
                        "path": (
                            f"/vehiclePremium"
                            f"/optionalCoverOptions"
                            f"/{tot_idx}/isSelected"
                        ),
                        "value": True,
                    },
                ]
                # Turn off previous cover
                if cho_idx is not None:
                    ops_bi.append({
                        "op": "replace",
                        "path": (
                            f"/vehiclePremium"
                            f"/optionalCoverOptions"
                            f"/{cho_idx}/isSelected"
                        ),
                        "value": False,
                    })
                elif wsd_idx is not None:
                    ops_bi.append({
                        "op": "replace",
                        "path": (
                            f"/vehiclePremium"
                            f"/optionalCoverOptions"
                            f"/{wsd_idx}/isSelected"
                        ),
                        "value": False,
                    })
                self._premium_update(extra_ops=ops_bi)
                bi_resp = self._persist(
                    "recalc-premium-task", []
                )
                self.output["BusinessItemsCover"] = _resolve_premium(
                    bi_resp
                )
                logger.info(
                    f"💵 Business Items: "
                    f"{self.output['BusinessItemsCover']}"
                )
            except KnockoutException:
                raise
            except Exception as e:
                logger.error(f"❌ Business Items failed: {e}")
                self.output["BusinessItemsCover"] = ""
        else:
            logger.info("⏭️ TOT cover not available — skipping")
            self.output["BusinessItemsCover"] = ""

        # =============================================
        # STEP 6: MONTHLY PAYMENT
        # Turn OFF all covers dynamically [1]
        # =============================================
        logger.info("💵 Calculating Monthly...")
        try:
            ops_mo = [
                {
                    "op": "replace",
                    "path": (
                        "/vehiclePremium"
                        "/paymentFrequencyAbbreviated"
                    ),
                    "value": "P/-",
                },
                {
                    "op": "replace",
                    "path": "/vehiclePremium/paymentFrequency",
                    "value": "Monthly",
                },
            ]
            # Dynamically turn off ALL covers regardless
            # of which was last toggled
            for code, idx in cover_map.items():
                ops_mo.append({
                    "op": "replace",
                    "path": (
                        f"/vehiclePremium"
                        f"/optionalCoverOptions"
                        f"/{idx}/isSelected"
                    ),
                    "value": False,
                })

            mo_resp = self._premium_update(
                extra_ops=ops_mo,
                endpoint=(
                    "set-payment-frequency-and-recalc-premium"
                ),
            )

            md = _get_val(
                mo_resp, "/vehiclePremium/premiumDollars"
            )
            mc = _get_val(
                mo_resp, "/vehiclePremium/premiumCents"
            )

            # Fallback: if set-payment didn't return values, try an
            # explicit recalc. `is None` (not a falsy check) — "0" is
            # a valid dollar-string, not a missing value.
            if md is None and mc is None:
                mo_resp = self._persist(
                    "recalc-premium-task", []
                )

            self.output["MonthlyPayment"] = _resolve_premium(mo_resp)
            logger.info(
                f"💵 Monthly: {self.output['MonthlyPayment']}"
            )
        except KnockoutException:
            raise
        except Exception as e:
            logger.error(f"❌ Monthly failed: {e}")
            self.output["MonthlyPayment"] = ""

        logger.info("✅ Premium extraction complete")
        logger.info(
            f"📊 Summary: Annual={self.output['YearlyPayment']}, "
            f"Monthly={self.output['MonthlyPayment']}, "
            f"Agreed={self.output['AgreedValue']}, "
            f"Window={self.output['WindowGlassExcess']}, "
            f"HireCar={self.output['HireCar']}, "
            f"Business={self.output['BusinessItemsCover']}"
        )
    # ------------------------------------------------------------------
    # Premium update helper
    # ------------------------------------------------------------------
    def _premium_update(
        self,
        excess=None,
        extra_ops=None,
        endpoint="update-premium-details",
    ):
        """Build and send premium update operations."""
        ops = [
            {
                "op": "remove",
                "path": "/vehiclePremium/annualGrossPremium",
            },
            {
                "op": "replace",
                "path": "/vehiclePremium/premiumCents",
                "value": "--",
            },
            {
                "op": "replace",
                "path": "/vehiclePremium/premiumDollars",
                "value": "___",
            },
            {
                "op": "replace",
                "path": "/vehiclePremium/isInReCalcMode",
                "value": True,
            },
        ]

        if excess is not None:
            ops.extend([
                {
                    "op": "replace",
                    "path": (
                        "/vehiclePremium/selectedExcessInput"
                    ),
                    "value": excess,
                },
                {
                    "op": "replace",
                    "path": "/vehiclePremium/selectedExcess",
                    "value": excess,
                },
            ])

        if extra_ops:
            ops.extend(extra_ops)

        return self._persist(endpoint, ops)

    # ------------------------------------------------------------------
    # MAIN FLOW ORCHESTRATOR
    # ------------------------------------------------------------------
    def run_flow(self) -> Dict[str, Any]:
        try:
            # Phase 1: Session
            self.start_quote()

            # Phase 2: Vehicle
            self.vehicle_lookup()
            self.vehicle_condition()
            self.cover_information()
            self.vehicle_usage()
            self.kilometers()
            self.vehicle_parking()

            # Phase 3: Driver
            self.policyholder()
            self.driving_details()
            self.incidents()

            # Phase 4: Employment + Switch Flow Gate
            self.employment()
            self.handle_switch_flow()

            # Phase 5: History & Mods (conditional)
            self.insurance_history()
            self.modifications()

            # Phase 6: Additional Details & Drivers
            self.additional_policyholder()
            self.drivers_list()

            # Phase 7: Disclosure & Premium
            self.duty_of_disclosure()
            self.extract_premiums()

            logger.info(
                f"✅ Quote {self.output['QuoteNumber']} "
                f"completed successfully"
            )

        except KnockoutException as e:
            logger.error(
                f"❌ Flow failed: {e}", exc_info=True
            )
            # A knockout declines the whole risk — clear any premium
            # fields set before it hit rather than leave a quote that
            # looks partially priced.
            for key in (
                "DefaultExcess", "MonthlyPayment", "YearlyPayment",
                "WindowGlassExcess", "BusinessItemsCover",
                "AgreedValue", "MiddleAgreedValueSI", "HireCar",
            ):
                self.output[key] = ""
            for key in list(self.output):
                if key.startswith("excess"):
                    self.output[key] = ""
            self.output["Reason_for_quote_failure"] = str(e)

        except Exception as e:
            logger.error(
                f"❌ Flow failed: {e}", exc_info=True
            )
            self.output["Reason_for_quote_failure"] = str(e)

        return self.output


# ==============================================================================
# OUTPUT HELPERS
# ==============================================================================
def sort_columns_with_excess(columns: list) -> list:
    prefix = [
        "SAMPLE_NUMBER", "Postcode", "DOB_day", "DOB_month",
        "DOB_year", "QuoteDate", "Timestamp AEST (in hh:mm)",
    ]
    quote_data = [
        "DeducedVehicleSelection", "QuoteLink", "QuoteNumber",
        "DefaultExcess", "MonthlyPayment", "YearlyPayment",
        "RoadsideAssist", "HireCar", "WindowGlassExcess",
        "BusinessItemsCover", "AgreedValue", "MiddleAgreedValueSI",
    ]
    suffix = ["Reason_for_quote_failure", "Remark (if any)"]

    excess_pattern = re.compile(r"^excess(\d+)$", re.IGNORECASE)
    excess_cols = []
    other_cols = []

    for col in columns:
        match = excess_pattern.match(col)
        if match:
            excess_cols.append((int(match.group(1)), col))
        elif col not in prefix + quote_data + suffix:
            other_cols.append(col)

    excess_cols.sort(key=lambda x: x[0])

    final = []
    for col in prefix:
        if col in columns:
            final.append(col)
    for col in quote_data:
        if col in columns:
            final.append(col)
    final.extend([col for _, col in excess_cols])
    for col in other_cols:
        if col not in final:
            final.append(col)
    for col in suffix:
        if col in columns:
            final.append(col)

    return final


def save_record(record: dict, output_csv_path: str):
    if not record:
        return

    new_df = pd.DataFrame([record])

    if (
        os.path.exists(output_csv_path)
        and os.path.getsize(output_csv_path) > 0
    ):
        existing_df = pd.read_csv(output_csv_path)
        combined = pd.concat(
            [existing_df, new_df], ignore_index=True
        )
        sorted_cols = sort_columns_with_excess(
            list(combined.columns)
        )
        combined[sorted_cols].to_csv(
            output_csv_path, index=False
        )
    else:
        sorted_cols = sort_columns_with_excess(
            list(new_df.columns)
        )
        new_df[sorted_cols].to_csv(
            output_csv_path, index=False
        )


# ==============================================================================
# ENTRY POINT
# ==============================================================================
if __name__ == "__main__":
    OUTPUT_PATH = (
        "/home/wmc/WMC/DataManipulation/Suncorp/Youi/outputs/202608/Motor_YOUI_quarterly_202608.csv"
    )
    INPUT_PATH = (
        "/home/wmc/WMC/DataManipulation/Suncorp/Youi/inputs/202608/data/Motor_YOUI_quarterly_202608.csv"
    )

    try:
        data_list = pd.read_csv(INPUT_PATH).to_dict(
            orient="records"
        )
        for data in data_list:
            if data.get("SAMPLE_NUMBER") not in {21820.2025}:
                continue
            input_data = format_values(data)
            flow = YouiMotorsNewFlow(input_data)
            result = flow.run_flow()
            print(json.dumps(result, indent=2))
            save_record(result, OUTPUT_PATH)
    except Exception as e:
        logger.error(f"Execution Error: {e}")
