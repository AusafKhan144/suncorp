# main.py
import json
import logging
import os
import re
import time
from datetime import datetime
from typing import Any, Dict, Hashable, List, Optional
from zoneinfo import ZoneInfo
import pandas as pd
import requests
from fuzzywuzzy import fuzz, process
from requests import RequestException, Response

# ==============================================================================
# CONFIG & CONSTANTS
# ==============================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("YouiScraper")

CONSTANTS = {
    "YES_NO": {"No": "808", "Yes": "807"},
    "COVER_TYPE": {
        "Comprehensive": "COMP",
        "Third Party Fire And Theft": "LTDC",
        "Third Party Property Damage": "OTHER",
    },
    "VEHICLE_USAGE": {"Private": "12439", "Business": "10384"},
    "TRIPS_TO_WORK": {str(i): str(10723 + i) for i in range(1, 8)},
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
        "Nrma": "057",
        "Racq": "079",
        "Apia": "070",
        "Gio": "061",
        "Youi": "087",
        "Aami": "056",
        "Not Insured": None,
        "Allianz": "060",
        "Raa": "079",
        "Racv": "079",
        "Budget Direct": "058",
        "Suncorp": "083",
        "Other": "038",
        "Woolworths": "011",
        "Ract": "079",
        "Rac": "079",
        "Coles": "003",
        "Real Insurance": "059",
        "Cgu": "073",
        "Sgio": "090",
        "Shannons": "082",
        "Qbe": "086",
        "Comminsure": "065",
        "Sgic": "090",
        "Tio": "090",
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
        logger.warning(f"⚠️  Lookup list is empty. Cannot match input: '{user_input}'")
        return {}
    
    if user_input.lower() == 'mg':
        user_input = 'M.G.'

    # 1. Exact match (Fastest, O(n))
    exact_match = next(
        (
            item
            for item in lookup_list
            if item.get(parameter, "").lower() == user_input.lower()
        ),
        None,
    )
    if exact_match:
        logger.info(f"✅ Exact API Match: '{user_input}' matched '{exact_match.get(parameter)}' (Key: {exact_match.get('key')})")
        return exact_match
        
    # 2. Fuzzy match with Token Sort (Handles reordered words)
    choices = [item.get(parameter, "") for item in lookup_list]
    
    # Use token_sort_ratio to handle "45 Tfsi" vs "TFSI 45" scenarios
    result = process.extractOne(user_input, choices, scorer=fuzz.token_sort_ratio)
    
    if result:
        best_match_str, score = result[:2]
        if score >= threshold:
            # Retrieve the full object associated with the matched string
            matched_item = next(item for item in lookup_list if item.get(parameter) == best_match_str)
            logger.info(f"🔍 Fuzzy Match (Sort): Input '{user_input}' matched '{best_match_str}' (Score: {score}, Key: {matched_item.get('key')})")
            return matched_item
            
    logger.warning(f"❌ No Match Found for '{user_input}' (Threshold {threshold}). Defaulting to first item: '{lookup_list[0].get(parameter)}'")
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

# ==============================================================================
# API CLIENT
# ==============================================================================

import json
from datetime import datetime

class ResponseCapture:
    def __init__(self, filepath="captured_flow.json"):
        self.filepath = filepath
        self.entries = []
    
    def capture(self, method, url, request_body, response_status, response_body):
        self.entries.append({
            "index": len(self.entries),
            "timestamp": datetime.now().isoformat(),
            "method": method,
            "url": url,
            "request_body": request_body,
            "response_status": response_status,
            "response_body": response_body,
        })
        self.save()
    
    def save(self):
        with open(self.filepath, "w") as f:
            json.dump(self.entries, f, indent=2, default=str)

# Modify YouiApiClient.request() to capture everything:
class YouiApiClient:
    BASE_URL = "https://quotes.youi.com.au"
    
    def __init__(self, headers, proxies, cookies=None, timeout=180):
        self.session = requests.Session()
        self.session.headers.update(headers)
        self.session.proxies.update(proxies)
        if cookies:
            self.session.cookies.update(cookies)
        self.timeout = timeout
        self.capture = ResponseCapture(
            "/home/wmc/WMC/DataManipulation/Suncorp/captured_flow.json"
        )
    
    def set_auth(self, token):
        self.session.headers["Authorization"] = f"Bearer {token}"
    
    def request(self, path, method="GET", payload=None, params=None, retries=3):
        url = f"{self.BASE_URL}{path}"
        for attempt in range(retries + 1):
            try:
                resp = self.session.request(
                    method, url, json=payload, params=params,
                    timeout=self.timeout, verify=False
                )
                if resp.status_code in {429, 500, 502, 503, 504} and attempt < retries:
                    time.sleep(0.5 * (2 ** attempt))
                    continue
                resp.raise_for_status()
                
                # Parse response
                try:
                    response_body = resp.json()
                except ValueError:
                    response_body = resp.text
                
                # ✅ CAPTURE EVERYTHING
                self.capture.capture(
                    method=method,
                    url=url,
                    request_body=payload or params,
                    response_status=resp.status_code,
                    response_body=response_body,
                )
                
                return response_body
                
            except RequestException as e:
                if attempt >= retries:
                    # Capture failures too
                    self.capture.capture(
                        method=method,
                        url=url,
                        request_body=payload or params,
                        response_status="ERROR",
                        response_body=str(e),
                    )
                    raise
                time.sleep(0.5 * (2 ** attempt))
# ==============================================================================
# FLOW LOGIC
# ==============================================================================

class YouiMotorsNewFlow:
    def __init__(self, input_data: Dict[Hashable, Any]):
        self.input = input_data
        self.session_id = None
        self.flow_state_id = None
        self.address_id = None
        self.driver_id = None
        self.mid_agreed_value = 0
        self.is_taxi = False

        logger.info("=" * 60)
        logger.info(f"🚀 STARTING FLOW FOR ID: {self.input.get('SAMPLE_NUMBER')}")
        logger.info(f"📋 Input Data Summary: {json.dumps(self.input, default=str)}")
        logger.info("=" * 60)

        self.headers = {
            'User-Agent': 'Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:147.0) Gecko/20100101 Firefox/147.0',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br, zstd',
            'Content-Type': 'application/json',
            'Origin': 'https://quotes.youi.com.au',
            'Connection': 'keep-alive',
            'Sec-Fetch-Dest': 'empty',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Site': 'same-origin',
            'TE': 'trailers'
            }
        YOUR_API_KEY = "4d39f5403fa24cf6a31cf8873b3d9101"
        go_proxy = "https://customer-watchmycompetitor-country-au:DKo3FPU_qpNJ@proxy.goproxies.com:1080"
        oxy_lab_proxy = "http://customer-wmc_user_aakFe:WmcLetmein_123@pr.oxylabs.io:10002"
        zyte_data_api = f"http://{YOUR_API_KEY}:@api.zyte.com:8011/"

        self.proxies = {"http": zyte_data_api, "https": zyte_data_api}

        aest_tz = ZoneInfo("Australia/Sydney")
        now = datetime.now(aest_tz)
        self.output = {
            "SAMPLE_NUMBER":self.input.get("SAMPLE_NUMBER"),
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
            "excess500": "",
            "excess550": "",
            "excess600": "",
            "excess650": "",
            "excess700": "",
            "excess750": "",
            "excess800": "",
            "excess850": "",
            "excess900": "",
            "excess950": "",
            "excess1000": "",
            "excess1050": "",
            "excess1100": "",
            "excess1150": "",
            "excess1200": "",
            "excess1250": "",
            "excess1300": "",
            "excess1350": "",
            "excess1400": "",
            "excess1450": "",
            "excess1500": "",
            "excess1550": "",
            "excess1600": "",
            "excess1650": "",
            "excess1700": "",
            "excess1750": "",
            "excess1800": "",
            "excess1850": "",
            "excess1900": "",
            "excess1950": "",
            "excess2000": "",
            "excess2050": "",
            "excess2100": "",
            "excess2150": "",
            "excess2200": "",
            "excess2250": "",
            "excess2300": "",
            "excess2350": "",
            "excess2400": "",
            "excess2450": "",
            "excess2500": "",
            "Reason_for_quote_failure": "",
            "Remark (if any)": "",
        }

    # --- Verbose Helper Functions ---

    def _resolve_mapping(self, category: str, raw_input: Any) -> str:
        """Looks up a value in CONSTANTS and logs the deduction trace."""
        mapping = CONSTANTS.get(category, {})

        if category == "TRIPS_TO_WORK":
            lookup_key = str(safe_int(raw_input))
        elif category == "TRAVEL":
            lookup_key = str(raw_input).lower()
        else:
            lookup_key = str(raw_input).title()

        result = mapping.get(lookup_key,"NOT_FOUND")

        if result:
            logger.info(f"🧩 Mapping Deduction [{category}]: Input '{raw_input}' -> Key '{lookup_key}' -> Resolved Value '{result}'")
        else:
            logger.warning(f"⚠️ Mapping Deduction FAILED [{category}]: Input '{raw_input}' -> Key '{lookup_key}' not found in map.")

        return result

    def _log_logic(self, description: str, value: Any):
        logger.info(f"🧮 Logic Calculation: {description} = {value}")

    def _persist(self, endpoint: str, ops: List[Dict]):
        logger.info(f"📡 API Request [{endpoint}]: Applying {len(ops)} operations.")
        for op in ops:
            path = op.get('path')
            val = op.get('value')
            logger.info(f"   ↳ Operation: {op.get('op')} | Path: {path} | Value: {val}")

        payload = {
            "dataOperations": ops,
            "context": {"sessionId": self.session_id, "riskCode": "VEH"},
            "flowStateId": self.flow_state_id,
        }
        return self.client.request(
            f"/bff/api/v1/persist-flow/dcq/vehicle/quote-v2/{endpoint}",
            "PATCH",
            payload,
        )

    def _persist_v1(
        self, endpoint: str, ops: List[Dict], additional_data=None, child_data=None
    ):
        logger.info(f"📡 API Request [{endpoint}]: Applying {len(ops)} operations.")
        for op in ops:
            path = op.get('path')
            val = op.get('value')
            logger.info(f"   ↳ Operation: {op.get('op')} | Path: {path} | Value: {val}")

        payload = {
            "dataOperations": ops,
            "context": {"sessionId": self.session_id, "riskCode": "VEH"},
            "flowStateId": self.flow_state_id,
        }
        if additional_data and child_data:
            payload["additionalData"] = additional_data
            payload["childDataKey"] = child_data

        return self.client.request(
            f"/bff/api/v1/persist-flow/dcq/vehicle/quote/{endpoint}",
            "PATCH",
            payload,
        )

    def _metric(self, name: str):
        if self.flow_state_id:
            try:
                self.client.request(f"/bff/api/v1/metrics/{self.flow_state_id}/{name}", "POST")
            except:
                pass

    def _patch_data(self, step: str):
        payload = {"dataOperations": [], "context": {"sessionId": self.session_id, "riskCode": "VEH"}, "flowStateId": self.flow_state_id}
        return self.client.request(f"/bff/api/v1/data/dcq/vehicle/quote-v2/{step}", "PATCH", payload)

    def _patch_data_v1(self, step: str):
        payload = {"dataOperations": [], "context": {"sessionId": self.session_id, "riskCode": "VEH"}, "flowStateId": self.flow_state_id}
        return self.client.request(f"/bff/api/v1/data/dcq/vehicle/quote/{step}", "PATCH", payload)

    def _lookup(self, endpoint: str, params: Dict = {}, method: str = "GET", payload: Dict = {}):
        res = self.client.request(f"/bff/api/v1/lookups/{endpoint}", method, payload=payload, params=params)
        return res.get("lookupData", [])

    # --- Core Steps ---

    def get_youi_hit_id(self):
        """
        Fetch the YOUI_HIT_KEY cookie required for initializing a quote session.
        
        Returns:
            str: The YOUI_HIT_KEY value from cookies, or None if not found.
        """
        import requests

        logger.info("🔑 Fetching YOUI_HIT_KEY cookie...")

        cookies = {
            # '__spdt': 'b4697b9b9a2944dcab7a4d35bd381932',
            # 'afx_profile_brid_csid': '641a4c3eb1f341c68663c18c15c9d8148de630f487033f3',
            # '_ga_T941HWW5GL': 'GS2.1.s1770114257$o1$g1$t1770114645$j60$l0$h197731826',
        }

        headers = {
            'User-Agent': 'Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:147.0) Gecko/20100101 Firefox/147.0',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Sec-Fetch-User': '?1',
            'If-None-Match': 'W/"7B4DCA7003F1AE8F797462DE3BFE9BCEDCBE6C9C645B0F2401A08193579A5263"',
            'Priority': 'u=0, i',
        }

        try:
            logger.debug(f"📡 Making request to https://www.youi.com.au/")

            response = requests.get(
                'https://www.youi.com.au/',
                # cookies=cookies,
                headers=headers,
                proxies=self.proxies,
                verify=False,
            )

            logger.debug(f"📥 Response status code: {response.status_code}")

            if response.status_code != 200:
                logger.warning(f"⚠️ Unexpected status code: {response.status_code}")

            # Update cookies with response cookies
            response_cookies = dict(response.cookies)
            cookies.update(response_cookies)
            self.cookies = cookies

            logger.debug(f"🍪 Cookies received: {list(response_cookies.keys())}")

            # Initialize the API client with updated cookies
            self.client = YouiApiClient(self.headers, self.proxies, self.cookies)

            youi_hit_key = cookies.get("YOUI_HIT_KEY")

            if youi_hit_key:
                logger.info(f"✅ YOUI_HIT_KEY obtained: {youi_hit_key[:8]}...")
            else:
                logger.warning("⚠️ YOUI_HIT_KEY not found in cookies")
                logger.debug(f"🍪 Available cookies: {list(cookies.keys())}")

            return youi_hit_key

        except requests.RequestException as e:
            logger.error(f"❌ Failed to fetch YOUI_HIT_KEY: {e}")
            raise
        except Exception as e:
            logger.error(f"❌ Unexpected error in get_youi_hit_id: {e}", exc_info=True)
            raise

    def start_quote(self):
        logger.info("🎬 Initializing Quote Session...")
        youi_hit_key = self.get_youi_hit_id()
        payload = {'sqid': None,'webHitId': youi_hit_key,'leadSource': '','placementKey': '','promotionCode': '000','promotionCodeWord': '','featureFlagOverrides': None,'fast': None,}
        resp = self.client.request("/bff/api/v1/initialize/dcq/vehicle/quote", "POST", payload)
        self.session_id = resp.get("sessionId")
        self.flow_state_id = resp.get("flowStateId")
        if token := resp.get("authorizationToken"):
            self.client.set_auth(token)
        logger.info(f"✅ Session ID: {self.session_id}")

    def vehicle_lookup(self):
        self.client.request("/bff/api/v1/start-flow/dcq/vehicle/quote", "POST", payload={"context": {"sessionId": self.session_id}, "flowStateId": self.flow_state_id})
        self._metric("vehicle-lookup")
        i = self.input

        logger.info("🔍 Starting Vehicle Match Cascade...")
        makers = self._lookup("common/vehicle-makes")
        make = find_best_match(i["PG_GG_MAKE"], makers, "value")

        years = self._lookup("common/vehicle-years", method="POST", payload={"queryParameters": {"Make": make["key"]}})
        year = find_best_match(i["VEHICLE_YEAR"], years, "value")

        models = self._lookup("common/vehicle-models", method="POST", payload={"queryParameters": {"Make": make["key"], "Year": year["key"]}})
        model = find_best_match(i["PG_GG_FAMILY"], models, "value")

        types = self._lookup("common/vehicle-types", method="POST", payload={"queryParameters": {"Make": make["key"], "Year": year["key"], "Model": model["key"]}})
        v_type_input = f"{i['PG_GG_VARIANT']} - {i['PG_GG_SERIES']} - {i['PG_GG_BODYSTYLE']}"
        v_type = find_best_match(v_type_input, types, "value")

        styles = self._lookup("common/vehicle-styles", method="POST", payload={"queryParameters": {"Make": make["key"], "Year": year["key"], "Model": model["key"], "Type": v_type["key"]}})
        style_match_input = f"{i['PG_GG_TRANS']} - {i['PG_GG_ENGSIZE']}"
        style = find_best_match(style_match_input, styles, "value")

        descs = self._lookup("common/vehicle-descriptions", method="POST", payload={"queryParameters": {"Make": make["key"], "Year": year["key"], "Model": model["key"], "Type": v_type["key"], "Style": style["key"]}})
        description_input= i["PG_GG_ENGINE"]
        desc = find_best_match(description_input, descs, "value")

        color = find_best_match(i["COLOUR"], COLORS, "value")

        deduced_string = f"{make['value']} | {model['value']} | {year['value']} | {v_type['value']} | {style['value']} | {desc['value']} | {color['value']}"
        self.output["DeducedVehicleSelection"] = deduced_string
        logger.info(f"🚘 Deduced Vehicle Selection: {deduced_string}")

        self._persist_v1("vehicle-lookup-pre-next-task", [
            {"op": "replace", "path": "/currentStepName", "value": "vehicle-lookup"},
            {"op": "add", "path": "/vehicleLookup", "value": {
                "selectedLookupOption": "make-and-model", "showRegistrationApiError": False, "vehicleFoundByRegoSearch": "808",
                "makeNameModelSearch": make["value"], "showRegoSearchInlineLoader": False,
                "make": make["key"], "year": year["key"], "model": model["key"], "type": v_type["key"],
                "style": style["key"], "modelCode": desc["key"], "colour": color["key"]
            }}
        ])
        return self._patch_data_v1("vehicle-lookup")

    def vehicle_condition_lookup(self):
        self._metric("vehicle-condition")
        cond_list = self._lookup("common/new-used", method="POST", payload={"queryParameters": {"year": self.input["VEHICLE_YEAR"]}})
        cond = find_best_match("Used - Already Owner", cond_list, "value")
        poss_list = self._lookup("common/possession")
        poss = find_best_match("Yes", poss_list, "value")
        cov_list = self._lookup("common/covered")
        cov = find_best_match(self.input.get("PREVIOUSINSURER_COVER", ""), cov_list, "value")

        no_val = self._resolve_mapping("YES_NO", "No")

        response = self._persist_v1("vehicle-condition-pre-next-task", [
            {"op": "replace", "path": "/vehicleCondition/carAlreadyInPossession", "value": True},
            {"op": "add", "path": "/vehicleCondition/carCondition", "value": cond["key"]},
            {"op": "add", "path": "/vehicleCondition/currentVehicleLocation", "value": poss["key"]},
            {"op": "add", "path": "/vehicleCondition/currentInsuranceCover", "value": cov["key"]},
            {"op": "add", "path": "/vehicleCondition/currentExistingDamage", "value": no_val},
            {"op": "replace", "path": "/currentStepName", "value": "vehicle-condition"},
            {"op": "add", "path": "/underwriting", "value": {"readableConditionsOfCover": [], "conditionsOfCover": []}},
        ])

        minimum_agreed = 0
        maximum_agreed = 0
        for dop in response.get("dataDeltaOperations", []):
            if dop.get("path") == "/vehiclePremium":
                val = dop.get("value", {})
                minimum_agreed = val.get("minimumSumInsuredAmount", 0)
                maximum_agreed = val.get("maximumSumInsuredAmount", 0)

        self._log_logic("Agreed Value Range", f"{minimum_agreed} - {maximum_agreed}")
        if minimum_agreed and maximum_agreed:
            self.mid_agreed_value = (maximum_agreed + minimum_agreed) / 2
            self._log_logic("Mid Agreed Value", self.mid_agreed_value)

        additional_data = {
            "underwriting":{
                "activeWarningText":"This policy automatically provides cover for the Tray up to the maximum of $2500, if you require additional cover please add this value to the aftermarket accessories sum insured."
                }
            }
        child_data = "warning-dialog-form"

        response = self._persist_v1(
            "mark-active-warning-read-task",
            [],
            additional_data=additional_data,
            child_data=child_data,
        )

        response = self._persist_v1(
            "vehicle-condition-pre-next-task",
            []
        )

    def vehicle_cover_information(self):
        logger.info("🛡️ Setting Cover Information")
        self._patch_data_v1("vehicle-condition")
        self._metric("covers")

        dt = datetime(safe_int(self.input["POLICY_START_YEAR"]), safe_int(self.input["POLICY_START_MONTH"]), safe_int(self.input["POLICY_START_DAY"]))
        pol_date_str = dt.strftime("%Y-%m-%dT%H:%M:%S.000")

        cover_type_code = self._resolve_mapping("COVER_TYPE", self.input.get("COVER_TYPE"))

        self._persist_v1("cover-pre-next-task", [
            {"op": "replace", "path": "/currentStepName", "value": "covers"},
            {"op": "add", "path": "/cover/coverTypeCode", "value": cover_type_code},
            {"op": "add", "path": "/riskStartDate", "value": pol_date_str},
        ])

    def vehicle_usage(self):
        logger.info("🚦 Setting Vehicle Usage")
        self._patch_data_v1("covers")
        self._metric("vehicle-usages")

        usage_code = self._resolve_mapping("VEHICLE_USAGE", self.input.get("PG_VEHICLEUSE1"))

        if usage_code == '12439':
            days_code = self._resolve_mapping("TRIPS_TO_WORK", self.input.get("DAYSPERWEEK"))
            commute_yes = self._resolve_mapping("COMMUTING", "Yes")

            self._persist_v1("vehicle-usages-pre-next-task", [
                {"op": "add", "path": "/vehicleParking/isDaytimeParkingAtSameAddress", "value": "808"},
                {"op": "replace", "path": "/currentStepName", "value": "vehicle-usages"},
                {"op": "add", "path": "/vehicleUsage", "value": {
                    "selectedUsage": usage_code, "selectedIndustryControlKeyRequired": False,
                    "howManyDaysAWeekCommuting": days_code, "usedForCommuting": commute_yes
                }}
            ])
        else:
            type_of_business = self.input.get("PG_VEHICLEUSE2","")
            business_code = re.sub(r'[^\d+]','',type_of_business).strip()
            if not business_code:
                business_code = type_of_business.split()[0]
                self.is_taxi = True
            industries = self._lookup("common/industries",method="GET",params={"filter": business_code})
            industry = find_best_match(type_of_business, industries, "value")

            radius_list = self._lookup("common/radius-of-operations",method="GET")
            radius = find_best_match("25 Up To 50km", radius_list, "value")

            data = [
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
                        "selectedAnzSicCode": industry.get('data',{}).get('ANZSICCode'),
                        "selectedIndustryCode": industry.get('data',{}).get('industryCode'),
                        "businessDescription": industry.get('data',{}).get('industryDescription'),
                        "primaryActivities": industry.get('data',{}).get('relatedActivities'),
                        "businessTitle": industry.get('data',{}).get('industryName'),
                        "maximumDistanceTravelledFromBase": radius.get('key'),
                    },
                },
            ]

            self._persist_v1("vehicle-usages-pre-next-task",data)

    def vehicle_kilometers_driven(self):
        self._patch_data_v1("vehicle-usages")
        self._metric("kilometers-per-year")

        km_code = self._resolve_mapping("TRAVEL", self.input.get("PG_ANNUALKM"))
        self._persist_v1("kilometers-per-year-pre-next-task", [
            {"op": "replace", "path": "/currentStepName", "value": "kilometers-per-year"},
            {"op": "add", "path": "/kilometersPerYear", "value": {"selectedKilometersPerYear": km_code}},
        ])

    def vehicle_stored_address_information(self):
        self._patch_data_v1("kilometers-per-year")
        self._metric("vehicle-parking")
        self._persist_v1("vehicle-parking-pre-task", [{"op": "replace", "path": "/currentStepName", "value": "vehicle-parking"}])

        addr_str = f"{self.input['ADDRESS']}, {self.input['SUBURB']} {self.input['STATE']} {self.input['POSTCODE']}"
        sub_str = f"{self.input['SUBURB']} {self.input['POSTCODE']}"
        logger.info(f"🏠 Matching Address: '{addr_str}'")

        addr_list = self._lookup("address/one-line", params={"filter": addr_str})
        addr = find_best_match(addr_str, addr_list, "value")
        if not addr:
            raise ValueError("Address not found")

        store_list = self._lookup("common/stored-daytime")
        store = find_best_match(self.input.get("PG_GARAGE",""), store_list, "value")

        hyd_resp = self._persist_v1("hydrate-overnight-parking-address-details-task", [
            {"op": "replace", "path": "/vehicleParking/addressDetails/anotherAddressDisplayText", "value": addr["value"]},
            {"op": "replace", "path": "/vehicleParking/addressDetails/anotherAddressLookupKey", "value": addr["key"]},
        ])

        for op in hyd_resp.get("dataDeltaOperations", []):
            if op.get("path") == "/vehicleParking/addressDetails/anotherAddressDetails":
                self.address_id = op.get("value", {}).get("addressId")
        self._log_logic("Retrieved Address ID", self.address_id)

        sub_list = self._lookup("address/suburbsWithoutPoAddress", params={"filter": sub_str})  
        sub = find_best_match(sub_str, sub_list, "value")

        data = [
            {"op": "replace","path": "/vehicleParking/selectedDaytimeParkingLocationInput","value": store["key"],},
            {"op": "replace","path": "/vehicleParking/selectedNightParkingLocation","value": store["key"],},
            {"op": "add","path": "/vehicleParking/daytimeParkingSuburb","value": int(sub["key"]),},
            {"op": "add","path": "/vehicleParking/selectedDaytimeParkingLocation","value": int(store["key"]),},
            {"op": "add","path": "/vehicleParking/daytimeParkingSuburbInput","value": sub["key"],},
        ]

        self._persist_v1("vehicle-parking-pre-next-task-group", data)

    def vehicle_policy_holder_information(self):
        self._patch_data_v1("vehicle-parking")
        self._metric("policyholder")

        dt = datetime(safe_int(self.input["DOB_DUMMY_YEAR"]), safe_int(self.input["DOB_DUMMY_MONTH"]), safe_int(self.input["DOB_DUMMY_DAY"]))
        dob_str = dt.strftime("%Y-%m-%dT%H:%M:%S.000")
        gender_code = self._resolve_mapping("GENDERS", self.input.get("PG_GENDER"))

        self._persist_v1("policyholder-pre-next-task", [
            {"op": "add", "path": "/policyholder/firstName", "value": self.input["FIRST_NAME_DUMMY"]},
            {"op": "add", "path": "/policyholder/dateOfBirth", "value": dob_str},
            {"op": "add", "path": "/policyholder/gender", "value": gender_code},
            {"op": "replace", "path": "/currentStepName", "value": "policyholder"},
        ])

    def vehicle_policy_holder_license_detail(self):
        self._patch_data_v1("policyholder")
        self._metric("driving-details")

        unsup_age = safe_int(self.input["PC_AGE"]) - safe_int(self.input["PC_YEARSHELDLICENSE"])
        self._log_logic("Unsupervised Age (Age - Years Held)", unsup_age)

        lic_list = self._lookup("common/licence-types")
        lic = find_best_match(self.input.get("LICENCE_TYPE", ""), lic_list, "value")

        no_val = self._resolve_mapping("YES_NO", "No")
        demerit_val = self._resolve_mapping("DEMERITS", "0")
        mobile_val = self._resolve_mapping("MOBILE_PHONE", "Never")

        self._persist_v1("driving-details-pre-next-task", [
        { "op": "add", "path": "/drivingDetails/licenceType", "value": lic["key"] },
        { "op": "add", "path": "/drivingDetails/ageStartDriving", "value": unsup_age },
        { "op": "add", "path": "/drivingDetails/licenseSuspended", "value": no_val },
        {"op": "add","path": "/drivingDetails/demeritPointsInput","value": demerit_val},
        { "op": "add", "path": "/drivingDetails/mobilePhoneUsage", "value": mobile_val },
        { "op": "add", "path": "/drivingDetails/ownAnotherVehicle", "value": no_val },
        { "op": "replace", "path": "/currentStepName", "value": "driving-details" }
        ])

    def vehicle_incident_history(self):
        self._patch_data_v1("driving-details")
        self._metric("previous-incidents")

        incident_cause = self.input.get("INCIDENT_CAUSE1")
        logger.info(f"💥 Checking Incident History: {incident_cause}")

        if incident_cause != "No Claim":
            inc_type = self._resolve_mapping("INCIDENT_TYPES", incident_cause)
            yr = safe_int(self.input["DATE_INCIDENT_YEAR"])
            mn = safe_int(self.input["DATE_INCIDENT_MONTH"])
            yes_val = self._resolve_mapping("YES_NO", "Yes")

            self._persist_v1("incident-toggle-task", [{"op":"replace","path":"/currentStepName","value":"previous-incidents"},{"op":"add","path":"/incidents","value":{"hasAny":yes_val,"count":0,"historyItems":[]}}])
            self._persist_v1("incident-populate-name-task", [{"op": "add", "path": "/incidents/historyItems/0/incidentType", "value": inc_type}])
            self._persist_v1("incident-change-task", [
                {"op": "replace", "path": "/incidents/historyItems/0/onChange", "value": True},
                {"op": "add", "path": "/incidents/historyItems/0/incidentYearKey", "value": f"Y-{yr}"}
            ])
            self._persist_v1("incident-change-task", [
                {"op": "replace", "path": "/incidents/historyItems/0/onChange", "value": True},
                {"op": "add", "path": "/incidents/historyItems/0/headerSeparator", "value": "/"},
                {"op": "add", "path": "/incidents/historyItems/0/incidentMonthKey", "value": f"M-{str(mn).zfill(2)}"},
            ])
        else:
            no_val = self._resolve_mapping("YES_NO", "No")
            self._persist_v1("incident-toggle-task", [
                {"op": "replace", "path": "/currentStepName", "value": "previous-incidents"},
                {"op": "add", "path": "/incidents", "value": {"hasAny": no_val, "count": 0, "historyItems": []}},
            ])

            data = [{"op":"remove","path":"/incidents/historyItems"}]
            self._persist_v1("incident-check-for-staged-task", data)

        self._persist_v1("incident-history-pre-next-task", [])

    def nsw_tax_eligibility(self):
        # self._metric("nsw-input-tax-eligibility")
        # data = [{"op":"replace","path":"/currentStepName","value":"nsw-input-tax-eligibility"},{"op":"add","path":"/nswInputTaxEligibility","value":{"usedMoreThanFiftyPercentForBusiness":"808"}}]
        # self._persist_v1("nsw-input-tax-eligibility-pre-next-task",data)
        self._patch_data_v1("nsw-input-tax-eligibility")

    def policy_holder_employment_details(self):
        self._patch_data_v1("previous-incidents")
        # self.nsw_tax_eligibility()
        self._metric("employment-details")

        emp = find_best_match(self.input.get("EMPLOYMENT", ""), self._lookup("common/employment-status"), "value")

        if self.input.get("EMPLOYMENT", "") in ['Employed','Self Employed']:
            occ = find_best_match(self.input.get("OCCUPATION", ""), self._lookup("common/occupation-industry"), "value")

            self._persist_v1("employment-details-pre-next-task", [
                {"op": "replace", "path": "/employmentDetails/prepopulatedOccupationIndustry", "value": ""},
                {"op": "replace", "path": "/employmentDetails/employmentStatus", "value": emp["key"]},
                {"op": "replace", "path": "/employmentDetails/needOccupationIndustry", "value": True},
                {"op": "add", "path": "/employmentDetails/occupationIndustry", "value": occ["key"]},
            ])
        else:
            data = [
                {"op": "add","path": "/employmentDetails/employmentStatus","value": emp["key"],},
                {"op": "add","path": "/employmentDetails/prepopulatedOccupationIndustry","value": "",},
                {"op": "replace","path": "/currentStepName","value": "employment-details",},
            ]
            self._persist_v1("populate-occupation-code-task",data)
            self._persist_v1("employment-details-pre-next-task", [])

    def policy_holder_insurance_history(self):
        self._patch_data_v1("employment-details")
        self._metric("insurance-history")

        held_input = self.input.get("HELD_INS_2W", "").title()
        held_code = self._resolve_mapping("YES_NO", held_input)

        val = {}
        if held_input == "Yes":
            comp = self._resolve_mapping("INSURERS", self.input.get("PG_PREVIOUSINSURER", ""))
            dur = find_best_match(self.input.get("PC_YRSINSURED", ""), self._lookup("common/cover-duration"), "value")
            val = {
                "isCurrentlyCovered": held_code, "whichInsurerPreviousToYoui": comp,
                "howLongwithPreviousInsurer": dur["key"], "previousInsuranceStatus": False,
            }
        else:
            no_cov = find_best_match('Never Been Covered', self._lookup("common/no-cover-duration"), "value")
            val = {"isCurrentlyCovered": held_code, "howLongWithoutInsurance": no_cov["key"]}

        self._persist_v1("insurance-history-pre-next-task", [{"op": "add", "path": "/insuranceHistory", "value": val}])

    def vehicle_modification_details(self):
        self._patch_data_v1("insurance-history")
        self._metric("modifications-and-accessories")

        no_val = self._resolve_mapping("YES_NO", "No")
 
        data = [
            {"op": "remove","path": "/modifications/factoryFittedOptionalAccessoryOptions",},
            {"op": "remove", "path": "/modifications/factoryFittedListItemElements"},
            {"op": "remove", "path": "/modifications/modelSummary"},
            {"op": "remove","path": "/modifications/factoryOptionalAccessoriesAvailable",},
            {"op": "remove","path": "/modifications/factoryOptionalAccessoriesMoreThanTen",},
            {"op": "remove","path": "/modifications/hasFactoryFittedOptionalAccessoriesAndAccessoriesListLessThanTen",},
            {"op": "remove","path": "/modifications/showOptionalFactoryFittedAccessoriesList",},
            {"op": "remove","path": "/modifications/hasOptionalFactoryFittedAccessoriesList",},
            {"op": "remove", "path": "/modifications/showFactoryFittedAccessoriesList"},
            {"op": "remove", "path": "/modifications/hasFactoryFittedAccessoriesList"},
            {"op": "remove","path": "/modifications/showFactoryFittedAccessoriesQuestion",},
            {"op": "add", "path": "/modifications/hasAnyModifications", "value": no_val},
            {"op": "add","path": "/modifications/selectedModificationsDescriptionList","value": [],},
            {"op": "add", "path": "/modifications/modificationElements", "value": []},
            {"op": "add","path": "/modifications/hasAnyAftermarketAccessories","value": no_val,},
            {"op": "replace","path": "/currentStepName","value": "modifications-and-accessories",},
        ]

        self._persist_v1("modifications-pre-next-task",data)


        # self._persist_v1("populate-selected-modification-list-task", [
        #     {"op": "add", "path": "/modifications/modificationElements", "value": []},
        #     {"op": "replace", "path": "/currentStepName", "value": "modifications-and-accessories"},
        # ])
        # self._persist_v1("modifications-pre-next-task", [
        #     {"op":"add","path":"/modifications/selectedFactoryFittedOptionalAccessoryOptions","value":[]},
        #     {"op":"add","path":"/modifications/hasAnyFactoryFittedAccessories","value":no_val},
        #     {"op": "add", "path": "/modifications/hasAnyModifications", "value": no_val},
        #     {"op": "add", "path": "/modifications/selectedModificationsDescriptionList", "value": []},
        #     {"op": "add", "path": "/modifications/hasAnyAftermarketAccessories", "value": no_val},
        # ])

    def policy_holder_additional_details(self):
        self._patch_data_v1("modifications-and-accessories")
        self._metric("additional-policyholder-details")
        yes_val = self._resolve_mapping("YES_NO", "Yes")

        self._persist_v1("additional-policyholder-details-create-policyholder-task", [
            {"op": "add", "path": "/additionalPolicyholderDetails/addressDetails/useExistingAddress", "value": self.address_id},
            {"op": "add", "path": "/additionalPolicyholderDetails/willDriveTheCar", "value": yes_val},
            {"op": "add", "path": "/additionalPolicyholderDetails/firstName", "value": self.input.get("FIRST_NAME_DUMMY")},
            {"op": "add", "path": "/additionalPolicyholderDetails/lastName", "value": self.input.get("LAST_NAME_DUMMY")},
            {"op": "add", "path": "/drivingDetails/driverType", "value": "ListedDriver"},
            {"op": "replace", "path": "/currentStepName", "value": "additional-policyholder-details"},
        ])

    def is_add_another_driver(self):
        # self._persist_v1("retrieve-policyholder-id-task", [])
        self._persist_v1("additional-policyholder-details-pre-next-task", [])
        self._patch_data_v1("additional-policyholder-details")
        self._metric("drivers-list")
        self._persist_v1("build-listed-drivers-task", [
            {"op": "replace", "path": "/currentStepName", "value": "drivers-list"},
            {"op": "add", "path": "/driverSummary", "value": {
                "hasRegularDriver": False, "numberOfRegularDriversToNominate": 0, "policyholderDisplayList": [],
                "regularDriverOptions": [], "isRegularDriverMustBeExplicitlyNominated": False, "showListedAnotherDriver": False,
                "hasAvailableAnotherDrivers": False, "listAnotherDriverOptions": [],
            }}
        ])

        driver_needed_input = self.input.get("SECOND_DRIVER_FLAG", "")
        needed_val = self._resolve_mapping("YES_NO", driver_needed_input)

        if str(driver_needed_input).title() == "Yes":
            self._persist_v1("set-edit-listed-driver-task", [
                {"op": "remove", "path": "/driverSummary/selectedAnotherDriver"},
                {"op": "remove", "path": "/driverSummary/listedDriversDisplayList/0/dateOfBirth"},
                {"op": "remove", "path": "/driverSummary/policyholderDisplayList/0/driverNameSuffix"},
                {"op": "remove", "path": "/driverSummary/policyholderDisplayList/0/hasRegularDriverWhenThisOneRemoved"},
                {"op": "remove", "path": "/driverSummary/policyholderDisplayList/0/hasOtherDriversWhenThisOneRemoved"},
                {"op": "remove", "path": "/driverSummary/policyholderDisplayList/0/dateOfBirth"},
                {"op": "add", "path": "/driverSummary/addAnotherDriverQuestion", "value": needed_val},
            ])
            self._persist_v1("driver-details-pre-task", [{"op": "remove", "path": "/driverSummary/selectedAnotherDriver"}])
            self._metric("driver-details")
            self._add_second_driver(needed_val)

        else:
            self._persist_v1("set-regular-driver-task",[
                {"op": "remove", "path": "/driverSummary/selectedAnotherDriver"},
                {"op": "remove","path": "/driverSummary/listedDriversDisplayList/0/dateOfBirth",},
                {"op": "remove","path": "/driverSummary/policyholderDisplayList/0/driverNameSuffix",},
                {"op": "remove","path": "/driverSummary/policyholderDisplayList/0/hasRegularDriverWhenThisOneRemoved",},
                {"op": "remove","path": "/driverSummary/policyholderDisplayList/0/hasOtherDriversWhenThisOneRemoved",},
                {"op": "remove","path": "/driverSummary/policyholderDisplayList/0/dateOfBirth",},
                {"op": "add","path": "/driverSummary/addAnotherDriverQuestion","value":needed_val},
            ])

    def _add_second_driver(self, needed_val: str):
        logger.info("➕ Adding Second Driver Details")
        i = self.input
        dob = datetime(safe_int(i["DOB_DUMMY_YEAR2"]), safe_int(i["DOB_DUMMY_MONTH2"]), safe_int(i["DOB_DUMMY_DAY2"])).strftime("%Y-%m-%dT%H:%M:%S.000")
        unsup = safe_int(i["PC_AGE2"]) - safe_int(i["PC_YEARSHELDLICENSE2"])

        lic = find_best_match(i["LICENCE_TYPE2"], self._lookup("common/licence-types"), "value")
        emp = find_best_match(i["EMPLOYMENT2"], self._lookup("common/employment-status"), "value")
        occ = find_best_match(i["OCCUPATION2"], self._lookup("common/occupation-industry"), "value")

        no_val = self._resolve_mapping("YES_NO", "No")
        demerit_val = self._resolve_mapping("DEMERITS", "0")
        mobile_val = self._resolve_mapping("MOBILE_PHONE", "Never")
        gender_val = self._resolve_mapping("GENDERS", i["PG_GENDER2"])

        self._persist_v1(
            "calculate-driver-age-task",
            [
                {"op": "add","path": "/editListedDriver/employmentDetails/employmentStatus","value": None,},
                {"op": "add","path": "/editListedDriver/drivingDetails/driverType","value": "ListedDriver",},
                {"op": "add","path": "/editListedDriver/addressDetails/useExistingAddress","value": self.address_id,},
                {"op": "add", "path": "/editListedDriver/dateOfBirth", "value": dob},
                {"op": "add", "path": "/editListedDriver/gender", "value": gender_val},
                {"op": "add","path": "/driverSummary/selectedAnotherDriver","value": needed_val,},
                {"op": "add", "path": "/underwriting/knockoutsFound", "value": False},
                {"op": "replace","path": "/currentStepName","value": "driver-details",},
            ],
        )
        self._persist_v1(
            "incident-toggle-task",
            [
                {"op": "add","path": "/editListedDriver/incidentHistory/hasAny","value": no_val,},
                {"op": "add", "path": "/editListedDriver/firstName", "value": i["FIRST_NAME_DUMMY2"]},
                {"op": "add", "path": "/editListedDriver/lastName", "value": i["LAST_NAME_DUMMY2"]},
            ],
        )
        self._persist_v1("incident-check-for-staged-task", [
            {"op": "replace", "path": "/editListedDriver/employmentDetails/needOccupationIndustry", "value": True},
            {"op": "add", "path": "/editListedDriver/employmentDetails/employmentStatus", "value": emp["key"]},
            {"op": "add", "path": "/editListedDriver/employmentDetails/occupationIndustry", "value": occ["key"]},
            {"op": "add", "path": "/editListedDriver/employmentDetails/prepopulatedOccupationIndustry", "value": ""},
            {"op": "add", "path": "/editListedDriver/drivingDetails/licenceType", "value": lic["key"]},
            {"op": "add", "path": "/editListedDriver/drivingDetails/demeritPointsInput", "value": demerit_val},
            {"op": "add", "path": "/editListedDriver/drivingDetails/mobilePhoneUsage", "value": mobile_val},
            {"op": "add", "path": "/editListedDriver/ageStartDriving", "value": unsup},
            {"op": "add", "path": "/editListedDriver/licenseSuspended", "value": no_val},
            {"op": "add", "path": "/editListedDriver/ownAnotherVehicle", "value": no_val},
        ])
        self._persist_v1("check-duplicate-person-task", [])
        self._persist_v1("save-listed-driver-task", [])

        jo = self._persist_v1("build-listed-drivers-task", [{"op": "replace", "path": "/currentStepName", "value": "drivers-list"}])
        target = i["FULL_NAME_DUMMY"].lower()
        for d in jo.get("dataDeltaOperations", []):
            if d.get("path") == "/driverSummary/regularDriverOptions":
                for v in d.get("value", []):
                    if v.get("value", "").lower() == target:
                        self.driver_id = v.get("key")
                        break

        self._persist_v1("set-regular-driver-task", [
            {"op": "remove", "path": "/driverSummary/selectedAnotherDriver"},
            {"op": "replace", "path": "/driverSummary/addAnotherDriverQuestion", "value": no_val},
            {"op": "replace", "path": "/driverSummary/isRegularDriverMustBeExplicitlyNominated", "value": True},
            {"op": "remove", "path": "/driverSummary/listedDriversDisplayList/1/dateOfBirth"},
            {"op": "remove", "path": "/driverSummary/listedDriversDisplayList/0/dateOfBirth"},
            {"op": "remove", "path": "/driverSummary/policyholderDisplayList/0/hasOtherDriversWhenThisOneRemoved"},
            {"op": "add", "path": "/driverSummary/nominatedRegularDriverId", "value": self.driver_id},
        ])

    def duty_of_disclosure(self):
        self._persist_v1("save-regular-driver-task", [])
        self._metric("duty-of-disclosure")
        no_val = self._resolve_mapping("YES_NO", "No")

        self._persist_v1("cancellation-add-task", [
            {"op": "replace", "path": "/currentStepName", "value": "duty-of-disclosure"},
            {"op": "add", "path": "/dutyOfDisclosure", "value": {"hasAnyPreviousCancellations": no_val}},
        ])
        return self._persist_v1("duty-of-disclosure-pre-next-task", [
            {"op": "add", "path": "/dutyOfDisclosure/hasAnyCriminalConvictions", "value": no_val}
        ])

    def switch_flow(self):
        self._persist_v1("final-switch-flow-decision-task",[
            {"op":"replace","path":"/leadGenerationStatus","value":"NotApplicable"},
            {"op":"replace","path":"/hasClientMadeSwitchFlowDecision","value":True},
            {"op":"replace","path":"/switchFlowDecision","value":"ContinueOnline"}
        ])

    def extract_premiums(self):
        logger.info("💰 Extracting Premiums...")
        annual_resp = self._patch_data_v1("duty-of-disclosure")
        self._metric("vehicle-premium")

        def _get_val(resp, path):
            for op in resp.get("dataDeltaOperations", []):
                if op.get("path") == path: return op.get("value")
            return None
        def _to_float(d, c):
            raw_d = str(d or '0').replace(',', '')
            raw_c = str(c or '0')
            return float(f"{raw_d}.{raw_c}")

        a_dollars = _get_val(annual_resp, "/vehiclePremium/premiumDollars")
        a_cents = _get_val(annual_resp, "/vehiclePremium/premiumCents")
        quote_num = (_get_val(annual_resp, "/vehiclePremium/displayedQuoteNumber") or "").split(" ")[-1].lstrip("#").strip()
        def_excess = _get_val(annual_resp, "/vehiclePremium/defaultExcess") or "N/A"
        excess_list = _get_val(annual_resp, "/vehiclePremium/excessList") or []

        annual_pay = _to_float(a_dollars, a_cents)
        logger.info(f"💵 Extracted Annual: {annual_pay} | Quote: {quote_num} | Excess: {def_excess}")

        self.output.update({
            "QuoteNumber": quote_num, "QuoteLink": f"https://quotes.youi.com.au/dcq/vehicle/v2/{self.session_id}/premium",
            "DefaultExcess": def_excess, "YearlyPayment": annual_pay, "MiddleAgreedValueSI": self.mid_agreed_value,
        })

        for exc in excess_list:
            self._update_premium_ops(exc)
            exc_resp = self._persist_v1("recalc-premium-task", [])
            ed = _get_val(exc_resp, "/vehiclePremium/premiumDollars")
            ec = _get_val(exc_resp, "/vehiclePremium/premiumCents")
            self.output[f"excess{int(exc)}"] = _to_float(ed, ec)

        # Window
        logger.info("💵 Calculating Window Glass...")
        ops = [
            { "op": "remove", "path": "/vehiclePremium/checkedSpecialConditions" },
            {
                "op": "replace",
                "path": "/vehiclePremium/optionalCoverOptions/0/isSelected",
                "value": True
            },
            {
                "op": "add",
                "path": "/underwriting/hasReferableKnockouts",
                "value": False
            },
            {
                "op": "add",
                "path": "/vehicleLookup/foundVehicle",
                "value": { "yearOfManufacture": 0 }
            },
            { "op": "replace", "path": "/currentStepName", "value": "vehicle-premium" }
            ]
        self._update_premium_ops(def_excess, ops)
        win_resp = self._persist_v1("recalc-premium-task", [])
        wd = _get_val(win_resp, "/vehiclePremium/premiumDollars")
        wc = _get_val(win_resp, "/vehiclePremium/premiumCents")
        self.output["WindowGlassExcess"] = _to_float(wd, wc)

        # Hire Car
        ops_hc = [
            {"op": "replace", "path": "/vehiclePremium/optionalCoverOptions/1/isSelected", "value": True},
            {"op": "replace", "path": "/vehiclePremium/optionalCoverOptions/0/isSelected", "value": False},
        ]
        self._update_premium_ops(None, ops_hc, keep_excess=True)
        hc_resp = self._persist_v1("recalc-premium-task", [])
        hd = _get_val(hc_resp, "/vehiclePremium/premiumDollars")
        hc = _get_val(hc_resp, "/vehiclePremium/premiumCents")

        if self.is_taxi:
            logger.info("💵 Calculating Business Items...")
            self.output["BusinessItemsCover"] = _to_float(hd, hc)
            ops = [{"op": "replace", "path": "/vehiclePremium/optionalCoverOptions/1/isSelected", "value": False}]
            self._update_premium_ops(None, ops, keep_excess=True)
            hc_resp = self._persist_v1("recalc-premium-task", [])

        else:
            logger.info("💵 Calculating Hire Car...")
            self.output["HireCar"] = _to_float(hd, hc)

        # Business
        if not self.is_taxi:
            logger.info("💵 Calculating Business Items...")
            ops_bi = [
                {"op": "replace", "path": "/vehiclePremium/optionalCoverOptions/2/isSelected", "value": True},
                {"op": "replace", "path": "/vehiclePremium/optionalCoverOptions/1/isSelected", "value": False},
            ]
            self._update_premium_ops(None, ops_bi, keep_excess=True)
            bi_resp = self._persist_v1("recalc-premium-task", [])
            bd = _get_val(bi_resp, "/vehiclePremium/premiumDollars")
            bc = _get_val(bi_resp, "/vehiclePremium/premiumCents")
            self.output["BusinessItemsCover"] = _to_float(bd, bc)

        # Monthly
        logger.info("💵 Calculating Monthly...")
        if not self.is_taxi:
            ops_mo = [
                {"op": "replace", "path": "/vehiclePremium/paymentFrequencyAbbreviated", "value": "P/-"},
                {"op": "replace", "path": "/vehiclePremium/paymentFrequency", "value": "Monthly"},
                {"op": "replace", "path": "/vehiclePremium/optionalCoverOptions/2/isSelected", "value": False},
            ]
        else:
            ops_mo = [
                {"op": "replace", "path": "/vehiclePremium/paymentFrequencyAbbreviated", "value": "P/-"},
                {"op": "replace", "path": "/vehiclePremium/paymentFrequency", "value": "Monthly"},
                ]

        bkup_mo_resp = self._update_premium_ops(None, ops_mo, endpoint="set-payment-frequency-and-recalc-premium", keep_excess=True)
        mo_resp = self._persist_v1("recalc-premium-task", [])
        md = _get_val(mo_resp, "/vehiclePremium/premiumDollars") or _get_val(bkup_mo_resp,"/vehiclePremium/premiumDollars")
        mc = _get_val(mo_resp, "/vehiclePremium/premiumCents") or _get_val(bkup_mo_resp,"/vehiclePremium/premiumCents")
        self.output["MonthlyPayment"] = _to_float(md, mc)

        # Agreed
        if self.mid_agreed_value > 0:
            logger.info(f"💵 Calculating Agreed Value ({self.mid_agreed_value})...")
            agreed_ops = [
                { "op": "remove","path": "/modifications/aftermarketAccessories/sumInsuredAmount"},
                { "op": "remove","path": "/vehiclePremium/premiumInstalmentsDisplayText" },
                { "op": "remove","path": "/vehiclePremium/monthlyPremium" },
                { "op": "remove","path": "/vehiclePremium/checkedSpecialConditions" },
                { "op": "remove","path": "/vehiclePremium/annualGrossPremium" },
                { "op": "replace","path": "/vehiclePremium/insuredValueInput","value": "6343"},
                { "op": "replace","path": "/vehiclePremium/premiumCents", "value": "--" },
                { "op": "replace","path": "/vehiclePremium/premiumDollars", "value": "___" },
                { "op": "replace","path": "/vehiclePremium/paymentFrequencyAbbreviated", "value": "P/-"},
                { "op": "replace","path": "/vehiclePremium/paymentFrequency","value": "Annual"},
                { "op": "replace","path": "/vehiclePremium/coverOptionsRecalcRequired","value": True},
                { "op": "replace", "path": "/vehiclePremium/isInReCalcMode", "value": True },
                { "op": "replace", "path": "/vehiclePremium/insuredValue", "value": 6343 },
                { "op": "add", "path": "/vehiclePremium/agreedValue", "value": self.mid_agreed_value }
            ]

            try:
                # self._update_premium_ops(None, agreed_ops)
                agreed_resp = self._persist_v1("set-payment-frequency-and-recalc-premium", agreed_ops)
                ad = _get_val(agreed_resp, "/vehiclePremium/premiumDollars")
                ac = _get_val(agreed_resp, "/vehiclePremium/premiumCents")
                self.output["AgreedValue"] = _to_float(ad, ac)
            except Exception as e:
                print(e)
                self.output["AgreedValue"] = ""
                self.output["MiddleAgreedValueSI"] = ""

    def _update_premium_ops(self, excess, extra_ops=None, endpoint="update-premium-details", keep_excess=False):
        ops = [
            {"op": "remove", "path": "/vehiclePremium/annualGrossPremium"},
            {"op": "replace", "path": "/vehiclePremium/premiumCents", "value": "--"},
            {"op": "replace", "path": "/vehiclePremium/premiumDollars", "value": "___"},
            {"op": "replace", "path": "/vehiclePremium/isInReCalcMode", "value": True},
        ]
        if excess:
            ops.extend([
                {"op": "replace", "path": "/vehiclePremium/selectedExcessInput", "value": excess},
                {"op": "replace", "path": "/vehiclePremium/selectedExcess", "value": excess},
            ])
        if extra_ops:
            ops.extend(extra_ops)
        return self._persist_v1(endpoint, ops)

    def run_flow(self) -> Dict[str, Any]:
        try:
            self.start_quote()
            self.vehicle_lookup()
            self.vehicle_condition_lookup()
            self.vehicle_cover_information()
            self.vehicle_usage()
            self.vehicle_kilometers_driven()
            self.vehicle_stored_address_information()
            self.vehicle_policy_holder_information()
            self.vehicle_policy_holder_license_detail()
            self.vehicle_incident_history()
            self.policy_holder_employment_details()
            self.switch_flow()
            self.policy_holder_insurance_history()
            self.vehicle_modification_details()
            self.policy_holder_additional_details()
            self.is_add_another_driver()
            self.duty_of_disclosure()
            self.extract_premiums()
            logger.info(f"✅ Quote {self.output['QuoteNumber']} completed successfully.")
        except Exception as e:
            logger.error(f"❌ Flow failed: {e}", exc_info=True)
            self.output["Reason_for_quote_failure"] = str(e)
        return self.output


def sort_columns_with_excess(columns: list[str]) -> list[str]:
    """
    Sort columns with excess values numerically.
    
    Groups:
    1. Fixed prefix columns (SAMPLE_NUMBER, Postcode, DOB_*, QuoteDate, etc.)
    2. Quote data columns (QuoteLink, QuoteNumber, DefaultExcess, payments, etc.)
    3. Excess columns sorted numerically (excess500, excess550, ..., excess10000)
    4. Suffix columns (Reason_for_quote_failure, Remark)
    """
    # Define column order groups
    prefix_cols = [
        "SAMPLE_NUMBER", "Postcode", "DOB_day", "DOB_month", "DOB_year",
        "QuoteDate", "Timestamp AEST (in hh:mm)"
    ]
    
    quote_data_cols = [
        "DeducedVehicleSelection", "QuoteLink", "QuoteNumber", "DefaultExcess",
        "MonthlyPayment", "YearlyPayment", "RoadsideAssist", "HireCar",
        "WindowGlassExcess", "BusinessItemsCover", "AgreedValue", "MiddleAgreedValueSI"
    ]
    
    suffix_cols = [
        "Reason_for_quote_failure", "Remark (if any)"
    ]
    
    # Extract excess columns and sort numerically
    excess_pattern = re.compile(r'^excess(\d+)$', re.IGNORECASE)
    excess_cols = []
    other_cols = []
    
    for col in columns:
        match = excess_pattern.match(col)
        if match:
            excess_cols.append((int(match.group(1)), col))
        elif col not in prefix_cols + quote_data_cols + suffix_cols:
            other_cols.append(col)
    
    # Sort excess columns by numeric value
    excess_cols.sort(key=lambda x: x[0])
    sorted_excess = [col for _, col in excess_cols]
    
    # Build final column order (only include columns that exist)
    final_order = []
    
    for col in prefix_cols:
        if col in columns:
            final_order.append(col)
    
    for col in quote_data_cols:
        if col in columns:
            final_order.append(col)
    
    # Add sorted excess columns
    final_order.extend(sorted_excess)
    
    # Add any other columns not in predefined groups
    for col in other_cols:
        if col not in final_order:
            final_order.append(col)
    
    for col in suffix_cols:
        if col in columns:
            final_order.append(col)
    
    return final_order


def save_record(record: dict):
    if not record:
        return
    
    new_df = pd.DataFrame([record])
    new_cols = set(new_df.columns)

    output_csv_path = "/home/wmc/WMC/DataManipulation/Suncorp/Youi/outputs/0426/Motor_YOUI_quarterly_202605_test.csv"

    existing_df = pd.read_csv(output_csv_path)
    current_headers = sort_columns_with_excess(list(existing_df.columns))
    existing_cols = set(current_headers)
    
    schema_changed = (not current_headers) or bool(new_cols - existing_cols)
    
    if schema_changed:
        logger.info(f"Schema update needed. New cols: {new_cols - existing_cols}")
        
        if os.path.exists(output_csv_path) and os.path.getsize(output_csv_path) > 0:
            
            combined_df = pd.concat([existing_df, new_df], ignore_index=True)
            
            # Sort columns with excess numerically
            sorted_cols = sort_columns_with_excess(list(combined_df.columns))
            combined_df = combined_df[sorted_cols]
            
            combined_df.to_csv(output_csv_path, index=False)
            current_headers = sorted_cols
        else:
            # Sort columns for new file
            sorted_cols = sort_columns_with_excess(list(new_df.columns))
            new_df = new_df[sorted_cols]
            
            new_df.to_csv(output_csv_path, index=False)
            current_headers = sorted_cols
    else:
        # Reindex to match current headers (already sorted)
        new_df = new_df.reindex(columns=current_headers)
        new_df.to_csv(output_csv_path, mode="a", header=False, index=False)


if __name__ == "__main__":
    try:
        data_list = pd.read_csv("/home/wmc/WMC/DataManipulation/Suncorp/Youi/inputs/0426/data/Motor_YOUI_quarterly_202605.csv").to_dict(orient="records")
        for data in data_list:
            if data.get("SAMPLE_NUMBER") not in {70606.2025}:
                continue

            input_data = format_values(data)
            flow = YouiMotorsNewFlow(input_data)
            result = flow.run_flow()
            print(json.dumps(result, indent=2))
            save_record(result)
    except Exception as e:
        logger.error(f"Execution Error: {e}")
